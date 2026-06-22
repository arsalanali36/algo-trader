"""
vwap_ema_failure.py — VWAP-EMA Failure Reversal Strategy (Mukul's strategy)

Pine counterpart: _PINE/v6.pine ("VWAP-EMA Failure Reversal Strategy")
Source video: https://www.youtube.com/watch?v=eOEYYTrlEG0
Tested decent on TCS, POLYCAB, RIL — 5-min timeframe.

Logic (per the video):
  Regime: EMA(10) vs day's VWAP tells you which side buyers/sellers control.
  SHORT trigger — EMA already below VWAP (both this bar and the prior bar),
                  AND price closes below EMA this bar having been >= EMA
                  the prior bar ("failure of buyers" confirmation candle).
  LONG trigger  — mirror image (EMA above VWAP, price closes above EMA
                  having failed below it the bar before — "failure of sellers").
  Daily trend filter — skip longs on a day trading below yesterday's close,
                        skip shorts on a day trading above it (per video's
                        discretionary rule: don't fight the day's own bias).
  SL — placed beyond the trigger candle's high/low + a buffer.
  Targets — R-multiples of the entry risk (1R/2R/3R), partial exits
            40%/30%/30% by default (rest at T3).

This module owns ALL position/SL/target state itself (unlike the simple
evaluate()->BUY/SELL/EXIT strategy plugins elsewhere in this project) because
partial profit-booking needs to track remaining qty% across several bars —
a single enum return value can't carry that. backtest_engine.py's
_run_vwap_ema() calls `backtest(df, cfg)` directly with a resampled,
volume-included OHLCV dataframe and gets back a flat trade list, same shape
every other strategy's backtest path already uses.

Every strategy in this project force-exits by 3:15 PM IST regardless of its
own configured session window — backtest_engine.py applies that cutoff
itself (EXIT_HM) on top of whatever this module returns, so this module's
own `session_end` only governs the Pine script's "session window" used by
the entry/trend filters, not the hard EOD square-off.

Config keys (cfg dict, all optional — defaults match the Pine inputs):
  symbol               : str   — which equity this run is for (TCS/POLYCAB/RELIANCE/...)
  timeframe            : str   — "5m" etc (handled by caller's resample, not here)
  ema_len              : int   (10)
  max_ema_vwap_gap     : float (0 = off) — skip entries where |EMA-VWAP| > this many points
  use_session_filter   : bool  (True)
  session_start        : "HH:MM" (09:15)
  session_end          : "HH:MM" (15:30)  — entries only; EOD square-off is separate (3:15 hard rule)
  sl_buffer_points     : float (2.0)
  r1, r2, r3           : float (1.0, 2.0, 3.0) — R-multiple targets
  max_sl_percent       : float (2.5) — skip trade if SL would be wider than this % of price
  use_partial_exits    : bool  (True)
  partial1_pct         : int   (40) — qty% closed at T1
  partial2_pct         : int   (30) — qty% closed at T2 (remainder closed at T3)
  use_daily_trend_filter : bool (True)
"""

import datetime

import pandas as pd


def _parse_hm(s, default):
    try:
        h, m = s.split(":")
        return datetime.time(int(h), int(m))
    except Exception:
        return default


