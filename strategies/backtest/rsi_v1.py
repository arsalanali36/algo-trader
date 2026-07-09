"""
rsi_v1.py — RSI Simple Strategy (scratch build, 2026-06-19)

Pine counterpart: _PINE/rsi_v1.pine

Logic:
  CE (BUY)  — RSI crosses UP through oversold (default 30)   → bounce expected
  PE (SELL) — RSI crosses DOWN through overbought (default 70) → reversal expected
  EXIT      — RSI returns to mid zone (default 50)

Config keys (from nifty_config.json strategy block):
  rsi_period  : int   (default 14)
  overbought  : float (default 70)
  oversold    : float (default 30)
  rsi_exit    : float (default 50)

Interface: evaluate(df, cfg, pos) → 'BUY' | 'SELL' | 'EXIT' | None
  BUY  = open CE (bullish)
  SELL = open PE (bearish)
  EXIT = close current position
"""

import pandas as pd

# RSI ka SINGLE source of truth (Rule 6B / ADR-002) — wahi Wilder formula
# (com=period-1, Pine ta.rsi match) jo pehle yahin inline thi.
from _CHARTING.indicators import wilder_rsi as _rsi


def evaluate(df: pd.DataFrame, cfg: dict, pos):
    period   = int(cfg.get("rsi_period", 14))
    ob       = float(cfg.get("overbought", 70))
    os_      = float(cfg.get("oversold",   30))
    exit_mid = float(cfg.get("rsi_exit",   50))

    if len(df) < period + 5:
        return None

    rsi = _rsi(df["close"], period)

    # Use second-last bar (confirmed, same as Pine's bar_index[-1])
    cur = rsi.iloc[-2]
    prv = rsi.iloc[-3]

    # ── Exit first ───────────────────────────────────────────────────────────
    if pos == "LONG"  and cur >= exit_mid:
        return "EXIT"
    if pos == "SHORT" and cur <= exit_mid:
        return "EXIT"
    if pos is not None:
        return None   # in position, no new entry

    # ── Entry (no open position) ──────────────────────────────────────────────
    cross_up   = prv <= os_ and cur > os_   # ta.crossover(rsi, os_)
    cross_down = prv >= ob  and cur < ob    # ta.crossunder(rsi, ob)

    if cross_up:
        return "BUY"    # → buy CE
    if cross_down:
        return "SELL"   # → buy PE

    return None


# _rsi = wilder_rsi (imported at top from _CHARTING/indicators.py —
# Task 2 consolidation 2026-07-07; formula bit-for-bit wahi hai jo yahan inline thi)
