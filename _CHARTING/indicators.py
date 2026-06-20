"""
_CHARTING/indicators.py — Library-backed indicator calculations.

Uses the open-source `ta` package (pure pandas, no compiled deps) rather than
hand-written EMA/RSI/ATR/VWAP formulas. Note: `pandas-ta` was the original
choice but its build depends on `numba`, which does not yet support
Python 3.14 (this machine's interpreter) — `ta` covers the same indicators
without that constraint.

INDICATOR_REGISTRY drives the dashboard's "Add Indicator" dropdown
(see _CHARTING/plot_spec.py + templates/backtest_chart.html) — adding a new
*standard* indicator to a chart should mean adding one registry entry here,
not writing new per-strategy plotting code.
"""

import pandas as pd
from ta.trend import EMAIndicator, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange, BollingerBands
from ta.volume import VolumeWeightedAveragePrice


def _ema(df, period=20):
    return EMAIndicator(close=df["close"], window=int(period)).ema_indicator()

def _sma(df, period=20):
    return SMAIndicator(close=df["close"], window=int(period)).sma_indicator()

def _rsi(df, period=14):
    return RSIIndicator(close=df["close"], window=int(period)).rsi()

def _atr(df, period=14):
    return AverageTrueRange(high=df["high"], low=df["low"], close=df["close"], window=int(period)).average_true_range()

def _vwap(df):
    if "volume" not in df.columns:
        raise ValueError("VWAP needs a 'volume' column — use resample_with_volume(), not resample()")
    return VolumeWeightedAveragePrice(
        high=df["high"], low=df["low"], close=df["close"], volume=df["volume"]
    ).volume_weighted_average_price()

def _bbands_mid(df, period=20):
    return BollingerBands(close=df["close"], window=int(period)).bollinger_mavg()


# overlay=True  → drawn ON the price chart (same scale as candles): EMA/SMA/VWAP/BBANDS
# overlay=False → oscillator, drawn in its OWN bottom panel (separate scale): RSI/ATR
INDICATOR_REGISTRY = {
    "EMA":     {"fn": _ema,        "type": "line", "color": "#d29922", "params": ["period"], "default": {"period": 20}, "overlay": True},
    "SMA":     {"fn": _sma,        "type": "line", "color": "#8b949e", "params": ["period"], "default": {"period": 20}, "overlay": True},
    "RSI":     {"fn": _rsi,        "type": "line", "color": "#1f6feb", "params": ["period"], "default": {"period": 14}, "overlay": False},
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
