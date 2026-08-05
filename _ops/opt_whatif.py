"""
opt_whatif.py — manual options "what-if" backtest from REAL chain data.

For a set of legs (side/strike/type) on a date + entry/exit time, computes the
P&L from ACTUAL premiums, split into price-move / IV-crush / decay. Display &
analysis only — no order/live path.

Data source is auto-picked per date:
  - recent days  -> live collector  (_TRADING_DATA/OptionChain/<U>/<U>_<date>.csv):
                    real Dhan greeks + IV per minute.
  - older days   -> expired-options lake (_TRADING_DATA/OptChainLake_1m/<U>/WEEK/):
                    held-strike reconstruction of the real 1-min premium; the lake
                    has NO stored greeks, so IV is inverted from the premium and the
                    greeks are Black-Scholes-derived (bs_option.py, the single BS
                    source — Rule 6B). Premium / P&L stays 100% real either way.

Decomposition (per leg, using ENTRY greeks — same method that reconciles the live
strangle to the rupee): price = Δ·ΔS + ½·Γ·ΔS² ; iv = vega·ΔIV ; decay = total −
price − iv (so the three always sum to the real total).
"""
import os
import sys
import csv
import glob
import math

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import option_curves as _oc   # reuse the collector CSV loader + float parse (Rule 6B)

STEP = {"NIFTY": 50, "BANKNIFTY": 100}
LOT = {"NIFTY": 65, "BANKNIFTY": 30}    # SEM_LOT_UNITS from the Dhan scrip master (verified)
_R = 0.065


def _f(x):
    return _oc._f(x)


def _hm2m(hm):
    """'HH:MM' -> minutes since midnight, or None if unparseable."""
    try:
        h, m = str(hm)[:5].split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


# How far (minutes) a leg's ACTUAL entry/exit snapshot may drift from the requested
# time before we flag it. The collector only stores ~ATM±10 strikes per minute, so a
# strike that's far OTM at the requested time simply isn't in the chain yet and its
# first available snapshot lands much later (or an earlier last-known on exit). We warn
# instead of silently pretending the trade opened/closed at the asked time.
_TIME_TOL_MIN = 5


# ---------------------------------------------------------------- Black-Scholes
_bs = None


def _bsmod():
    global _bs
    if _bs is not None:
        return _bs or None
    d = os.path.join(PROJECT, "scratch", "nifty_trend")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    try:
        import bs_option as m
        _bs = m
    except Exception:
        _bs = False
    return _bs or None


def _greeks_bs(S, K, T, sigma, opt):
    """Numerical greeks off bs_option.bs_price / bs_delta (no new BS impl)."""
    m = _bsmod()
    if not m or T <= 0 or sigma <= 0:
        return None
    o = "CE" if opt == "CE" else "PE"
    hS = max(S * 0.001, 1.0)
    delta = m.bs_delta(S, K, T, sigma, _R, o)
    gamma = (m.bs_price(S + hS, K, T, sigma, _R, o) - 2 * m.bs_price(S, K, T, sigma, _R, o)
             + m.bs_price(S - hS, K, T, sigma, _R, o)) / (hS * hS)
    # vega per +1 IV-point (1%): σ + 0.01
    vega = m.bs_price(S, K, T, sigma + 0.01, _R, o) - m.bs_price(S, K, T, sigma, _R, o)
    return {"delta": delta, "gamma": gamma, "vega": vega}   # iv used as % elsewhere


