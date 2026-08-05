"""Multi-day options backtest engine — powers /backtest-lab (StockMock-style).

Loops trading days over a date range; per day resolves each leg's strike (ATM±offset at
entry), pulls the held-strike 1-min premium series, simulates intraday with per-leg SL/
target/trail + strategy SL/target + square-off-one/all, books P&L (NET of real Zerodha
charges), and records the minute-by-minute combined MTM for the per-day intraday graph.

DATA (Rule 6B — reuse, no new pipeline):
  • historical  -> OptChainLake_1m offset files (opt_whatif._lake_root/_ofn), 2021->~Jul'26,
                   NIFTY+BANKNIFTY, weekly, real premium, ATM±10 strikes.
  • recent      -> live collector CSVs (option_curves._load_rows), real premium+greeks.
Caveats (surfaced in the UI): ATM±10 strikes only; illiquid deep strikes ~4% uncertain;
weekly expiry; spot-ATM only (no futures data); data from 2021 (not 2017).

Display-only / research — no order path, no live risk. Pure computation on disk data.
"""
import os
import datetime as _dt
import pandas as pd
import numpy as np

import os as _os
import sys as _sys
import opt_whatif as _w          # _lake_root / _ofn / _lake_series / STEP / LOT
import option_curves as _oc      # _load_rows (collector)

# scratch/nifty_trend has the date-aware Zerodha charges model (same as opt_whatif does)
_ntd = _os.path.join(getattr(_w, "PROJECT", ""), "scratch", "nifty_trend")
if _ntd and _os.path.isdir(_ntd) and _ntd not in _sys.path:
    _sys.path.insert(0, _ntd)
try:
    import charges as _ch
except Exception:
    _ch = None

STEP = {"NIFTY": 50, "BANKNIFTY": 100}
IST = 19800
DAY = 86400

# Date-aware lot size (StockMock schedule — "L till <date>, ..., last after that")
_LOT_SCHED = {
    "NIFTY":     [("2021-07-22", 75), ("2024-04-25", 50), ("2025-12-26", 25), ("2025-12-30", 75), ("9999-12-31", 65)],
    "BANKNIFTY": [("2018-10-25", 40), ("2020-07-22", 20), ("2023-07-20", 25), ("2025-01-29", 15),
                  ("2025-06-26", 30), ("2025-12-30", 35), ("9999-12-31", 30)],
}


def lot_for(u, date):
    for cut, l in _LOT_SCHED.get(u, [("9999-12-31", 1)]):
        if date <= cut:
            return l
    return _LOT_SCHED[u][-1][1]


def _hm_to_mod(hm):
    h, m = str(hm)[:5].split(":")[:2]
    return int(h) * 60 + int(m)


_EPOCH = _dt.date(1970, 1, 1)


def _di_to_date(di):
    # di = IST day ordinal (matches data's (ts+IST)//DAY). Ordinal → calendar date.
    return (_EPOCH + _dt.timedelta(days=int(di))).isoformat()


def _date_to_di(date):
    return (_dt.datetime.strptime(date, "%Y-%m-%d").date() - _EPOCH).days


