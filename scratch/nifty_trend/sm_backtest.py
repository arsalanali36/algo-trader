"""Backtest half of the StockMock-style (_sm) strategy pipeline.

The SAME `_sm` config that drives the live-paper firer (_ops/sm_runner +
trader_dashboard._fire_sm_strategy) is backtested here on OUR lake (2021->), then emitted as
a runs/<slug>/ Lab report (RESULTS_SCHEMA) so it shows in the Lab hub + Stats "Backtest"
toggle + registry. Rule 10: same config → backtest and live can't silently diverge.

Model (validated vs StockMock's exported per-expiry PDF, 2026-08-05): entry = the entry-
minute's OPEN (StockMock fill convention); held ATM strike (not rolling-ATM); per-leg SL fires
on the minute HIGH at entry×(1+sl%), fills at that level (square-off-one = legs independent);
0.5% slippage/leg (StockMock includes) + real date-aware Zerodha charges + date-aware lot.
Win-rate matched StockMock 56% ≈ 56.7%; trending/loss days ~1%.

Usage:  python scratch/nifty_trend/sm_backtest.py sm_nifty_expiry_v1   # reads nifty_config, emits run
"""
import os, sys, json, math, datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "_ops"), os.path.join(_ROOT, "_core"),
           os.path.join(_ROOT, "_data"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _paths  # noqa

import pandas as pd
import backtest_lab as bl          # STEP, IST, DAY, lot_for, _di_to_date, _date_to_di, _hm_to_mod, _ffill, _w
import sm_runner as smr            # is_expiry_day, parse_cfg
try:
    import charges as _ch
except Exception:
    _ch = None

SLIP = 0.005   # 0.5% per leg (StockMock includes)


def _wide(u, ot):
    """Wide offset frame (open+high+close) for held-strike tracking across the day."""
    root = bl._w._lake_root(u)
    frames = []
    for off in range(-10, 11):
        p = os.path.join(root, bl._w._ofn(ot, off))
        if os.path.exists(p):
            try:
                frames.append(pd.read_csv(p, usecols=["timestamp", "open", "high", "close", "strike", "spot"]))
            except Exception:
                pass
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).dropna(subset=["close", "strike"])
    istp = df["timestamp"].values.astype("int64") + bl.IST
    df["di"] = istp // bl.DAY
    df["mod"] = ((istp % bl.DAY) // 60).astype("int32")
    df["strike"] = df["strike"].astype("int32")
    return df


def simulate(cfg, from_date, to_date):
    """Run the _sm strategy over [from_date, to_date] on the lake. Returns list of per-day
    dicts: {date, spot, atm, lot, net, gross, chg, legs:[{opt,side,lots,strike,entry,exit,
    pnl,gross,fee,points,reason,qty}]}."""
    u = cfg["instrument"]
    legs_cfg = cfg["legs"]
    m_e = bl._hm_to_mod(cfg["entry_hm"])
    m_x = bl._hm_to_mod(cfg["exit_hm"])
    grid = list(range(m_e, m_x + 1))
    step = bl.STEP.get(u, 50)

    DF = {}
    for ot in sorted({lg["opt"] for lg in legs_cfg}):
        DF[ot] = _wide(u, ot)
        if DF[ot] is None:
            return []
    ref = DF[legs_cfg[0]["opt"]]
    all_dates = sorted(bl._di_to_date(d) for d in ref["di"].unique().tolist())
    dates = [d for d in all_dates if from_date <= d <= to_date and smr.should_fire_today(d, cfg)]

    def leg_series(df, di, strike):
        g = df[(df["di"] == di) & (df["strike"] == strike) & (df["mod"] >= m_e) & (df["mod"] <= m_x)].sort_values("mod")
        if len(g) == 0:
            return None, None, None
        entry_open = float(g.iloc[0]["open"])
        cmap, hmap = {}, {}
        for mo, cl, hi in zip(g["mod"].values.tolist(), g["close"].values.tolist(), g["high"].values.tolist()):
            cmap.setdefault(int(mo), float(cl)); hmap.setdefault(int(mo), float(hi))
        return entry_open, bl._ffill(cmap, grid), bl._ffill(hmap, grid)

    out = []
    for date in dates:
        di = bl._date_to_di(date)
        g = ref[(ref["di"] == di) & (ref["mod"] >= m_e)].sort_values("mod")
        if len(g) == 0:
            continue
        spot = float(g.iloc[0]["spot"])
        atm = round(spot / step) * step
        lot = bl.lot_for(u, date)
        legs_out = []
        ok = True
        for lc in legs_cfg:
            strike = int(atm + lc["off"] * step)
            entry, ser, ser_hi = leg_series(DF[lc["opt"]], di, strike)
            if entry is None or not ser:
                ok = False; break
            qty = lot * lc["lots"]
            sgn = 1.0 if lc["side"] == "SELL" else -1.0
            exitp, reason = None, "EOD"
            if lc.get("sl_pct"):
                sl_lvl = entry * (1 + lc["sl_pct"] / 100.0) if lc["side"] == "SELL" else entry * (1 - lc["sl_pct"] / 100.0)
                for hi in ser_hi:                       # SL triggers on the minute HIGH
                    if hi is None:
                        continue
                    if (lc["side"] == "SELL" and hi >= sl_lvl):
                        exitp, reason = sl_lvl, "SL"; break
            if exitp is None:
                exitp = next((v for v in reversed(ser) if v is not None), entry)   # exit-time close
            e_eff = entry * (1 - SLIP) if lc["side"] == "SELL" else entry * (1 + SLIP)
            x_eff = exitp * (1 + SLIP) if lc["side"] == "SELL" else exitp * (1 - SLIP)
            gross = sgn * (e_eff - x_eff) * qty
            fee = 0.0
            if _ch:
                try:
                    fee = _ch.option_charges(entry, exitp, qty, entry_side=lc["side"],
                                             when=_dt.datetime.strptime(date, "%Y-%m-%d"))
                except Exception:
                    fee = 0.0
            legs_out.append({
                "opt": lc["opt"], "side": lc["side"], "lots": lc["lots"], "strike": strike,
                "entry": round(entry, 2), "exit": round(exitp, 2), "qty": qty,
                "gross": round(gross), "fee": round(fee), "pnl": round(gross - fee),
                "points": round(sgn * (entry - exitp), 2), "reason": reason})
        if not ok or not legs_out:
            continue
        gross = sum(l["gross"] for l in legs_out); chg = sum(l["fee"] for l in legs_out)
        out.append({"date": date, "spot": round(spot), "atm": atm, "lot": lot,
                    "gross": round(gross), "chg": round(chg), "net": round(gross - chg),
                    "legs": legs_out})
    return out


# ------------------------------------------------------------------- metrics + emit
def _metrics(days, start_cap):
    """Day-level (per straddle) metrics on the per-day net series."""
    nets = [d["net"] for d in days]
    n = len(nets)
    if n == 0:
        return {}
    net = sum(nets)
    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x < 0]
    mean = net / n
    var = sum((x - mean) ** 2 for x in nets) / n if n else 0
    sd = math.sqrt(var)
    yrs = max(1e-9, (_dt.date.fromisoformat(days[-1]["date"]) - _dt.date.fromisoformat(days[0]["date"])).days / 365.25)
    tpy = n / yrs
    sharpe = (mean / sd * math.sqrt(tpy)) if sd else 0.0
    # cumulative equity + maxdd
    eq = 0.0; peak = 0.0; mdd = 0.0
    for x in nets:
        eq += x; peak = max(peak, eq); mdd = max(mdd, peak - eq)
    pf = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    return {
        "trades": n, "net_abs": round(net), "net_pct": round(net / start_cap * 100, 2),
        "final_cap": round(start_cap + net), "start_cap": start_cap,
        "sharpe": round(sharpe, 3), "maxdd": round(-mdd / start_cap * 100, 3), "maxdd_abs": round(-mdd),
        "annual_return": round(net / yrs / start_cap * 100, 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(pf, 3) if math.isfinite(pf) else None,
        "expectancy": round(net / n), "avg_win": round(sum(wins) / len(wins)) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses)) if losses else 0,
        "largest_win": max(nets), "largest_loss": min(nets), "years": round(yrs, 2),
    }