# ---------------------------------------------------------------- collector source
def _collector_legs(u, date, entry_hm, exit_hm, legs, expiry=None, exit_date=None):
    """Return per-leg entry/exit dicts from the live collector CSV, or None if that
    day/those strikes aren't in the collector lake. `expiry` picks a specific stored
    expiry (real-like simulation); default = nearest weekly. `exit_date` (default =
    entry `date`) lets a positional hold exit on a LATER day — entry premium comes from
    `date`, exit premium from `exit_date`, same held expiry+strike on both."""
    exit_date = exit_date or date
    _, e_rows = _oc._load_rows(u, date)
    if not e_rows:
        return None
    if exit_date == date:
        x_rows = e_rows
    else:
        _, x_rows = _oc._load_rows(u, exit_date)
        if not x_rows:
            return None
    exps = sorted({r.get("expiry") for r in e_rows if r.get("expiry")})
    if not exps:
        return None
    expiry = expiry if (expiry and expiry in exps) else exps[0]   # chosen expiry, else nearest weekly
    e_day = [r for r in e_rows if r.get("expiry") == expiry]
    x_day = [r for r in x_rows if r.get("expiry") == expiry]
    if not e_day or not x_day:
        return None                        # held expiry missing on a side (e.g. exit after expiry)

    def at(day, strike, ot, hm, last=False):
        c = sorted((r for r in day if _f(r.get("strike")) == strike and r.get("opt_type") == ot),
                   key=lambda r: r.get("datetime") or "")
        if not c:
            return None
        if last:
            return c[-1]
        for r in c:
            if (r.get("datetime") or "")[11:16] >= hm:
                return r
        return c[-1]

    out = []
    for lg in legs:
        e = at(e_day, lg["strike"], lg["type"], entry_hm)
        x = at(x_day, lg["strike"], lg["type"], exit_hm, last=(exit_hm >= "15:25"))
        if not e or not x or _f(e.get("ltp")) is None or _f(x.get("ltp")) is None:
            return None                    # incomplete -> let caller try the lake
        out.append({
            "e_prem": _f(e["ltp"]), "x_prem": _f(x["ltp"]),
            "e_spot": _f(e["spot"]), "x_spot": _f(x["spot"]),
            "e_iv": _f(e.get("iv")), "x_iv": _f(x.get("iv")),
            "delta": _f(e.get("delta")), "gamma": _f(e.get("gamma")), "vega": _f(e.get("vega")),
            "e_t": (e["datetime"])[11:16], "x_t": (x["datetime"])[11:16], "src": "collector",
            "e_dt": (e["datetime"])[:10], "x_dt": (x["datetime"])[:10],
        })
    return {"expiry": expiry, "legs": out}


# ---------------------------------------------------------------- historical lake
def _lake_root(u):
    for base in (os.path.join(os.path.dirname(PROJECT), "._TRADING DATA"),
                 os.path.join(PROJECT, "_TRADING_DATA")):
        p = os.path.join(base, "OptChainLake_1m", u, "WEEK")
        if os.path.isdir(p):
            return p
    return None


import datetime as _dt
_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _date_range(date):
    d = _dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=_IST)
    s = int(d.timestamp())
    return s, s + 86400


def _hm(ts):
    return _dt.datetime.fromtimestamp(int(ts), _IST).strftime("%H:%M")


_DAY_CACHE = {}   # (path, start) -> (mtime, small df for that one day) — memory-safe (~375 rows)


def _scan_date(path, start, end):
    """One offset file's rows in [start,end): DataFrame[timestamp,close,strike,spot].
    Pandas C parser (≈10x faster than csv.reader on these 467k-row files); only the
    small per-day slice is cached (repeat queries on the same date are instant)."""
    import pandas as pd
    if not os.path.exists(path):
        return None
    mt = os.path.getmtime(path)
    key = (path, start)
    c = _DAY_CACHE.get(key)
    if c and c[0] == mt:
        return c[1]
    try:
        df = pd.read_csv(path, usecols=["timestamp", "close", "strike", "spot"])
        sub = df[(df["timestamp"] >= start) & (df["timestamp"] < end)].copy()
    except Exception:
        return None
    if len(_DAY_CACHE) > 400:
        _DAY_CACHE.clear()
    _DAY_CACHE[key] = (mt, sub)
    return sub


def _ofn(ot, off):
    if off == 0:
        return f"{ot}_ATM.csv"
    return f"{ot}_ATM{'p' if off > 0 else 'm'}{abs(off)}.csv"


def _lake_series(root, u, date, strike, ot):
    """Held-strike reconstruction for ONE day — read only the OFFSET files near the
    target strike (from the day's spot range), not all 21. Returns (series, hms) where
    series[hm] = (prem, spot, ts), or None if that strike isn't present that day."""
    step = STEP.get(u, 50)
    start, end = _date_range(date)
    # 1) the day's spot range from the ATM file → which offset files to read
    atm = _scan_date(os.path.join(root, _ofn(ot, 0)), start, end)
    if atm is None or atm.empty:
        return None
    spots = atm["spot"].dropna()
    if spots.empty:
        return None
    o_lo = int(round((strike - spots.max()) / step)) - 1
    o_hi = int(round((strike - spots.min()) / step)) + 1
    series = {}   # hm -> (prem, spot, ts)
    for off in range(min(o_lo, o_hi), max(o_lo, o_hi) + 1):
        d = _scan_date(os.path.join(root, _ofn(ot, off)), start, end)
        if d is None or d.empty:
            continue
        hit = d[d["strike"] == strike]
        for ts, close, sp in zip(hit["timestamp"], hit["close"], hit["spot"]):
            if close == close:   # not NaN
                series[_hm(ts)] = (float(close), float(sp) if sp == sp else None, int(ts))
    if not series:
        return None
    return series, sorted(series)