# ---------------------------------------------------------------- lake bulk load
def _lake_frame(u, ot, off_lo, off_hi):
    """Concat the lake offset files [off_lo..off_hi] for opt `ot` once → indexed DataFrame.
    Lake WEEK folder = the nearest weekly rolling, so per day it's a SINGLE expiry (no expiry
    filter needed). `opt` column tags CE vs PE so a CE+PE strategy never cross-matches a strike."""
    root = _w._lake_root(u)
    if not root:
        return None
    frames = []
    for off in range(off_lo, off_hi + 1):
        p = os.path.join(root, _w._ofn(ot, off))
        if os.path.exists(p):
            try:
                frames.append(pd.read_csv(p, usecols=["timestamp", "close", "strike", "spot"]))
            except Exception:
                pass
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).dropna(subset=["close", "strike"])
    istp = df["timestamp"].values.astype("int64") + IST
    df["di"] = (istp // DAY)
    df["mod"] = ((istp % DAY) // 60).astype("int32")
    df["strike"] = df["strike"].astype("int32")
    df["opt"] = ot
    return df


def _collector_day(u, date):
    """Recent (collector) day → DataFrame[mod, strike, opt, expiry, close, spot] (or None).
    KEEPS expiry — the collector stores MULTIPLE expiries per strike (a 55700 CE weekly ≈ ₹814
    but the monthly ≈ ₹2816); the sim must pin the nearest weekly or it reads the wrong contract."""
    _, rows = _oc._load_rows(u, date)
    if not rows:
        return None
    recs = []
    for r in rows:
        k = r.get("strike"); ot = r.get("opt_type"); lt = r.get("ltp"); sp = r.get("spot")
        ex = r.get("expiry"); d = (r.get("datetime") or "")
        if not k or ot not in ("CE", "PE") or lt in (None, "") or len(d) < 16:
            continue
        recs.append((int(d[11:13]) * 60 + int(d[14:16]), int(float(k)), ot, str(ex or ""),
                     float(lt), float(sp) if sp not in (None, "") else np.nan))
    if not recs:
        return None
    return pd.DataFrame(recs, columns=["mod", "strike", "opt", "expiry", "close", "spot"])


def _ffill(series_map, grid):
    """Forward-fill a {mod: val} map onto a sorted minute grid → list aligned with grid."""
    ks = sorted(series_map)
    out = []
    last = None
    j = 0
    for m in grid:
        while j < len(ks) and ks[j] <= m:
            last = series_map[ks[j]]; j += 1
        out.append(last)
    return out


# ---------------------------------------------------------------- exit helpers
def _px_level(kind, val, entry, side, lot):
    """Return the premium level at which a per-leg ₹/pts/% SL or target triggers.
    kind: 'rs' (₹ per lot), 'pts' (premium points), 'pct' (% of entry premium)."""
    if kind == "pts":
        d = val
    elif kind == "pct":
        d = entry * val / 100.0
    else:  # rs per lot → points = ₹ / lot
        d = val / max(1, lot)
    return d  # points of adverse/favourable move


# ---------------------------------------------------------------- one day
def _sim_day(u, dd, date, legs, m_entry, m_exit, strat_sl, strat_tp, sqoff, lot, use_lake):
    """`dd` = this day's rows only (lake subset OR collector day). One expiry for lake;
    for collector we pin the nearest weekly."""
    if dd is None or len(dd) == 0:
        return None
    exp = None
    if not use_lake:
        exps = sorted(set(dd["expiry"].tolist()))
        exp = exps[0] if exps else None   # nearest weekly (collector stores this + next)
    # spot map (once) — index spot is the same across strikes/expiries
    sp = dd[["mod", "spot"]].dropna()
    spot_map = {}
    for mo, s in zip(sp["mod"].values.tolist(), sp["spot"].values.tolist()):
        spot_map.setdefault(int(mo), s)   # first per minute
    if not spot_map:
        return None
    spot_e = None
    for m in sorted(spot_map):
        if m >= m_entry:
            spot_e = spot_map[m]; break
    if not spot_e:
        return None
    step = STEP.get(u, 50)
    atm = round(spot_e / step) * step
    grid = list(range(m_entry, m_exit + 1))
    src = "lake" if use_lake else "collector"
    L = []
    for lg in legs:
        strike = int(atm + lg["off"])
        ot = lg["opt"]
        if use_lake:
            g = dd[(dd["opt"] == ot) & (dd["strike"] == strike) & (dd["mod"] >= m_entry) & (dd["mod"] <= m_exit + 1)]
        else:
            g = dd[(dd["opt"] == ot) & (dd["strike"] == strike) & (dd["expiry"] == exp)
                   & (dd["mod"] >= m_entry) & (dd["mod"] <= m_exit + 1)]
        smap = {}
        for mo, cl in zip(g["mod"].values.tolist(), g["close"].values.tolist()):
            smap.setdefault(int(mo), float(cl))
        if not smap:
            return None
        ser = _ffill(smap, grid)
        entry = next((v for v in ser if v is not None), None)
        if entry is None:
            return None
        L.append({**lg, "strike": strike, "ot": ot, "entry": float(entry), "ser": ser,
                  "qty": lot * int(lg.get("lots") or 1), "src": src,
                  "open": True, "exitp": None, "peak": 0.0})
    # per-minute simulation
    intr = []
    spot_ser = _ffill(spot_map, grid)
    ended = False
    for i, m in enumerate(grid):
        comb = 0.0
        for leg in L:
            cur = leg["ser"][i]
            if cur is None:
                cur = leg["entry"]
            sgn = 1.0 if leg["side"] == "SELL" else -1.0
            pnl = sgn * (leg["entry"] - cur) * leg["qty"]
            if leg["open"]:
                leg["_cur"] = cur; leg["_pnl"] = pnl
                leg["peak"] = max(leg["peak"], pnl)
            comb += pnl if leg["open"] else leg["_bookpnl"]
        _sp = spot_ser[i]
        intr.append([m, round(comb, 1), round(_sp, 1) if (_sp is not None and _sp == _sp) else None])
        if ended:
            continue
        # per-leg exits
        for leg in L:
            if not leg["open"]:
                continue
            hit = None
            sgn = 1.0 if leg["side"] == "SELL" else -1.0
            # SL: leg pnl <= -sl_rs ; Target: leg pnl >= tp_rs (per lot × lots)
            if leg.get("sl_rs") and leg["_pnl"] <= -leg["sl_rs"] * int(leg.get("lots") or 1):
                hit = "SL"
            elif leg.get("tp_rs") and leg["_pnl"] >= leg["tp_rs"] * int(leg.get("lots") or 1):
                hit = "TARGET"
            elif leg.get("trail_arm") and leg["peak"] >= leg["trail_arm"] * int(leg.get("lots") or 1) \
                    and leg["_pnl"] <= (leg["peak"] - leg["trail_gap"] * int(leg.get("lots") or 1)):
                hit = "TRAIL"
            if hit:
                leg["open"] = False; leg["exitp"] = leg["_cur"]; leg["_bookpnl"] = leg["_pnl"]; leg["exit_reason"] = hit
                if sqoff == "all":
                    for o in L:
                        if o["open"]:
                            o["open"] = False; o["exitp"] = o["_cur"]; o["_bookpnl"] = o["_pnl"]; o["exit_reason"] = "SQOFF_" + hit
                    ended = True
                    break
        if ended:
            continue
        # strategy exits
        if strat_sl and comb <= -strat_sl:
            for o in L:
                if o["open"]:
                    o["open"] = False; o["exitp"] = o["_cur"]; o["_bookpnl"] = o["_pnl"]; o["exit_reason"] = "STRAT_SL"
            ended = True
        elif strat_tp and comb >= strat_tp:
            for o in L:
                if o["open"]:
                    o["open"] = False; o["exitp"] = o["_cur"]; o["_bookpnl"] = o["_pnl"]; o["exit_reason"] = "STRAT_TP"
            ended = True
    # close remaining at exit
    for leg in L:
        if leg["open"]:
            leg["open"] = False; leg["exitp"] = leg["ser"][-1] if leg["ser"][-1] is not None else leg["entry"]
            sgn = 1.0 if leg["side"] == "SELL" else -1.0
            leg["_bookpnl"] = sgn * (leg["entry"] - leg["exitp"]) * leg["qty"]; leg["exit_reason"] = "EOD"
    gross = sum(leg["_bookpnl"] for leg in L)
    # charges (per leg round-trip)
    chg = 0.0
    if _ch:
        when = _dt.datetime.strptime(date, "%Y-%m-%d")
        for leg in L:
            try:
                chg += _ch.option_charges(leg["entry"], leg["exitp"], leg["qty"],
                                          entry_side=leg["side"], when=when)
            except Exception:
                pass
    net = gross - chg
    legrows = [{"label": "%s %s %d%s" % (leg["side"][0], "ATM%+d" % leg["off"], leg["strike"], leg["ot"]),
                "strike": leg["strike"], "ot": leg["ot"], "side": leg["side"],
                "entry": round(leg["entry"], 2), "exit": round(leg["exitp"], 2),
                "pnl": round(leg["_bookpnl"]), "reason": leg.get("exit_reason", "EOD"),
                "src": leg["src"]} for leg in L]
    return {"date": date, "spot": round(spot_e, 1), "atm": int(atm),
            "gross": round(gross), "charges": round(chg), "net": round(net),
            "legs": legrows, "intraday": intr}


# ---------------------------------------------------------------- run
def run(u, legs, entry_hm, exit_hm, date_from, date_to,
        strat_sl=None, strat_tp=None, sqoff="all", weekdays=None):
    """Full multi-day backtest. Returns summary + per-day + breakups + equity + trade log.
    `legs` = [{side,opt,off,lots, sl_rs?, tp_rs?, trail_arm?, trail_gap?}] (off = points from ATM)."""
    u = u.upper()
    m_entry, m_exit = _hm_to_mod(entry_hm), _hm_to_mod(exit_hm)
    di0, di1 = _date_to_di(date_from), _date_to_di(date_to)
    off_lo = min(lg["off"] for lg in legs) // STEP.get(u, 50) - 6
    off_hi = max(lg["off"] for lg in legs) // STEP.get(u, 50) + 6
    off_lo, off_hi = max(-10, off_lo), min(10, off_hi)
    ots = sorted({lg["opt"] for lg in legs})
    # lake frames (one load per opt), then PRE-GROUP by day → O(1) per-day lookup (not O(rows))
    frames = [f for f in (_lake_frame(u, ot, off_lo, off_hi) for ot in ots) if f is not None]
    lake_all = pd.concat(frames, ignore_index=True) if frames else None
    lake_by_di = {int(di): g for di, g in lake_all.groupby("di")} if lake_all is not None else {}

    days = []
    di = di0
    while di <= di1:
        date = _di_to_date(di)
        wd = _dt.datetime.strptime(date, "%Y-%m-%d").weekday()  # 0=Mon
        if weekdays and wd not in weekdays:
            di += 1; continue
        lake = lake_by_di.get(di)
        dd = lake if lake is not None else _collector_day(u, date)   # this day's rows
        use_lake = lake is not None
        if dd is None or len(dd) == 0:
            di += 1; continue
        lot = lot_for(u, date)
        r = _sim_day(u, dd, date, legs, m_entry, m_exit, strat_sl, strat_tp, sqoff, lot, use_lake)
        if r:
            r["day"] = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][wd]
            days.append(r)
        di += 1

    return _aggregate(u, days, legs)


def _aggregate(u, days, legs):
    if not days:
        return {"ok": False, "reason": "is range me koi trading-day data nahi mila", "days": []}
    nets = np.array([d["net"] for d in days], float)
    eq = np.cumsum(nets)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    win = nets > 0; loss = nets < 0
    # streaks
    def _streak(mask):
        best = cur = 0
        for x in mask:
            cur = cur + 1 if x else 0
            best = max(best, cur)
        return best
    # monthly / weekday
    mon = {}
    wdw = {}
    for d in days:
        mon.setdefault(d["date"][:7], []).append(d["net"])
        wdw.setdefault(d["day"], []).append(d["net"])
    monthly = [{"m": k, "net": round(sum(v)), "days": len(v), "win": round(100 * np.mean(np.array(v) > 0))}
               for k, v in sorted(mon.items())]
    daywise = {k: round(sum(v)) for k, v in wdw.items()}
    avg_win = float(nets[win].mean()) if win.any() else 0.0
    avg_loss = float(nets[loss].mean()) if loss.any() else 0.0
    exp = (win.mean() * avg_win + loss.mean() * avg_loss) if len(nets) else 0.0
    mdd = float(dd.min()) if len(dd) else 0.0
    summary = {
        "net": round(float(nets.sum())), "gross": round(float(sum(d["gross"] for d in days))),
        "charges": round(float(sum(d["charges"] for d in days))),
        "days": len(days), "win_days": int(win.sum()), "loss_days": int(loss.sum()),
        "win_pct": round(100 * win.mean(), 1), "avg_day": round(float(nets.mean())),
        "max_profit": round(float(nets.max())), "max_profit_date": days[int(nets.argmax())]["date"],
        "max_loss": round(float(nets.min())), "max_loss_date": days[int(nets.argmin())]["date"],
        "mdd": round(mdd), "ret_mdd": round(float(nets.sum()) / abs(mdd), 2) if mdd else None,
        "max_win_streak": _streak(win), "max_loss_streak": _streak(loss),
        "avg_win": round(avg_win), "avg_loss": round(avg_loss), "expectancy": round(exp),
    }
    # trade log (drop heavy intraday from the list; keep per-day summary + legs)
    log = [{"date": d["date"], "day": d["day"], "spot": d["spot"], "atm": d["atm"],
            "net": d["net"], "gross": d["gross"], "charges": d["charges"], "legs": d["legs"]}
           for d in days]
    equity = [{"date": days[i]["date"], "eq": round(float(eq[i])), "dd": round(float(dd[i]))} for i in range(len(days))]
    srcs = {d["legs"][0]["src"] for d in days if d.get("legs")}
    src = "+".join(sorted(srcs)) if srcs else "lake"
    return {"ok": True, "underlying": u, "summary": summary, "monthly": monthly,
            "daywise": daywise, "equity": equity, "trades": log, "src": src}


def intraday(u, legs, entry_hm, exit_hm, date, strat_sl=None, strat_tp=None, sqoff="all"):
    """Single-day minute-by-minute combined MTM + spot (for the per-day PnL modal)."""
    u = u.upper()
    di = _date_to_di(date)
    off_lo = min(lg["off"] for lg in legs) // STEP.get(u, 50) - 6
    off_hi = max(lg["off"] for lg in legs) // STEP.get(u, 50) + 6
    off_lo, off_hi = max(-10, off_lo), min(10, off_hi)
    ots = sorted({lg["opt"] for lg in legs})
    frames = [f for f in (_lake_frame(u, ot, off_lo, off_hi) for ot in ots) if f is not None]
    lake_all = pd.concat(frames, ignore_index=True) if frames else None
    lake = lake_all[lake_all["di"] == di] if (lake_all is not None and (lake_all["di"] == di).any()) else None
    dd = lake if lake is not None else _collector_day(u, date)
    use_lake = lake is not None
    if dd is None or len(dd) == 0:
        return {"ok": False, "reason": "is din ka data nahi"}
    lot = lot_for(u, date)
    r = _sim_day(u, dd, date, legs, _hm_to_mod(entry_hm), _hm_to_mod(exit_hm), strat_sl, strat_tp, sqoff, lot, use_lake)
    if not r:
        return {"ok": False, "reason": "premium series nahi mili"}
    return {"ok": True, "date": date, "net": r["net"], "gross": r["gross"],
            "legs": r["legs"], "intraday": r["intraday"]}
