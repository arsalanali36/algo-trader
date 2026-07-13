"""Task 0a (ml-strategy-mining) — data inventory of the NIFTY option-chain lake.

Enumerates every series in _TRADING_DATA/OptChainLake/NIFTY/<WEEK|MONTH>/<CE|PE>_<off>.csv:
rows, date span, distinct trading days, field-quality stats (iv zero/out-of-band %,
oi zero %, volume zero %), and daily-coverage gaps vs the flag's best series.

Run on the VPS (data lives there):
    venv/bin/python scratch/nifty_trend/ml_data_inventory.py [--json out.json]

Read-only — touches nothing, writes only the optional --json report.
"""
import os
import sys
import glob
import json
import argparse
import datetime as dt

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LAKE = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake", "NIFTY")

IV_LO, IV_HI = 3.0, 120.0   # same sane band optlake_load uses


def series_stats(path):
    df = pd.read_csv(path)
    if df.empty:
        return None
    ts = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    days = ts.dt.date
    iv = df["iv"]
    return {
        "rows": int(len(df)),
        "first": str(days.min()),
        "last": str(days.max()),
        "days": int(days.nunique()),
        "iv_zero_pct": round(100.0 * (iv == 0).mean(), 2),
        "iv_oob_pct": round(100.0 * ((iv < IV_LO) | (iv > IV_HI)).mean(), 2),
        "oi_zero_pct": round(100.0 * (df["oi"] == 0).mean(), 2),
        "vol_zero_pct": round(100.0 * (df["volume"] == 0).mean(), 2),
        "day_set": set(days.unique()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    report = {"lake": LAKE, "generated": str(dt.datetime.now()), "flags": {}}
    for flag in ("WEEK", "MONTH"):
        fdir = os.path.join(LAKE, flag)
        if not os.path.isdir(fdir):
            continue
        rows = {}
        all_days = set()
        for p in sorted(glob.glob(os.path.join(fdir, "*.csv"))):
            name = os.path.basename(p)[:-4]
            st = series_stats(p)
            if st is None:
                rows[name] = None
                continue
            all_days |= st["day_set"]
            rows[name] = st
        # coverage vs union of days seen in this flag
        n_all = len(all_days)
        out = {}
        for name, st in rows.items():
            if st is None:
                out[name] = {"rows": 0}
                continue
            st = dict(st)
            missing = n_all - len(st.pop("day_set"))
            st["missing_days_vs_flag"] = int(missing)
            out[name] = st
        report["flags"][flag] = {"union_days": n_all, "series": out}

    # manifest progress
    man = os.path.join(LAKE, "_done.json")
    if os.path.exists(man):
        done = json.load(open(man))
        report["manifest_done_chunks"] = len(done)

    # pretty table to stdout
    for flag, blk in report["flags"].items():
        print(f"\n=== {flag}  (union trading days: {blk['union_days']}) ===")
        print(f"{'series':<10} {'rows':>8} {'first':>12} {'last':>12} {'days':>5} "
              f"{'miss':>5} {'iv0%':>6} {'ivOOB%':>7} {'oi0%':>6} {'vol0%':>6}")
        for name in sorted(blk["series"], key=lambda n: (n.split("_")[0], n)):
            s = blk["series"][name]
            if s.get("rows", 0) == 0:
                print(f"{name:<10} {'EMPTY':>8}")
                continue
            print(f"{name:<10} {s['rows']:>8} {s['first']:>12} {s['last']:>12} {s['days']:>5} "
                  f"{s['missing_days_vs_flag']:>5} {s['iv_zero_pct']:>6} {s['iv_oob_pct']:>7} "
                  f"{s['oi_zero_pct']:>6} {s['vol_zero_pct']:>6}")
    if "manifest_done_chunks" in report:
        print(f"\nmanifest done chunks: {report['manifest_done_chunks']}")

    if args.json:
        for blk in report["flags"].values():
            for s in blk["series"].values():
                s.pop("day_set", None)
        json.dump(report, open(args.json, "w"), indent=1, default=str)
        print(f"json -> {args.json}")


if __name__ == "__main__":
    main()
