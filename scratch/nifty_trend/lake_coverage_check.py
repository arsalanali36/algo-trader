"""Lake coverage checker — TRAP #198 ka permanent verifier.

Sawaal: kya hamara premium-lake un strikes ko poori trade-life tak price kar sakta hai
jo strategy sach me hold karti hai? Lake ATM-RELATIVE hai (offset window), par trade
FIXED strike hold karti hai — ATM khiskta hai to strike window se bahar chala jaata hai
aur reader chupchaap INTRINSIC (OTM ke liye 0) laga deta hai. Wahi bug 02.10.01 ke 19%
trades me tha aur uske 80% "profit" ka source tha.

Ye script koi backtest nahi chalata — sirf lake ki asli files padh ke naapta hai:
  1. lake me sach me kaunse offsets bhare hain (file-wise coverage)
  2. har structure ke liye: kitne % trading-days pe saare legs poori hold-life
     tak window ke andar rehte (ATM drift ke saath)

Usage:
    python lake_coverage_check.py                      # default structures
    python lake_coverage_check.py --hold 1 --sym BANKNIFTY
"""
import os
import sys
import glob
import argparse
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LAKE_ROOT = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake_1m")

# (naam, short offset, wing offset) — legs ATM se kitne strike door
STRUCTURES = [
    ("02.10.01 BNF hedged strangle", 6, 11),
    ("BNF wing-10 variant",          6, 10),
    ("02.17 NIFTY weekly iron-fly",  0,  5),
]


def lake_offsets(sym, flag):
    """Lake me physically kaunse offsets maujood hain."""
    d = os.path.join(LAKE_ROOT, sym, flag)
    offs = set()
    for p in glob.glob(os.path.join(d, "CE_*.csv")):
        tag = os.path.basename(p)[3:-4]          # CE_ATMp7.csv -> ATMp7
        if tag == "ATM":
            offs.add(0)
        elif tag.startswith("ATMp"):
            offs.add(int(tag[4:]))
        elif tag.startswith("ATMm"):
            offs.add(-int(tag[4:]))
    return offs


def atm_series(sym, flag):
    """Per-bar ATM strike + date, lake ke apne ATM file se."""
    p = os.path.join(LAKE_ROOT, sym, flag, "CE_ATM.csv")
    if not os.path.exists(p):
        return None
    d = pd.read_csv(p, usecols=["timestamp", "strike"]).dropna()
    ist = pd.to_datetime(d["timestamp"] + 19800, unit="s")
    d["date"] = ist.dt.strftime("%Y-%m-%d")
    return d.rename(columns={"strike": "atm"})[["date", "atm"]]


def check(sym, flag, short_off, wing_off, hold_days, step):
    offs = lake_offsets(sym, flag)
    if not offs:
        return None
    window = max(abs(o) for o in offs)
    atm = atm_series(sym, flag)
    if atm is None or atm.empty:
        return None
    days = sorted(atm.date.unique())
    per_day_atm = atm.groupby("date").agg(first=("atm", "first"))
    grouped = {d: g.atm.values for d, g in atm.groupby("date")}

    total = safe = 0
    for i, d0 in enumerate(days):
        span = days[i:i + hold_days + 1]
        if len(span) < hold_days + 1:
            break
        atm0 = float(per_day_atm.loc[d0, "first"])
        strikes = [atm0 + short_off * step, atm0 - short_off * step,
                   atm0 + wing_off * step, atm0 - wing_off * step]
        drift = np.concatenate([grouped[d] for d in span])
        ok = all((np.abs(np.round((K - drift) / step)) <= window).all() for K in strikes)
        total += 1
        safe += bool(ok)
    return dict(window=window, offsets=len(offs), days=total,
                safe=safe, pct=100.0 * safe / max(total, 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=1, help="overnight nights held (0=intraday)")
    ap.add_argument("--sym", default=None)
    a = ap.parse_args()

    print("=" * 96)
    print("LAKE COVERAGE CHECK (TRAP #198)  —  hold = %d night(s)" % a.hold)
    print("=" * 96)
    combos = [("BANKNIFTY", "MONTH", 100), ("NIFTY", "WEEK", 50), ("NIFTY", "MONTH", 50)]
    if a.sym:
        combos = [c for c in combos if c[0] == a.sym]
    for sym, flag, step in combos:
        offs = lake_offsets(sym, flag)
        if not offs:
            print(f"\n{sym}/{flag}: lake missing"); continue
        print(f"\n{sym}/{flag}   lake window +-{max(abs(o) for o in offs)}  "
              f"({len(offs)} offsets on disk)")
        for name, so, wo in STRUCTURES:
            r = check(sym, flag, so, wo, a.hold, step)
            if not r:
                continue
            verdict = ("CLEAN" if r["pct"] >= 99.5 else
                       "usable" if r["pct"] >= 95 else "NOT USABLE")
            print(f"   {name:<32} legs ATM+-{so}/+-{wo:<3}"
                  f"  safe {r['safe']:>5}/{r['days']:<5} = {r['pct']:>5.1f}%   {verdict}")
    print("\n  CLEAN = har trade ke saare legs poori hold-life window me rahe "
          "(koi nakli intrinsic nahi).")


if __name__ == "__main__":
    main()