def _lake_point(root, u, date, strike, ot, hm, last=False):
    """One (prem, spot, ts) snapshot for a held strike at time `hm` on `date`."""
    r = _lake_series(root, u, date, strike, ot)
    if not r:
        return None
    series, hms = r
    if last:
        h = hms[-1]
    else:
        h = next((x for x in hms if x >= hm), hms[-1])
    prem, spot, ts = series[h]
    return {"prem": prem, "spot": spot, "ts": ts, "t": h}


def _lake_legs(u, date, entry_hm, exit_hm, legs, exit_date=None):
    exit_date = exit_date or date
    root = _lake_root(u)
    if not root:
        return None
    m = _bsmod()
    import pandas as pd
    out = []
    for lg in legs:
        K = float(lg["strike"])
        e = _lake_point(root, u, date, K, lg["type"], entry_hm)
        x = _lake_point(root, u, exit_date, K, lg["type"], exit_hm, last=(exit_hm >= "15:25"))
        if not e or not x or e["prem"] is None or x["prem"] is None:
            return None
        # BS-derive IV + greeks (lake has no stored greeks)
        d = None; e_iv = None; x_iv = None
        if m and e["spot"] and x["spot"]:
            T_e = m.tte_years(pd.Timestamp(e["ts"], unit="s", tz="Asia/Kolkata"))
            T_x = m.tte_years(pd.Timestamp(x["ts"], unit="s", tz="Asia/Kolkata"))
            try:
                se = m.implied_vol(e["prem"], e["spot"], K, T_e, _R, lg["type"])
                sx = m.implied_vol(x["prem"], x["spot"], K, T_x, _R, lg["type"])
                e_iv, x_iv = se * 100.0, sx * 100.0
                d = _greeks_bs(e["spot"], K, T_e, se, lg["type"])
            except Exception:
                pass
        out.append({
            "e_prem": e["prem"], "x_prem": x["prem"], "e_spot": e["spot"], "x_spot": x["spot"],
            "e_iv": e_iv, "x_iv": x_iv,
            "delta": d["delta"] if d else None, "gamma": d["gamma"] if d else None,
            "vega": d["vega"] if d else None,
            "e_t": e["t"], "x_t": x["t"], "src": "lake",
            "e_dt": date, "x_dt": exit_date,
        })
    return {"expiry": None, "legs": out}


# ---------------------------------------------------------------- public
def available_dates(u):
    """Dates offering data — collector (recent) ∪ lake (historical), newest first."""
    ds = set(__import__("gex_profile").available_dates(u))  # collector days (reuse)
    return sorted(ds, reverse=True)


def iv_coverage(u):
    """Since-when REAL IV is available. IV is model-sensitive, so ONLY the broker's own
    reported IV (the live collector window) is trustworthy — we deliberately do NOT
    surface a Black-Scholes-inverted guess as if it were IV. Older lake days still have
    REAL premium/P&L (back to ~2021), just no real IV. Returns:
      real       = {from, to, days}  collector window with broker's own IV (or None)
      premium_from = earliest lake date (real premium/P&L, but NO real IV)."""
    u = u.upper()
    cds = sorted(__import__("gex_profile").available_dates(u))   # collector days (real IV)
    real = {"from": cds[0], "to": cds[-1], "days": len(cds)} if cds else None
    premium_from = None
    root = _lake_root(u)
    if root:
        done = os.path.join(os.path.dirname(root), "_done.json")
        try:
            import json
            with open(done, encoding="utf-8") as fh:
                keys = json.load(fh)
            ds = sorted(k.split("|")[-1] for k in keys
                        if "|" in k and len(k.split("|")[-1]) == 10 and k.split("|")[-1][4] == "-")
            if ds:
                premium_from = ds[0]
        except Exception:
            pass
    return {"ok": True, "underlying": u, "real": real, "premium_from": premium_from}


