"""Task 0d (ml-strategy-mining) — physically carve the LOCAL mining copy of the
option-chain lake into pre-lockbox (mining-visible) and lockbox (final-eval-only).

  _TRADING_DATA/OptChainLake/NIFTY/...          <- rows BEFORE ml_gate.LOCKBOX_START
  _TRADING_DATA/OptChainLake_LOCKBOX/NIFTY/...  <- rows ON/AFTER the cutoff

Run ONLY on the mining machine (local laptop). The VPS master lake stays whole —
the downloader keeps appending there; re-syncing later re-runs this split.
Idempotent: already-split files just report 0 moved. Spot data (nifty_1min.csv)
is shared with the existing hand-designed pipeline and is NOT physically split —
mining code must use ml_gate.trim_lockbox() + assert_no_lockbox() on it instead.
"""
import os
import glob

import pandas as pd

import ml_gate

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LAKE = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake", "NIFTY")
LOCK = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake_LOCKBOX", "NIFTY")


def split_file(src, dst):
    df = pd.read_csv(src)
    if df.empty:
        return 0, 0
    ist_date = (pd.to_datetime(df["timestamp"], unit="s", utc=True)
                  .dt.tz_convert("Asia/Kolkata").dt.date)
    box = df[ist_date >= ml_gate.LOCKBOX_START]
    keep = df[ist_date < ml_gate.LOCKBOX_START]
    if len(box) == 0:
        return len(keep), 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        old = pd.read_csv(dst)
        box = (pd.concat([old, box], ignore_index=True)
                 .drop_duplicates("timestamp").sort_values("timestamp"))
    box.to_csv(dst, index=False)
    keep.to_csv(src, index=False)
    return len(keep), len(box)


def main():
    print(f"lockbox cutoff: {ml_gate.LOCKBOX_START}  (rows >= cutoff move out of mining view)")
    tot_keep = tot_box = 0
    for src in sorted(glob.glob(os.path.join(LAKE, "*", "*.csv"))):
        rel = os.path.relpath(src, LAKE)
        k, b = split_file(src, os.path.join(LOCK, rel))
        tot_keep += k; tot_box += b
        if b:
            print(f"  {rel:22s} keep={k:6d}  lockbox={b:6d}")
    print(f"\ntotal: mining-visible rows={tot_keep}  lockbox rows={tot_box}")
    # verify: nothing in the mining lake is on/after the cutoff
    bad = 0
    for src in glob.glob(os.path.join(LAKE, "*", "*.csv")):
        df = pd.read_csv(src, usecols=["timestamp"])
        if len(df) and (pd.to_datetime(df.timestamp.max(), unit="s", utc=True)
                          .tz_convert("Asia/Kolkata").date() >= ml_gate.LOCKBOX_START):
            print(f"  VIOLATION remains in {src}"); bad += 1
    print("verify:", "CLEAN — mining lake ends before cutoff" if bad == 0 else f"{bad} files dirty")


if __name__ == "__main__":
    main()
