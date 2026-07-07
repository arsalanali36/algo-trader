"""
_CHARTING/indicators.py — SINGLE SOURCE OF TRUTH for indicator calculations.

(Task 2 / Rule 6B consolidation, 2026-07-07 — see _ADR/ADR-002-indicator-source-of-truth.md)

Two tiers:

1. CANONICAL PINE-MATCHED FORMULAS (pure pandas, zero external deps) —
   `wilder_rsi()`, `wilder_atr()`, `pine_ema()`. These are what LIVE TRADES
   are decided on (validated against TradingView at 90.2% exact — see
   ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md). Strategy files import THESE.
   The chart registry below also uses them, so signal and chart can never
   diverge (the old bug family: strategy had Wilder RSI, chart had the
   `ta`-library RSI — two "truths").

2. `ta`-LIBRARY EXTRAS (SMA/VWAP/BBANDS) — chart-only indicators with no
   strategy duplicate. `ta` is imported LAZILY inside each function so that
   live trader processes importing this module for tier-1 functions never
   need the `ta` package installed (VPS trader env may not have it).

INDICATOR_REGISTRY drives the dashboard's "Add Indicator" dropdown
(see _CHARTING/plot_spec.py + templates/backtest_chart.html) — adding a new
*standard* indicator to a chart should mean adding one registry entry here,
not writing new per-strategy plotting code.

Rule 6B: kabhi bhi in formulas ki copy kisi strategy/backtest file mein mat
likho — yahin se import karo. architecture_audit.py isko enforce karta hai.
"""

import pandas as pd


# ═══════════════════════════════════════════════════════════════════════════
#  TIER 1 — CANONICAL PINE-MATCHED FORMULAS (pure pandas — LIVE TRADES use these)
# ═══════════════════════════════════════════════════════════════════════════

def wilder_rsi(series, period=14):
    """
    Wilder RSI — Pine ke ta.rsi() se exactly match karta hai.

    Wilder smoothing formula:
      alpha = 1/period  →  EWM com = period - 1
    (agar span=period use karo toh Pine se values alag aayengi)

    Consolidated from: _TRADERS/01_rsi_v1.py `_compute_rsi`,
    _TOOLS/backtest_engine.py `_compute_rsi`, strategies/rsi_v1.py `_rsi`
    (all three were textually identical — this IS that formula, moved here).
    """
    delta = series.diff()
    gain  = delta.clip(lower=0)          # sirf upar wale moves
    loss  = (-delta).clip(lower=0)       # sirf neeche wale moves (positive rakho)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, float("inf"))   # loss=0 → RSI=100
    return 100 - (100 / (1 + rs))


def wilder_atr(df, period=14):
    """
    Wilder ATR — Pine ta.atr uses Wilder's RMA (alpha = 1/period), NOT EMA span.
    Needs df with high/low/close columns.

    Consolidated from: _TRADERS/range_trader.py `compute_atr` (verbatim).
    """
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


def pine_ema(series, period=20):
    """
    Pine ta.ema match — span-style alpha = 2/(period+1), adjust=False.

    Consolidated from the inline one-liners in _TRADERS/nifty_ema_trader.py
    `compute_signal` and _TOOLS/backtest_engine.py `_run_ema` (verbatim).
    """
    return series.ewm(span=int(period), adjust=False).mean()


# ═══════════════════════════════════════════════════════════════════════════
#  TIER 2 — chart-only extras (`ta` library, imported lazily)
# ═══════════════════════════════════════════════════════════════════════════

def _ema(df, period=20):
    return pine_ema(df["close"], period)

def _sma(df, period=20):
    from ta.trend import SMAIndicator
    return SMAIndicator(close=df["close"], window=int(period)).sma_indicator()

def _rsi(df, period=14):
    return wilder_rsi(df["close"], period)

def _atr(df, period=14):
    return wilder_atr(df, period)

def _vwap(df):
    from ta.volume import VolumeWeightedAveragePrice
    if "volume" not in df.columns:
        raise ValueError("VWAP needs a 'volume' column — use resample_with_volume(), not resample()")
    return VolumeWeightedAveragePrice(
        high=df["high"], low=df["low"], close=df["close"], volume=df["volume"]
    ).volume_weighted_average_price()

def _bbands_mid(df, period=20):
    from ta.volatility import BollingerBands
    return BollingerBands(close=df["close"], window=int(period)).bollinger_mavg()


# overlay=True  → drawn ON the price chart (same scale as candles): EMA/SMA/VWAP/BBANDS
# overlay=False → oscillator, drawn in its OWN bottom panel (separate scale): RSI/ATR
INDICATOR_REGISTRY = {
    "EMA":     {"fn": _ema,        "type": "line", "color": "#d29922", "params": ["period"], "default": {"period": 20}, "overlay": True},
    "SMA":     {"fn": _sma,        "type": "line", "color": "#8b949e", "params": ["period"], "default": {"period": 20}, "overlay": True},
    "RSI":     {"fn": _rsi,        "type": "line", "color": "#7e57c2", "params": ["period"], "default": {"period": 14}, "overlay": False},
    "ATR":     {"fn": _atr,        "type": "line", "color": "#f85149", "params": ["period"], "default": {"period": 14}, "overlay": False},
    "VWAP":    {"fn": _vwap,       "type": "line", "color": "#3fb950", "params": [],          "default": {},            "overlay": True},
    "BBANDS":  {"fn": _bbands_mid, "type": "line", "color": "#d2a8ff", "params": ["period"], "default": {"period": 20}, "overlay": True},
}


def compute_indicator(df, name, **params):
    """Returns a pandas Series aligned to df's index. Raises KeyError if name unknown."""
    spec = INDICATOR_REGISTRY[name]
    kwargs = {**spec["default"], **params}
    return spec["fn"](df, **kwargs) if kwargs else spec["fn"](df)


def list_available_indicators():
    """Name + param schema for the dashboard's 'Add Indicator' dropdown."""
    return [
        {"name": name, "params": spec["params"], "default": spec["default"],
         "type": spec["type"], "color": spec["color"], "overlay": spec["overlay"]}
        for name, spec in INDICATOR_REGISTRY.items()
    ]


def indicator_series_to_points(series, df, time_col=None):
    """pandas Series -> [{"time": unix_seconds, "value": float}, ...] for the chart, skipping NaN.
    time_col: explicit datetime column name; if omitted, prefers "time" (the
    convention used by backtest_engine.py's resample()/resample_with_volume())
    then "date", falling back to the row index only if neither exists."""
    points = []
    col = time_col or ("time" if "time" in df.columns else ("date" if "date" in df.columns else None))
    times = df[col] if col else df.index
    for ts, val in zip(times, series):
        if val is None or pd.isna(val):
            continue
        unix_ts = int(ts.timestamp()) if hasattr(ts, "timestamp") else int(ts)
        points.append({"time": unix_ts, "value": round(float(val), 4)})
    return points