def _daily_vwap(df):
    """ta.vwap(hlc3) — cumulative typical-price*volume / cumulative volume,
    resetting at the start of each calendar day. Falls back to the plain
    typical price for a day if volume is missing/zero (avoids div-by-zero;
    happens on index-style feeds that don't carry real volume)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].fillna(0.0)
    pv = tp * vol
    day = df["time"].dt.date
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = vol.groupby(day).cumsum()
    vwap = cum_pv / cum_vol.replace(0, pd.NA)
    return vwap.fillna(tp)


def backtest(df, cfg):
    """df: columns time/open/high/low/close/volume, sorted, possibly multi-day.
    Returns list of {entry_time, entry_price, side, exit_time, exit_price, exit_reason}."""
    if df.empty:
        return []

    ema_len      = int(cfg.get("ema_len", 10))
    max_gap      = float(cfg.get("max_ema_vwap_gap", 0.0))
    use_session  = cfg.get("use_session_filter", True)
    sess_start   = _parse_hm(cfg.get("session_start", "09:15"), datetime.time(9, 15))
    sess_end     = _parse_hm(cfg.get("session_end", "15:30"), datetime.time(15, 30))
    sl_buffer    = float(cfg.get("sl_buffer_points", 2.0))
    r1           = float(cfg.get("r1", 1.0))
    r2           = float(cfg.get("r2", 2.0))
    r3           = float(cfg.get("r3", 3.0))
    max_sl_pct   = float(cfg.get("max_sl_percent", 2.5))
    use_partials = cfg.get("use_partial_exits", True)
    p1_pct       = float(cfg.get("partial1_pct", 40))
    p2_pct       = float(cfg.get("partial2_pct", 30))
    use_trend    = cfg.get("use_daily_trend_filter", True)

    df = df.reset_index(drop=True).copy()
    if "volume" not in df.columns:
        df["volume"] = 0.0

    close, high, low = df["close"], df["high"], df["low"]
    ema10 = close.ewm(span=ema_len, adjust=False).mean()
    vwap = _daily_vwap(df)

    day = df["time"].dt.date
    daily_agg = df.groupby(day).agg(d_high=("high", "max"), d_low=("low", "min"),
                                     d_close=("close", "last"))
    daily_agg["prev_close"] = daily_agg["d_close"].shift(1)
    daily_agg["prev_high"]  = daily_agg["d_high"].shift(1)
    daily_agg["prev_low"]   = daily_agg["d_low"].shift(1)
    # previous day's VWAP = that day's own last VWAP value
    last_vwap_per_day = vwap.groupby(day).last()
    daily_agg["prev_vwap"] = last_vwap_per_day.shift(1)

    prev_close_s = day.map(daily_agg["prev_close"])
    prev_vwap_s  = day.map(daily_agg["prev_vwap"])

    ema_below_vwap = ema10 < vwap
    ema_above_vwap = ema10 > vwap

    short_trigger = (ema_below_vwap.shift(1, fill_value=False) & ema_below_vwap &
                      (close < ema10) & (close.shift(1) >= ema10.shift(1)))
    long_trigger = (ema_above_vwap.shift(1, fill_value=False) & ema_above_vwap &
                     (close > ema10) & (close.shift(1) <= ema10.shift(1)))

    gap_ok = (max_gap <= 0) | ((ema10 - vwap).abs() <= max_gap)

    in_session = pd.Series(True, index=df.index)
    if use_session:
        t_only = df["time"].dt.time
        in_session = (t_only >= sess_start) & (t_only < sess_end)

    trend_up = (close > prev_close_s) if use_trend else pd.Series(True, index=df.index)
    trend_down = (close < prev_close_s) if use_trend else pd.Series(True, index=df.index)
    trend_up = trend_up.fillna(True)
    trend_down = trend_down.fillna(True)

    trades = []
    pos = 0          # 0 flat, 1 long, -1 short
    entry_time = entry_price = active_sl = active_t1 = active_t2 = active_t3 = None
    partial1_done = partial2_done = False
    remaining_pct = 100.0
    weighted_exit = 0.0

    def _close_trade(t, side, reason):
        nonlocal trades, weighted_exit
        trades.append({"entry_time": entry_time, "entry_price": entry_price, "side": side,
                        "exit_time": t, "exit_price": round(weighted_exit, 2), "exit_reason": reason})

    # Every CODE3B strategy force-exits by 3:15 PM IST with no re-entry —
    # standing project rule (no overnight/gap-up exposure, any instrument).
    # Checked first, before SL/target, exactly like RSI/EMA's backtest path.
    eod_cutoff = datetime.time(15, 15)

    n = len(df)
    for i in range(ema_len + 5, n):
        t = df["time"].iat[i]
        o, h, l, c = df["open"].iat[i], high.iat[i], low.iat[i], close.iat[i]
        pos_at_start = pos

        if pos_at_start != 0 and t.time() >= eod_cutoff:
            weighted_exit += c * remaining_pct / 100.0
            _close_trade(t, "Long" if pos_at_start == 1 else "Short", "3:15 Daily Exit")
            pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 0, False, False, 100.0, 0.0
            continue

        if pos_at_start == 1:
            if l <= active_sl:
                weighted_exit = active_sl
                _close_trade(t, "Long", "SL")
                pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 0, False, False, 100.0, 0.0
            elif use_partials:
                if not partial1_done and h >= active_t1:
                    weighted_exit += active_t1 * p1_pct / 100.0
                    remaining_pct -= p1_pct
                    partial1_done = True
                if partial1_done and not partial2_done and h >= active_t2:
                    weighted_exit += active_t2 * p2_pct / 100.0
                    remaining_pct -= p2_pct
                    partial2_done = True
                if partial1_done and partial2_done and h >= active_t3:
                    weighted_exit += active_t3 * remaining_pct / 100.0
                    _close_trade(t, "Long", "T3")
                    pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 0, False, False, 100.0, 0.0
            elif h >= active_t3:
                weighted_exit = active_t3
                _close_trade(t, "Long", "TP")
                pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 0, False, False, 100.0, 0.0

        elif pos_at_start == -1:
            if h >= active_sl:
                weighted_exit = active_sl
                _close_trade(t, "Short", "SL")
                pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 0, False, False, 100.0, 0.0
            elif use_partials:
                if not partial1_done and l <= active_t1:
                    weighted_exit += active_t1 * p1_pct / 100.0
                    remaining_pct -= p1_pct
                    partial1_done = True
                if partial1_done and not partial2_done and l <= active_t2:
                    weighted_exit += active_t2 * p2_pct / 100.0
                    remaining_pct -= p2_pct
                    partial2_done = True
                if partial1_done and partial2_done and l <= active_t3:
                    weighted_exit += active_t3 * remaining_pct / 100.0
                    _close_trade(t, "Short", "T3")
                    pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 0, False, False, 100.0, 0.0
            elif l <= active_t3:
                weighted_exit = active_t3
                _close_trade(t, "Short", "TP")
                pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 0, False, False, 100.0, 0.0

        if pos_at_start == 0 and t.time() < eod_cutoff:
            can_short = (short_trigger.iat[i] and gap_ok.iat[i] and in_session.iat[i] and
                         trend_down.iat[i])
            can_long = (long_trigger.iat[i] and gap_ok.iat[i] and in_session.iat[i] and
                        trend_up.iat[i])

            if can_short:
                sl = h + sl_buffer
                risk = sl - c
                sl_pct = (risk / c) * 100 if c else 0
                if max_sl_pct <= 0 or sl_pct <= max_sl_pct:
                    entry_time, entry_price, active_sl = t, c, sl
                    active_t1, active_t2, active_t3 = c - risk * r1, c - risk * r2, c - risk * r3
                    pos, partial1_done, partial2_done, remaining_pct, weighted_exit = -1, False, False, 100.0, 0.0
            elif can_long:
                sl = l - sl_buffer
                risk = c - sl
                sl_pct = (risk / c) * 100 if c else 0
                if max_sl_pct <= 0 or sl_pct <= max_sl_pct:
                    entry_time, entry_price, active_sl = t, c, sl
                    active_t1, active_t2, active_t3 = c + risk * r1, c + risk * r2, c + risk * r3
                    pos, partial1_done, partial2_done, remaining_pct, weighted_exit = 1, False, False, 100.0, 0.0

    if pos != 0:
        last = df.iloc[-1]
        weighted_exit += float(last["close"]) * remaining_pct / 100.0
        _close_trade(last["time"], "Long" if pos == 1 else "Short", "EOD")

    return trades
