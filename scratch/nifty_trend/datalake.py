"""Canonical NIFTY 1-min data-lake: per-day CSVs in the shared store the rest of
CODE3B already uses. One good file per trading day means future runs download
nothing — they just read what's on disk.

Store: <._TRADING DATA>/Index/NIFTY/NIFTY_YYYY-MM-DD.csv
Format (matches existing convention): Datetime,Open,High,Low,Close,Volume
"""
import os, glob, datetime
import pandas as pd

# Fixed Windows store first (same convention as backtest_engine.py); else project-local.
_WIN = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Index\NIFTY"
if os.path.isdir(os.path.dirname(os.path.dirname(_WIN))):
    STORE = _WIN
else:
    _BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    STORE = os.path.join(_BASE, "_TRADING_DATA", "Index", "NIFTY")
os.makedirs(STORE, exist_ok=True)

COLS = ["Datetime", "Open", "High", "Low", "Close", "Volume"]


def _path(d):
    return os.path.join(STORE, f"NIFTY_{d}.csv")


def has_day(d):
    """True only if the per-day file exists AND holds real rows (not a stub)."""
    p = _path(d)
    return os.path.isfile(p) and os.path.getsize(p) > 60


def write_days(df):
    """Split a 1-min frame into per-day CSVs (overwrites stubs)."""
    df = df.copy()
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    n = 0
    for day, g in df.groupby(df["Datetime"].dt.date):
        g[COLS].to_csv(_path(day), index=False)
        n += 1
    return n


def load_all(start=None, end=None):
    """Concatenate every good per-day file (optionally within [start,end])."""
    frames = []
    for p in sorted(glob.glob(os.path.join(STORE, "NIFTY_20*.csv"))):
        if os.path.getsize(p) <= 60:
            continue
        d = os.path.basename(p)[6:16]        # NIFTY_YYYY-MM-DD.csv
        if start and d < start:
            continue
        if end and d > end:
            continue
        try:
            frames.append(pd.read_csv(p))
        except Exception:
            pass
    if not frames:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(frames, ignore_index=True)
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    return df.drop_duplicates("Datetime").sort_values("Datetime").reset_index(drop=True)


def present_days():
    out = set()
    for p in glob.glob(os.path.join(STORE, "NIFTY_20*.csv")):
        if os.path.getsize(p) > 60:
            out.add(os.path.basename(p)[6:16])
    return out


def missing_weekdays(start, end):
    """Weekdays in [start,end] with no good file (holidays will just come back empty)."""
    have = present_days()
    miss, cur = [], datetime.date.fromisoformat(start)
    endd = datetime.date.fromisoformat(end)
    while cur <= endd:
        if cur.weekday() < 5 and cur.isoformat() not in have:
            miss.append(cur.isoformat())
        cur += datetime.timedelta(days=1)
    return miss
