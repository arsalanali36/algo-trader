"""Shared loaders for the Markov / mean-reversion study.

Data is ALWAYS a local CSV — no yfinance (CODE3B rule). Any daily OHLC/Close
CSV works (NIFTY, SPY, a stock) as long as it has a Date/Datetime col + Close.
"""
import os
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DAILY = os.path.join(HERE, "nifty_daily.csv")


def _find_1min():
    """nifty_1min.csv is gitignored, so a git worktree won't have it locally.
    Try the sibling scratch first, then the main working tree."""
    cands = [
        os.path.abspath(os.path.join(HERE, "..", "nifty_trend", "nifty_1min.csv")),
        r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\nifty_1min.csv",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return cands[0]


DEFAULT_1MIN = _find_1min()


def resample_daily(src_1min=DEFAULT_1MIN, out=DEFAULT_DAILY):
    """1-min bars -> daily OHLC (calendar-date session groups). Writes `out`."""
    df = pd.read_csv(src_1min, parse_dates=["Datetime"])
    df["Date"] = df["Datetime"].dt.date
    daily = (
        df.groupby("Date")
        .agg(
            Open=("Open", "first"),
            High=("High", "max"),
            Low=("Low", "min"),
            Close=("Close", "last"),
            Volume=("Volume", "sum"),
        )
        .reset_index()
    )
    daily.to_csv(out, index=False)
    return daily


def load_daily(path=DEFAULT_DAILY, start=None, end=None):
    """Load a daily CSV -> DataFrame with Date(datetime), Close, ret, state.

    state: 'up' if daily return >= 0 else 'down'  (video's convention).
    """
    df = pd.read_csv(path)
    # find date + close columns flexibly
    date_col = next((c for c in df.columns if c.lower() in ("date", "datetime")), df.columns[0])
    close_col = next((c for c in df.columns if c.lower() in ("close", "adj close", "adjclose")), None)
    if close_col is None:
        raise ValueError(f"No Close column in {path}; cols={list(df.columns)}")
    df["Date"] = pd.to_datetime(df[date_col])
    df["Close"] = pd.to_numeric(df[close_col], errors="coerce")
    df = df[["Date", "Close"]].dropna().sort_values("Date").reset_index(drop=True)
    if start:
        df = df[df["Date"] >= pd.to_datetime(start)]
    if end:
        df = df[df["Date"] <= pd.to_datetime(end)]
    df = df.reset_index(drop=True)
    df["ret"] = df["Close"].pct_change()
    df["state"] = np.where(df["ret"] >= 0, "up", "down")
    return df


if __name__ == "__main__":
    d = resample_daily()
    print(f"Resampled -> {DEFAULT_DAILY}  ({len(d)} daily bars, "
          f"{d['Date'].min()} .. {d['Date'].max()})")