def _all_trades(days):
    """Per-leg rows in RESULTS_SCHEMA all_trades[] shape (calendar buckets by entry_date)."""
    rows = []
    for d in days:
        for l in d["legs"]:
            rows.append({
                "side": "short" if l["side"] == "SELL" else "long",
                "opt_type": l["opt"], "strike": l["strike"],
                "entry_dt": "%s %s" % (d["date"], _entry_hm), "exit_dt": "%s %s" % (d["date"], _exit_hm),
                "entry_prem": l["entry"], "exit_prem": l["exit"],
                "entry_spot": d["spot"], "exit_spot": d["spot"],
                "points": l["points"], "gross": l["gross"], "fee": l["fee"],
                "net": l["pnl"], "pnl": l["pnl"], "qty": l["qty"], "bars": None,
                "reason": l["reason"], "ym": d["date"][:7]})
    return rows


_entry_hm = "09:22"; _exit_hm = "15:15"


def emit_run(slug, title, cfg, days, start_cap=331000):
    """Write runs/<slug>/{results.js,meta.json,index.html} + append runs/index.json."""
    global _entry_hm, _exit_hm
    _entry_hm, _exit_hm = cfg["entry_hm"], cfg["exit_hm"]
    runs = os.path.join(_HERE, "runs")
    d = os.path.join(runs, slug); os.makedirs(d, exist_ok=True)
    # train/oos split by date (65/35)
    cut = int(len(days) * 0.65)
    splits = {"full": days, "train": days[:cut], "oos": days[cut:]}
    lot = days[-1]["lot"] if days else 0
    meta = {"window": [days[0]["date"], days[-1]["date"]] if days else [], "days": len(days),
            "start_cap": start_cap, "design": title, "tf": "1m", "instrument": cfg["instrument"] + " options",
            "lot_size": lot, "lots": 1, "intraday": True, "periods": ["full", "train", "oos"],
            "sm_config": cfg}
    combos = {}
    for period, dd in splits.items():
        if not dd:
            continue
        m = _metrics(dd, start_cap)
        at = _all_trades(dd)
        # cumulative equity for the chart
        eq = []; c = 0.0
        for x in dd:
            c += x["net"]; eq.append(round(c))
        combos["bs|%s" % period] = {"metrics": m, "all_trades": at, "trades": at,
                                    "equity": eq, "labels": [x["date"] for x in dd]}
    results = {"meta": meta, "combos": combos}
    with open(os.path.join(d, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(results, ensure_ascii=False) + ";")
    mfull = combos.get("bs|full", {}).get("metrics", {})
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write('<!doctype html><meta charset=utf-8><title>%s</title>'
                '<body style="background:#0d1117;color:#e6edf3;font-family:system-ui;padding:20px">'
                '<h2>%s</h2><p>Net ₹%s · Sharpe %s · Win %s%% · MaxDD ₹%s · %d expiries</p>'
                '<p style="color:#8b949e">StockMock-parity backtest — see Lab hub / Stats "Backtest" toggle for the full calendar.</p>'
                '<script src="results.js"></script></body>'
                % (title, title, format(mfull.get("net_abs", 0), ","), mfull.get("sharpe"),
                   mfull.get("win_rate"), format(-mfull.get("maxdd_abs", 0), ","), meta["days"]))
    # append/update runs/index.json
    idx_path = os.path.join(runs, "index.json")
    try:
        idx = json.load(open(idx_path, encoding="utf-8"))
    except Exception:
        idx = []
    idx = [e for e in idx if e.get("slug") != slug]
    bs = combos.get("bs|full", {}).get("metrics", {})
    idx.append({"slug": slug, "design": "sm_expiry", "title": title, "tf": "1m",
                "params": {"legs": cfg["legs"], "entry": cfg["entry_hm"], "exit": cfg["exit_hm"],
                           "day_filter": cfg["day_filter"]},
                "exit": "per-leg SL%% + %s EOD" % cfg["exit_hm"], "instrument": cfg["instrument"] + " options",
                "lot_size": lot, "window": meta["window"], "days": len(days),
                "significant": False,
                "bs_full": {"sharpe": bs.get("sharpe"), "net_pct": bs.get("net_pct"),
                            "maxdd": bs.get("maxdd"), "win_rate": bs.get("win_rate"),
                            "trades": bs.get("trades"), "profit_factor": bs.get("profit_factor")},
                "p_value": None, "deploy_key": cfg["id"], "deployed": cfg["id"],
                "real_cost": {"method": "REAL OptChainLake_1m premium (held-strike, open-entry, high-SL) + 0.5% slip + Zerodha charges",
                              "note": "StockMock-parity — validated vs StockMock PDF (win 56%, loss-days ~1%)"}})
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    return results, mfull


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "sm_nifty_expiry_v1"
    ncfg = json.load(open(os.path.join(_ROOT, "nifty_config.json"), encoding="utf-8"))
    cfg = smr.parse_cfg(sid, ncfg.get(sid, {}))
    if not cfg or not cfg["valid"]:
        print("no valid _sm config for", sid); sys.exit(1)
    reg = json.load(open(os.path.join(_ROOT, "strategy_registry.json"), encoding="utf-8"))
    title = next(("%s - %s" % (k, v.get("name")) for k, v in reg["strategies"].items()
                  if v.get("config_key") == sid), sid)
    print("simulating", title, "…")
    days = simulate(cfg, "2021-01-01", "2026-12-31")
    if not days:
        print("no trades"); sys.exit(1)
    results, m = emit_run(sid, title, cfg, days)
    print("emitted runs/%s/ — %d expiries · net ₹%s · Sharpe %s · win %s%% · maxDD ₹%s"
          % (sid, len(days), format(m["net_abs"], ","), m["sharpe"], m["win_rate"], format(-m["maxdd_abs"], ",")))
