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
def _collector_legs(u, date, entry_hm, exit_hm, legs, expiry=None):
    """Return per-leg entry/exit dicts from the live collector CSV, or None if that
    day/those strikes aren't in the collector lake. `expiry` picks a specific stored
    expiry (real-like simulation); default = nearest weekly."""
    _, rows = _oc._load_rows(u, date)
    if not rows:
        return None
    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if not exps:
        return None
    expiry = expiry if (expiry and expiry in exps) else exps[0]   # chosen expiry, else nearest weekly
    day = [r for r in rows if r.get("expiry") == expiry]

    def at(strike, ot, hm, last=False):
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
        e = at(lg["strike"], lg["type"], entry_hm)
        x = at(lg["strike"], lg["type"], exit_hm, last=(exit_hm >= "15:25"))
        if not e or not x or _f(e.get("ltp")) is None or _f(x.get("ltp")) is None:
            return None                    # incomplete -> let caller try the lake
        out.append({
            "e_prem": _f(e["ltp"]), "x_prem": _f(x["ltp"]),
            "e_spot": _f(e["spot"]), "x_spot": _f(x["spot"]),
            "e_iv": _f(e.get("iv")), "x_iv": _f(x.get("iv")),
            "delta": _f(e.get("delta")), "gamma": _f(e.get("gamma")), "vega": _f(e.get("vega")),
            "e_t": (e["datetime"])[11:16], "x_t": (x["datetime"])[11:16], "src": "collector",
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


def _lake_leg(root, u, date, strike, ot, entry_hm, exit_hm):
    """Held-strike reconstruction — but only read the OFFSET files near the target
    strike (from the day's spot range), not all 21. Keeps rows whose actual
    `strike`==target on `date`; returns entry & exit (prem, spot, ts)."""
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
    hms = sorted(series)

    def pick(hm, last):
        if last:
            return series[hms[-1]], hms[-1]
        for h in hms:
            if h >= hm:
                return series[h], h
        return series[hms[-1]], hms[-1]

    (e_prem, e_spot, e_ts), e_t = pick(entry_hm, False)
    (x_prem, x_spot, x_ts), x_t = pick(exit_hm, exit_hm >= "15:25")
    return {"e_prem": e_prem, "x_prem": x_prem, "e_spot": e_spot, "x_spot": x_spot,
            "e_ts": e_ts, "x_ts": x_ts, "e_t": e_t, "x_t": x_t}


def _lake_legs(u, date, entry_hm, exit_hm, legs):
    root = _lake_root(u)
    if not root:
        return None
    m = _bsmod()
    import pandas as pd
    out = []
    for lg in legs:
        r = _lake_leg(root, u, date, float(lg["strike"]), lg["type"], entry_hm, exit_hm)
        if not r or r["e_prem"] is None or r["x_prem"] is None:
            return None
        # BS-derive IV + greeks (lake has no stored greeks)
        d = None; e_iv = None; x_iv = None
        if m and r["e_spot"] and r["x_spot"]:
            T_e = m.tte_years(pd.Timestamp(r["e_ts"], unit="s", tz="Asia/Kolkata"))
            T_x = m.tte_years(pd.Timestamp(r["x_ts"], unit="s", tz="Asia/Kolkata"))
            try:
                se = m.implied_vol(r["e_prem"], r["e_spot"], float(lg["strike"]), T_e, _R, lg["type"])
                sx = m.implied_vol(r["x_prem"], r["x_spot"], float(lg["strike"]), T_x, _R, lg["type"])
                e_iv, x_iv = se * 100.0, sx * 100.0
                d = _greeks_bs(r["e_spot"], float(lg["strike"]), T_e, se, lg["type"])
            except Exception:
                pass
        out.append({
            "e_prem": r["e_prem"], "x_prem": r["x_prem"], "e_spot": r["e_spot"], "x_spot": r["x_spot"],
            "e_iv": e_iv, "x_iv": x_iv,
            "delta": d["delta"] if d else None, "gamma": d["gamma"] if d else None,
            "vega": d["vega"] if d else None,
            "e_t": r["e_t"], "x_t": r["x_t"], "src": "lake",
        })
    return {"expiry": None, "legs": out}


# ---------------------------------------------------------------- public
def available_dates(u):
    """Dates offering data — collector (recent) ∪ lake (historical), newest first."""
    ds = set(__import__("gex_profile").available_dates(u))  # collector days (reuse)
    return sorted(ds, reverse=True)


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


def run(u, date, entry_hm, exit_hm, lots, legs, expiry=None):
    """legs = [{side:'SELL'|'BUY', strike:float, type:'CE'|'PE'}]. `expiry` = a specific
    stored expiry (default nearest weekly). Returns full result."""
    u = u.upper()
    lot = LOT.get(u, 1) * max(1, int(lots or 1))
    data = _collector_legs(u, date, entry_hm, exit_hm, legs, expiry=expiry) or _lake_legs(u, date, entry_hm, exit_hm, legs)
    if not data:
        return {"ok": False, "error": "is date/strike ka data nahi mila (collector ya lake me).",
                "legs": [], "date": date, "underlying": u}

    src = data["legs"][0]["src"] if data["legs"] else "?"
    out_legs, tot = [], 0.0
    tot_price = tot_iv = 0.0
    e_spot = data["legs"][0]["e_spot"]; x_spot = data["legs"][0]["x_spot"]
    for lg, d in zip(legs, data["legs"]):
        sell = lg["side"].upper() == "SELL"
        dP = (d["x_prem"] - d["e_prem"])           # premium change (per unit)
        pnl = (-dP if sell else dP) * lot
        dS = (d["x_spot"] - d["e_spot"]) if (d["x_spot"] and d["e_spot"]) else 0.0
        dIV = ((d["x_iv"] - d["e_iv"]) if (d["e_iv"] is not None and d["x_iv"] is not None) else 0.0)
        price_dP = ((d["delta"] or 0) * dS + 0.5 * (d["gamma"] or 0) * dS * dS)
        iv_dP = ((d["vega"] or 0) * dIV)
        s = -1 if sell else 1
        pnl_price = s * price_dP * lot
        pnl_iv = s * iv_dP * lot
        tot += pnl; tot_price += pnl_price; tot_iv += pnl_iv
        out_legs.append({
            "label": f"{lg['side'].title()} {int(lg['strike'])} {lg['type']}",
            "side": lg["side"].upper(), "strike": lg["strike"], "type": lg["type"],
            "entry": round(d["e_prem"], 1), "exit": round(d["x_prem"], 1), "pnl": round(pnl),
            "e_iv": round(d["e_iv"], 1) if d["e_iv"] is not None else None,
            "x_iv": round(d["x_iv"], 1) if d["x_iv"] is not None else None,
        })
    decay = tot - tot_price - tot_iv       # residual = theta + higher-order → three sum to total
    # net credit collected at entry (SELL premium in, BUY premium out) × lot
    credit = sum(((d["e_prem"] if lg["side"].upper() == "SELL" else -d["e_prem"]))
                 for lg, d in zip(legs, data["legs"])) * lot
    return {
        "ok": True, "underlying": u, "date": date, "lots": int(lots or 1),
        "entry_hm": data["legs"][0]["e_t"], "exit_hm": data["legs"][0]["x_t"],
        "expiry": data.get("expiry"), "source": src,
        "spot_e": round(e_spot, 2) if e_spot else None, "spot_x": round(x_spot, 2) if x_spot else None,
        "move": round((x_spot - e_spot)) if (e_spot and x_spot) else None,
        "legs": out_legs, "total": round(tot), "credit": round(credit),
        "decay": round(decay), "price": round(tot_price), "iv": round(tot_iv),
    }