def chain_at(u, date, hm, expiry=None, n=10, sel=None):
    """Option-chain snapshot AT a backtest date+time — for the What-If chain-GRID picker.
    Per strike (ATM±n): REAL premium + REAL IV from the live collector. Collector-only by
    design — historical lake has no real IV, so on those days this returns ok:False and the
    UI falls back to typed strikes (never a BS-guessed IV in the grid). Each leg's snapshot
    is the first minute >= `hm` (else that leg's last known).

    `sel` = already-selected leg strikes: the window is WIDENED to include them (so a wide
    strangle's far legs still appear + highlight), and any selected strike the collector never
    captured that minute is returned as an empty (ltp/iv=None) row so it's still visible."""
    u = u.upper()
    try:
        _, rows = _oc._load_rows(u, date)
    except Exception:
        rows = None
    if not rows:
        return {"ok": False, "reason": "no-collector", "underlying": u, "date": date, "strikes": []}
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if not exps:
        return {"ok": False, "reason": "no-expiry", "underlying": u, "date": date, "strikes": []}
    exp = expiry if (expiry and expiry in exps) else exps[0]
    day = [r for r in rows if r.get("expiry") == exp]
    strikes = sorted({_f(r.get("strike")) for r in day if _f(r.get("strike")) is not None})
    if not strikes:
        return {"ok": False, "reason": "no-strikes", "underlying": u, "date": date, "strikes": []}

    def at(strike, ot):
        c = sorted((r for r in day if _f(r.get("strike")) == strike and r.get("opt_type") == ot),
                   key=lambda r: r.get("datetime") or "")
        if not c:
            return None
        for r in c:
            if (r.get("datetime") or "")[11:16] >= hm:
                return r
        return c[-1]

    def side(r):
        if not r:
            return {"ltp": None, "iv": None, "delta": None, "oi": None}
        lt, iv, dl, oi = _f(r.get("ltp")), _f(r.get("iv")), _f(r.get("delta")), _f(r.get("oi"))
        return {"ltp": round(lt, 1) if lt is not None else None,
                "iv": round(iv, 2) if iv is not None else None,
                "delta": round(dl, 2) if dl is not None else None,
                "oi": round(oi) if oi is not None else None}

    # spot at (or after) hm from any leg's snapshot
    spot = None
    for r in sorted(day, key=lambda r: r.get("datetime") or ""):
        if (r.get("datetime") or "")[11:16] >= hm:
            spot = _f(r.get("spot"))
            if spot:
                break
    if spot is None:
        spot = _f(day[-1].get("spot"))
    step = STEP.get(u, 50)
    if len(strikes) >= 2:
        diffs = sorted(strikes[i + 1] - strikes[i] for i in range(len(strikes) - 1))
        step = diffs[len(diffs) // 2] or step
    atm = min(strikes, key=lambda x: abs(x - spot)) if spot else strikes[len(strikes) // 2]
    lo, hi = atm - n * step, atm + n * step
    # widen the window to include any selected leg strikes (sane cap = ATM ± 40 steps so a
    # typo can't produce a 1000-row grid). Collector may not have far strikes → empty rows.
    sel_ok = []
    if sel:
        cap = 40 * step
        for s in sel:
            s = _f(s)
            if s is not None and abs(s - atm) <= cap:
                sel_ok.append(int(s))
                lo, hi = min(lo, s), max(hi, s)
    ks = [k for k in strikes if lo <= k <= hi]
    have = set(int(k) for k in ks)
    out = [{"strike": int(k), "ce": side(at(k, "CE")), "pe": side(at(k, "PE"))} for k in ks]
    # ensure every selected strike has a row even if the collector never captured it that minute
    for s in sorted(set(sel_ok) - have):
        out.append({"strike": s, "ce": {"ltp": None, "iv": None}, "pe": {"ltp": None, "iv": None}})
    out.sort(key=lambda r: r["strike"])
    return {"ok": True, "underlying": u, "date": date, "expiry": exp, "expiries": exps,
            "hm": hm, "spot": round(spot, 2) if spot else None,
            "atm": int(atm) if atm else None, "step": step, "strikes": out}


def payoff_at(u, date, hm, legs, expiry=None, exit_date=None, exit_hm=None, mult=1):
    """Payoff + KPI for the whatif2 Strategy Builder — legs priced at their ENTRY
    date/time REAL premium + REAL IV (collector). Reuses payoff.py's pure functions
    (Rule 6B — no second payoff engine): payoff at EXPIRY (intrinsic) + at EXIT-day
    (Black-Scholes, sticky entry-IV), max P/L, breakevens, POP (lognormal), plus
    net-credit / time-value / intrinsic. Collector days only (real IV). Display-only.

    legs = [{side:'SELL'|'BUY', strike, type:'CE'|'PE', qty?}]. Effective units per leg
    = lot_size × `mult` (global multiplier) × leg `qty` (per-leg lots, default 1)."""
    u = u.upper()
    try:
        import payoff as pf
    except Exception as e:
        return {"ok": False, "reason": "payoff module unavailable: %s" % e}
    sel = [l.get("strike") for l in (legs or [])]
    snap = chain_at(u, date, hm, expiry, n=10, sel=sel)
    if not snap.get("ok"):
        return {"ok": False, "reason": snap.get("reason", "no-collector")}
    spot = snap.get("spot")
    exp = snap.get("expiry")
    by_k = {int(r["strike"]): r for r in snap.get("strikes", [])}
    lot = LOT.get(u, 1) * max(1, int(mult or 1))
    plegs = []
    for l in (legs or []):
        k = _f(l.get("strike"))
        ot = str(l.get("type") or "").upper()
        sd = str(l.get("side") or "").upper()
        if k is None or ot not in ("CE", "PE") or sd not in ("SELL", "BUY"):
            continue
        row = by_k.get(int(k))
        cell = (row.get("ce") if ot == "CE" else row.get("pe")) if row else None
        prem = cell.get("ltp") if cell else None
        ivp = cell.get("iv") if cell else None
        if prem is None:
            return {"ok": False, "reason": "%d %s ka premium is date/time pe nahi mila (captured band se bahar)" % (int(k), ot)}
        plegs.append({"strike": float(int(k)), "opt": ot, "side": sd,
                      "qty": lot * max(1, int(l.get("qty") or 1)),
                      "entry": float(prem), "ltp": float(prem),
                      "iv": (ivp / 100.0) if ivp else None})
    if not plegs:
        return {"ok": False, "reason": "koi valid leg nahi"}

    # T to expiry from entry moment, and from exit moment (for the exit-day curve)
    T_e = T_x = None
    if exp:
        try:
            ed = _dt.datetime.strptime(exp, "%Y-%m-%d").date()
            e_dt = _dt.datetime.strptime("%s %s" % (date, hm[:5]), "%Y-%m-%d %H:%M")
            T_e = pf.tte_years(ed, e_dt)
            x_dt = _dt.datetime.strptime("%s %s" % (exit_date or date, (exit_hm or hm)[:5]), "%Y-%m-%d %H:%M")
            T_x = pf.tte_years(ed, x_dt)
        except Exception:
            pass

    base = spot or plegs[0]["strike"]
    lo, hi = pf._grid(plegs, base)
    step = max((hi - lo) / 400.0, 0.5)
    curve_e, S = [], lo
    while S <= hi:
        curve_e.append([round(S, 2), round(pf.payoff_expiry(plegs, S), 2)])
        S += step
    curve_x = None
    if T_x and T_x > 0 and all(l.get("iv") for l in plegs):
        cx = [[p[0], pf.payoff_today(plegs, p[0], T_x)] for p in curve_e]
        if not any(v is None for _, v in cx):
            curve_x = [[a, round(b, 2)] for a, b in cx]
    zones = pf.profit_zones(plegs, lo, hi, step)
    ys = [p[1] for p in curve_e]
    ivs = [l["iv"] for l in plegs if l.get("iv")]
    avg_iv = (sum(ivs) / len(ivs)) if ivs else None
    pop = pf.prob_of_profit(zones, spot, T_e, avg_iv, lo, hi) if (avg_iv and T_e) else None

    net_cr = net_intr = net_tv = 0.0
    for l in plegs:
        intr = max(spot - l["strike"], 0.0) if l["opt"] == "CE" else max(l["strike"] - spot, 0.0)
        g = 1 if l["side"] == "SELL" else -1
        net_cr += g * l["entry"] * l["qty"]
        net_intr += g * intr * l["qty"]
        net_tv += g * (l["entry"] - intr) * l["qty"]
    return {"ok": True, "underlying": u, "spot": spot, "expiry": exp,
            "curve_expiry": curve_e, "curve_exit": curve_x,
            "max_profit": round(max(ys), 2), "max_loss": round(min(ys), 2),
            "breakevens": sorted({round(x, 1) for z in zones for x in z if lo < x < hi}),
            "pop": round(pop, 4) if pop is not None else None,
            "net_credit": round(net_cr), "time_value": round(net_tv), "intrinsic": round(net_intr),
            "tte_days": round(T_e * 365, 2) if T_e else None,
            "scan": [round(lo, 1), round(hi, 1)]}


def list_expiries(u, date):
    """Expiries with STORED backtest data for this date, each {date, monthly}. What-If is
    a backtest so it can only offer expiries it has real chain data for — the collector
    stores the 2 near weeklies (this + next), NOT the far monthlies (that's why the list
    is shorter than the Quick Order's live scrip-master list). Historical (OptChainLake)
    is WEEKLY-only → [] (nearest weekly implied). weekly/monthly tag from the scrip
    master so the user can tell them apart (e.g. '28 Jul · monthly' vs '04 Aug · weekly')."""
    try:
        _, rows = _oc._load_rows(u.upper(), date)
    except Exception:
        rows = None
    if not rows:
        return []
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    monthly = set()
    try:
        import dhan_master as dm
        monthly = {e["date"] for e in dm.list_expiries(u.upper()) if e.get("monthly")}
    except Exception:
        pass
    return [{"date": e, "monthly": e in monthly} for e in exps]


def leg_prices_at(u, date, hm, legs, expiry=None):
    """Per-leg REAL premium AT time `hm` on `date` (the BACKTEST price at that moment,
    NOT the current live LTP) — for the What-If leg rows. Recent dates = collector
    (instant); historical lake reconstruction is too slow for an inline lookup, so it
    returns None there (the user Runs the backtest for those). {legs:[{ltp}], expiry}."""
    u = u.upper()
    try:
        _, rows = _oc._load_rows(u, date)
    except Exception:
        rows = None
    if not rows:
        return None                                  # historical / not in collector → use Run
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if not exps:
        return None
    exp = expiry if (expiry and expiry in exps) else exps[0]
    day = [r for r in rows if r.get("expiry") == exp]
    out = []
    for lg in legs:
        cands = sorted((r for r in day if _f(r.get("strike")) == float(lg["strike"]) and r.get("opt_type") == lg["type"]),
                       key=lambda r: r.get("datetime") or "")
        px = None
        for r in cands:
            if (r.get("datetime") or "")[11:16] >= hm:
                px = _f(r.get("ltp")); break
        if px is None and cands:
            px = _f(cands[-1].get("ltp"))            # after last snapshot → last known
        out.append({"ltp": round(px, 2) if px is not None else None})
    return {"legs": out, "expiry": exp}


def _days_between(d1, d2):
    try:
        a = _dt.datetime.strptime(d1, "%Y-%m-%d").date()
        b = _dt.datetime.strptime(d2, "%Y-%m-%d").date()
        return (b - a).days
    except Exception:
        return 0


def _day_span(d1, d2, cap=90):
    """All calendar dates d1..d2 (inclusive), capped — non-trading days just yield no data."""
    out = []
    try:
        a = _dt.datetime.strptime(d1, "%Y-%m-%d").date()
        b = _dt.datetime.strptime(d2, "%Y-%m-%d").date()
    except Exception:
        return [d1]
    while a <= b and len(out) <= cap:
        out.append(a.isoformat())
        a += _dt.timedelta(days=1)
    return out


def _leg_series_day(u, date, expiry, strike, ot):
    """[(hm, prem)] sorted for ONE leg on ONE day. Collector (strict on the held `expiry`)
    first; only fall back to the lake when no expiry is pinned (lake-origin hold), so a
    held weekly is never silently re-read as a different nearest weekly."""
    try:
        _, rows = _oc._load_rows(u, date)
    except Exception:
        rows = None
    if rows:
        if expiry:
            c = [r for r in rows if r.get("expiry") == expiry
                 and _f(r.get("strike")) == strike and r.get("opt_type") == ot]
        else:
            exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
            exp = exps[0] if exps else None
            c = ([r for r in rows if r.get("expiry") == exp and _f(r.get("strike")) == strike
                  and r.get("opt_type") == ot] if exp else [])
        c = sorted(c, key=lambda r: r.get("datetime") or "")
        out = [((r["datetime"])[11:16], _f(r.get("ltp"))) for r in c if _f(r.get("ltp")) is not None]
        if out:
            return out
        if expiry:            # collector-known held expiry missing this day → no wrong-expiry lake fallback
            return []
    if not expiry:            # lake-origin hold only
        root = _lake_root(u)
        if root:
            r = _lake_series(root, u, date, strike, ot)
            if r:
                series, hms = r
                return [(h, series[h][0]) for h in hms]
    return []


def _downsample(pts, cap=1500):
    if len(pts) <= cap:
        return pts
    vals = [p["mtm"] for p in pts]
    keep = {0, len(pts) - 1, vals.index(max(vals)), vals.index(min(vals))}
    step = len(pts) / float(cap)
    i = 0.0
    while i < len(pts):
        keep.add(int(i)); i += step
    return [pts[k] for k in sorted(keep)]


def _build_mtm(u, entry_date, exit_date, entry_hm, exit_hm, lot, legs, expiry, entry_prems, total, e_t, x_t):
    """Combined position MTM (₹) at every stored minute across the whole hold. SELL leg
    MTM = (entry_prem − prem)·lot ; BUY = (prem − entry_prem)·lot ; forward-filled per leg,
    summed. Endpoints anchored to the run's own entry(₹0)/exit(total). Display-only."""
    if any(p is None for p in entry_prems):
        return []
    days = _day_span(entry_date, exit_date)
    pts = []
    for d in days:
        lo = entry_hm if d == entry_date else "09:15"
        hi = exit_hm if d == exit_date else "15:30"
        leg_maps = [dict(_leg_series_day(u, d, expiry, float(lg["strike"]), lg["type"])) for lg in legs]
        mins = sorted({m for lm in leg_maps for m in lm if lo <= m <= hi})
        last = [None] * len(legs)
        for m in mins:
            mtm = 0.0; ok = True
            for i, lg in enumerate(legs):
                if m in leg_maps[i]:
                    last[i] = leg_maps[i][m]
                p = last[i]
                if p is None:
                    ok = False; break
                sell = lg["side"].upper() == "SELL"
                lq = lot * max(1, int(lg.get("qty") or 1))   # per-leg qty (default 1)
                mtm += (entry_prems[i] - p if sell else p - entry_prems[i]) * lq
            if ok:
                pts.append({"d": d, "t": m, "mtm": round(mtm)})
    if not pts:
        return []
    # anchor both ends to the run's exact entry/exit numbers (start at 0, end at total)
    pts = ([{"d": entry_date, "t": e_t, "mtm": 0}] + pts + [{"d": exit_date, "t": x_t, "mtm": round(total)}])
    return _downsample(pts)


def run(u, date, entry_hm, exit_hm, lots, legs, expiry=None, exit_date=None):
    """legs = [{side:'SELL'|'BUY', strike:float, type:'CE'|'PE'}]. `expiry` = a specific
    stored expiry (default nearest weekly). `exit_date` (default = entry `date`) allows a
    POSITIONAL hold — entry premium from `date`, exit premium from `exit_date` (same held
    expiry+strike). Returns full result."""
    u = u.upper()
    exit_date = exit_date or date
    lot = LOT.get(u, 1) * max(1, int(lots or 1))
    data = (_collector_legs(u, date, entry_hm, exit_hm, legs, expiry=expiry, exit_date=exit_date)
            or _lake_legs(u, date, entry_hm, exit_hm, legs, exit_date=exit_date))
    if not data:
        err = "is date/strike ka data nahi mila (collector ya lake me)."
        if exit_date != date:
            err += " Multi-day hold ke liye held expiry dono din (aur usually expiry ke andar) honi chahiye."
        return {"ok": False, "error": err, "legs": [], "date": date,
                "exit_date": exit_date, "underlying": u}

    src = data["legs"][0]["src"] if data["legs"] else "?"
    out_legs, tot = [], 0.0
    tot_price = tot_iv = 0.0
    e_spot = data["legs"][0]["e_spot"]; x_spot = data["legs"][0]["x_spot"]
    req_e = _hm2m(entry_hm); req_x = _hm2m(exit_hm)   # requested entry/exit (minutes)
    warnings = []
    for lg, d in zip(legs, data["legs"]):
        sell = lg["side"].upper() == "SELL"
        lq = lot * max(1, int(lg.get("qty") or 1))   # per-leg qty (ratio spreads); default 1 = uniform
        dP = (d["x_prem"] - d["e_prem"])           # premium change (per unit)
        pnl = (-dP if sell else dP) * lq
        dS = (d["x_spot"] - d["e_spot"]) if (d["x_spot"] and d["e_spot"]) else 0.0
        dIV = ((d["x_iv"] - d["e_iv"]) if (d["e_iv"] is not None and d["x_iv"] is not None) else 0.0)
        price_dP = ((d["delta"] or 0) * dS + 0.5 * (d["gamma"] or 0) * dS * dS)
        iv_dP = ((d["vega"] or 0) * dIV)
        s = -1 if sell else 1
        pnl_price = s * price_dP * lq
        pnl_iv = s * iv_dP * lq
        tot += pnl; tot_price += pnl_price; tot_iv += pnl_iv
        label = f"{lg['side'].title()} {int(lg['strike'])} {lg['type']}"
        # Did this leg actually open/close at the requested time? The collector stores only
        # ~ATM±10 strikes/minute, so a far-OTM strike isn't in the chain until spot moves
        # into range — its first snapshot lands late (entry) or its last leaves early (exit).
        act_e = _hm2m(d.get("e_t")); act_x = _hm2m(d.get("x_t"))
        entry_off = (req_e is not None and act_e is not None and act_e - req_e > _TIME_TOL_MIN)
        exit_off = (req_x is not None and act_x is not None and req_x - act_x > _TIME_TOL_MIN)
        if entry_off:
            warnings.append(f"{label}: entry {entry_hm} pe chain me nahi tha — pehla real data "
                            f"{d.get('e_t')} ka (strike us waqt captured band se bahar). "
                            f"Entry price is leg ke liye {d.get('e_t')} ka hai, {entry_hm} ka nahi.")
        if exit_off:
            warnings.append(f"{label}: exit {exit_hm} tak data nahi — aakhri available snapshot "
                            f"{d.get('x_t')} ka (strike tab tak band se nikal gaya). "
                            f"Exit price {d.get('x_t')} ka hai, {exit_hm} ka nahi.")
        out_legs.append({
            "label": label,
            "side": lg["side"].upper(), "strike": lg["strike"], "type": lg["type"],
            "entry": round(d["e_prem"], 1), "exit": round(d["x_prem"], 1), "pnl": round(pnl),
            "e_iv": round(d["e_iv"], 1) if d["e_iv"] is not None else None,
            "x_iv": round(d["x_iv"], 1) if d["x_iv"] is not None else None,
            "e_t": d.get("e_t"), "x_t": d.get("x_t"),   # this leg's ACTUAL entry/exit time
            "entry_off": entry_off, "exit_off": exit_off,
        })
    decay = tot - tot_price - tot_iv       # residual = theta + higher-order → three sum to total
    # net credit collected at entry (SELL premium in, BUY premium out) × lot
    credit = sum(((d["e_prem"] if lg["side"].upper() == "SELL" else -d["e_prem"]) * max(1, int(lg.get("qty") or 1)))
                 for lg, d in zip(legs, data["legs"])) * lot
    e_dt = data["legs"][0].get("e_dt") or date
    x_dt = data["legs"][0].get("x_dt") or exit_date
    e_t = data["legs"][0]["e_t"]; x_t = data["legs"][0]["x_t"]
    try:
        mtm = _build_mtm(u, e_dt, x_dt, entry_hm, exit_hm, lot, legs,
                         data.get("expiry"), [d["e_prem"] for d in data["legs"]],
                         tot, e_t, x_t)
    except Exception as _e:
        print("[whatif] mtm build fail:", _e, flush=True)
        mtm = []
    return {
        "mtm": mtm,
        "ok": True, "underlying": u, "date": date, "lots": int(lots or 1),
        "entry_date": e_dt, "exit_date": x_dt, "hold_days": _days_between(e_dt, x_dt),
        "entry_hm": data["legs"][0]["e_t"], "exit_hm": data["legs"][0]["x_t"],
        "expiry": data.get("expiry"), "source": src,
        "spot_e": round(e_spot, 2) if e_spot else None, "spot_x": round(x_spot, 2) if x_spot else None,
        "move": round((x_spot - e_spot)) if (e_spot and x_spot) else None,
        "legs": out_legs, "total": round(tot), "credit": round(credit),
        "decay": round(decay), "price": round(tot_price), "iv": round(tot_iv),
        "warnings": warnings, "req_entry_hm": entry_hm, "req_exit_hm": exit_hm,
    }
