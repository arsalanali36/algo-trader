"""SINGLE SOURCE — Chain-Zone signal (user's "Ars_Auto_Rev_Chain").

The backtest engine (intraday_engine._chain_zone_signals) AND the live trader
(04_chainzone_trader) both call this ONE implementation, so their signals match by
construction. EXACT reproduction of the validated intraday_engine logic
(_daily_levels + _candle_patterns + _chain_zone_signals), column-agnostic (takes Series).

Levels come from the PREVIOUS completed day (pivots + PDH/PDL + chain of higher-highs /
lower-lows). A key candle (engulf / hammer / harami) that touches a level seeds a zone;
a same-colour breakout out of that zone within `zone_age` bars = the entry.
"""
import numpy as np
import pandas as pd


def candle_patterns(open_, high, low, close, min_body=0.5, wick_ratio=2.5):
    """Vectorised bullish/bearish key-candle flags (engulf/hammer/harami), Pine parity."""
    O = np.asarray(open_, dtype=float)
    H = np.asarray(high, dtype=float)
    L = np.asarray(low, dtype=float)
    C = np.asarray(close, dtype=float)
    body = np.abs(C - O)
    up_wick = H - np.maximum(O, C)
    lo_wick = np.minimum(O, C) - L
    green = C > O
    red = C < O
    grn_ham = green & (body >= min_body) & (lo_wick >= wick_ratio * body) & (up_wick <= body)
    red_ham = red & (body >= min_body) & (lo_wick >= wick_ratio * body) & (up_wick <= body)
    inv_red = red & (body >= min_body) & (up_wick >= wick_ratio * body) & (lo_wick <= body)
    Op, Cp = np.roll(O, 1), np.roll(C, 1)
    prev_red, prev_grn = Cp < Op, Cp > Op
    body_p = np.abs(Cp - Op)
    bull_eng = green & prev_red & (body_p >= min_body) & (O <= Cp) & (C >= Op)
    bear_eng = red & prev_grn & (body_p >= min_body) & (O >= Cp) & (C <= Op)
    lo_c, hi_c = np.minimum(O, C), np.maximum(O, C)
    bull_har = green & prev_red & (lo_c >= Cp) & (hi_c <= Op)
    bear_har = red & prev_grn & (lo_c >= Op) & (hi_c <= Cp)
    bull_har[0] = bear_eng[0] = bull_eng[0] = False
    bearish = bear_eng | bear_har | inv_red | red_ham
    bullish = bull_eng | bull_har | grn_ham
    return bullish, bearish


def daily_levels(dt, high, low, close, lookback=20, max_jump=10.0):
    """date -> dict(res, sup, neutral). Levels from the PREVIOUS completed day (no lookahead)."""
    day = pd.to_datetime(dt).dt.date
    d = pd.DataFrame({"day": day.values, "H": np.asarray(high, float),
                      "L": np.asarray(low, float), "C": np.asarray(close, float)})
    daily = (d.groupby("day").agg(H=("H", "max"), L=("L", "min"), C=("C", "last")).reset_index())
    dates = list(daily["day"].values)
    Hs, Ls, Cs = daily.H.values, daily.L.values, daily.C.values
    out = {}
    for j in range(len(dates)):
        if j == 0:
            out[dates[j]] = None
            continue
        ph, pl, pc = Hs[j - 1], Ls[j - 1], Cs[j - 1]
        P = (ph + pl + pc) / 3.0
        rng = ph - pl
        R1, S1 = 2 * P - pl, 2 * P - ph
        R2, S2 = P + rng, P - rng
        R3, S3 = ph + 2 * (P - pl), pl - 2 * (ph - P)
        R4, S4 = R3 + rng, S3 - rng
        R5, S5 = R4 + rng, S4 - rng
        res = [ph, R1, R2, R3, R4, R5]
        sup = [pl, S1, S2, S3, S4, S5]
        thr = ph
        for k in range(2, min(lookback, j) + 1):
            hh = Hs[j - k]
            if hh > thr and (hh - thr) / thr * 100.0 <= max_jump:
                res.append(hh); thr = hh
        thr = pl
        for k in range(2, min(lookback, j) + 1):
            ll = Ls[j - k]
            if ll < thr and (thr - ll) / thr * 100.0 <= max_jump:
                sup.append(ll); thr = ll
        out[dates[j]] = dict(res=res, sup=sup, neutral=[P, pc])
    return out


def chain_zone_signals(dt, open_, high, low, close, params):
    """Vectorised chain-zone long/short entry arrays — EXACT intraday_engine logic."""
    p = params
    lookback = int(p.get("chain_lookback", 20))
    max_jump = float(p.get("max_jump", 10.0))
    tol = float(p.get("touch_tol", 5.0))
    max_age = int(p.get("zone_age", 2))
    max_cs = float(p.get("max_cs", 40.0))
    hawa = bool(p.get("hawa", False))
    hawa_k = int(p.get("hawa_k", 3))
    levels = daily_levels(dt, high, low, close, lookback, max_jump)
    O = np.asarray(open_, float); H = np.asarray(high, float)
    L = np.asarray(low, float); C = np.asarray(close, float)
    day = pd.to_datetime(dt).dt.date.values
    n = len(C)
    bull, bear = candle_patterns(open_, high, low, close)
    long_e = np.zeros(n, dtype=bool)
    short_e = np.zeros(n, dtype=bool)
    red_zone = None
    green_zone = None
    last_res_bar = -10 ** 9
    last_sup_bar = -10 ** 9
    for i in range(1, n):
        lv = levels.get(day[i])
        if lv is None:
            continue
        lo, hi = L[i] - tol, H[i] + tol
        t_res = any(lo <= x <= hi for x in lv["res"])
        t_sup = any(lo <= x <= hi for x in lv["sup"])
        t_neu = any(lo <= x <= hi for x in lv["neutral"])
        if t_res:
            last_res_bar = i
        if t_sup:
            last_sup_bar = i
        at_res = t_res or t_neu or (hawa and i - last_res_bar <= hawa_k)
        at_sup = t_sup or t_neu or (hawa and i - last_sup_bar <= hawa_k)
        if bear[i] and at_res and not t_sup:
            red_zone = dict(lower=L[i], upper=H[i], bar=i)
        if bull[i] and at_sup and not t_res:
            green_zone = dict(lower=L[i], upper=H[i], bar=i)
        cs = H[i] - L[i]
        if (red_zone is not None and i > red_zone["bar"]
                and i - red_zone["bar"] <= max_age and C[i] < red_zone["lower"]
                and C[i] < O[i] and C[i - 1] < O[i - 1] and cs <= max_cs):
            short_e[i] = True
            red_zone = None
        if (green_zone is not None and i > green_zone["bar"]
                and i - green_zone["bar"] <= max_age and C[i] > green_zone["upper"]
                and C[i] > O[i] and C[i - 1] > O[i - 1] and cs <= max_cs):
            long_e[i] = True
            green_zone = None
    return long_e, short_e


def chain_zone_signal_last(df, params, *, dt_col="time", op="open", hi="high", lo="low", cl="close"):
    """Point-in-time chain-zone for the LIVE trader — signal for the last CLOSED bar."""
    if df is None or len(df) < 3:
        return None
    d = df.reset_index(drop=True)
    long_e, short_e = chain_zone_signals(d[dt_col], d[op], d[hi], d[lo], d[cl], params)
    i = len(d) - 2
    if i < 1:
        return None
    if bool(long_e[i]):
        return "long"
    if bool(short_e[i]):
        return "short"
    return None
