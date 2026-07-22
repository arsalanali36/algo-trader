"""SINGLE SOURCE OF TRUTH — ORB (opening-range breakout) signal.

The backtest engine (scratch/nifty_trend/intraday_engine.py `tod_orb`) and the live
trader (strategies/live/orb_trader.py) BOTH call this one function, so their signals
match by construction — no more silent drift (OR-boundary `<` vs `<=`, previous-bar vs
current-bar ATR in the crossover) that made live diverge ~25% from the validated
backtest. This is step 1 of the "backtest == live" unification (pilot: orb_v1).

Rules = the validated `tod_orb`, reproduced EXACTLY:
  • Opening range (OR) per day = bars whose time <= 09:15 + or_min.
  • Threshold: orh = OR_high + orb_k*ATR ,  orl = OR_low − orb_k*ATR
      (ATR = Wilder ATR(atr_period), CURRENT bar — same value on both sides of the cross).
  • Entry only inside the window [h0:00 , h1:00].
  • LONG  = close crosses ABOVE orh  (close > orh AND prev_close <= orh)
  • SHORT = close crosses BELOW orl  (close < orl AND prev_close >= orl)

Pure/vectorised: takes pandas Series, returns (long, short) boolean Series. Column-name
agnostic — each caller passes its own dt/high/low/close Series (backtest uses
Datetime/High/Low/Close, live uses time/high/low/close).
"""
import datetime as _dt
import numpy as np
import pandas as pd


def wilder_atr(high, low, close, n=14):
    """Wilder RMA of True Range — identical to engine.atr / _CHARTING.wilder_atr."""
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def orb_signals(dt, high, low, close, params):
    """Vectorised ORB long/short over a (possibly multi-day) bar series.

    dt/high/low/close : pandas Series (aligned index). params: or_min, orb_k,
    atr_period, h0, h1 (window hours). Returns (long, short) bool Series."""
    p = params
    orm = int(p.get("or_min", 30))
    k = float(p.get("orb_k", 1.0))
    n = int(p.get("atr_period", 14))
    h0 = int(p.get("h0", 11))
    h1 = int(p.get("h1", 14))

    dts = pd.to_datetime(dt)
    day = dts.dt.date
    tt = dts.dt.time
    a = wilder_atr(high, low, close, n)

    or_end = (_dt.datetime.combine(_dt.date.today(), _dt.time(9, 15))
              + _dt.timedelta(minutes=orm)).time()
    cutoff = tt <= or_end                                  # OR = first or_min mins (inclusive)
    orh = high.where(cutoff).groupby(day).transform("max") + k * a
    orl = low.where(cutoff).groupby(day).transform("min") - k * a

    win = (tt >= _dt.time(h0, 0)) & (tt <= _dt.time(h1, 0))
    after = (~cutoff) & win
    long = after & (close > orh) & (close.shift(1) <= orh)
    short = after & (close < orl) & (close.shift(1) >= orl)
    return long.fillna(False), short.fillna(False)


def st_dir(high, low, close, period, mult):
    """Supertrend DIRECTION (+1/-1) — exact copy of the backtest intraday_engine.supertrend
    so orb_st matches on both sides. Wilder ATR(period), Supertrend band = hl2 ∓ mult*ATR."""
    a = wilder_atr(high, low, close, period)
    hl2 = (high + low) / 2.0
    up = (hl2 - mult * a).values
    dn = (hl2 + mult * a).values
    c = np.asarray(close.values, dtype=float)
    n = len(c)
    dir_ = np.ones(n)
    fup, fdn = up.copy(), dn.copy()
    for i in range(1, n):
        fup[i] = max(up[i], fup[i - 1]) if c[i - 1] > fup[i - 1] else up[i]
        fdn[i] = min(dn[i], fdn[i - 1]) if c[i - 1] < fdn[i - 1] else dn[i]
        if c[i] > fdn[i - 1]:
            dir_[i] = 1
        elif c[i] < fup[i - 1]:
            dir_[i] = -1
        else:
            dir_[i] = dir_[i - 1]
    return pd.Series(dir_, index=close.index)


def orb_st_signals(dt, high, low, close, params):
    """ORB breakout CONFIRMED by Supertrend direction (design 'orb_st'). No time window —
    entries any time AFTER the opening range, in the Supertrend's direction. Same OR /
    crossover rules as orb_signals (current-bar orh both sides). Params: or_min, orb_k,
    atr_period, st_period, st_mult."""
    p = params
    orm = int(p.get("or_min", 30))
    k = float(p.get("orb_k", 1.0))
    n = int(p.get("atr_period", 14))
    stp = int(p.get("st_period", 10))
    stm = float(p.get("st_mult", 3.0))
    dts = pd.to_datetime(dt)
    day = dts.dt.date
    tt = dts.dt.time
    a = wilder_atr(high, low, close, n)
    or_end = (_dt.datetime.combine(_dt.date.today(), _dt.time(9, 15))
              + _dt.timedelta(minutes=orm)).time()
    cutoff = tt <= or_end
    orh = high.where(cutoff).groupby(day).transform("max") + k * a
    orl = low.where(cutoff).groupby(day).transform("min") - k * a
    d_ = st_dir(high, low, close, stp, stm)
    after = ~cutoff
    long = after & (close > orh) & (close.shift(1) <= orh) & (d_ == 1)
    short = after & (close < orl) & (close.shift(1) >= orl) & (d_ == -1)
    return long.fillna(False), short.fillna(False)


def orb_st_signal_last(df, params, *, dt_col="time", hi="high", lo="low", cl="close"):
    """Point-in-time orb_st for the LIVE trader — signal for the last CLOSED bar."""
    if df is None or len(df) < 3:
        return None
    d = df.reset_index(drop=True)
    long, short = orb_st_signals(d[dt_col], d[hi], d[lo], d[cl], params)
    i = len(d) - 2
    if i < 1:
        return None
    if bool(long.iloc[i]):
        return "long"
    if bool(short.iloc[i]):
        return "short"
    return None


def orb_signal_last(df, params, *, dt_col="time", hi="high", lo="low", cl="close"):
    """Point-in-time helper for the LIVE trader. Given a continuous multi-day df, return
    the signal for the LAST CLOSED bar (index -2; last row is the still-forming bar) —
    'long' / 'short' / None. Uses the exact same `orb_signals` as the backtest, so a live
    entry fires iff the backtest would have fired on that bar.

    Only reports a signal if the last-closed bar is TODAY (live never acts on stale bars)."""
    if df is None or len(df) < 3:
        return None
    d = df.reset_index(drop=True)
    long, short = orb_signals(d[dt_col], d[hi], d[lo], d[cl], params)
    i = len(d) - 2                                          # last CLOSED bar
    if i < 1:
        return None
    if bool(long.iloc[i]):
        return "long"
    if bool(short.iloc[i]):
        return "short"
    return None
