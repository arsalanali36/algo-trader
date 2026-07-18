"""analyze.py — honest gate metrics on a trades CSV from vwap_ema_failure.py.

Gate (project standard): daily-Sharpe >= 1, permutation/bootstrap p < 0.05,
and BOTH train & OOS net positive (min(train,OOS) > 0). Monthly breakdown too.

Sign p-value: bootstrap over DAILY net P&L (block = 1 day, the trade-independent
unit) — resample days with replacement, fraction of resamples with mean<=0.
"""
import sys
import numpy as np
import pandas as pd

def bootstrap_p(daily, n=5000, seed=7):
    rng = np.random.default_rng(seed)
    d = daily.values
    if len(d) < 5 or d.mean() <= 0:
        return 1.0
    means = rng.choice(d, size=(n, len(d)), replace=True).mean(axis=1)
    return float((means <= 0).mean())

def sharpe(daily):
    return (daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0

def block(df, label):
    if df.empty:
        print(f"{label:10s}: no trades"); return
    daily = df.groupby("date").net.sum()
    wr = (df.net > 0).mean()
    pf = df[df.net>0].net.sum() / abs(df[df.net<=0].net.sum()) if (df.net<=0).any() else float('inf')
    print(f"{label:10s}: {len(df):5d} tr | net {df.net.sum():>10,.0f} | "
          f"WR {wr*100:4.1f}% | PF {pf:4.2f} | Sharpe {sharpe(daily):5.2f} | "
          f"p={bootstrap_p(daily):.3f}")

def main(path):
    df = pd.read_csv(path)
    df["date"] = df["date"].astype(str)
    df = df.sort_values("date")
    print(f"\n### {path}  ({len(df)} trades, {df.date.nunique()} days)")
    block(df, "ALL")
    # 70/30 chronological train/OOS
    days = sorted(df.date.unique())
    cut = days[int(len(days)*0.7)]
    block(df[df.date < cut], "TRAIN")
    block(df[df.date >= cut], "OOS")
    # monthly
    df["month"] = df.date.str[:7]
    print("\n  monthly net:")
    mo = df.groupby("month").net.sum()
    for m, v in mo.items():
        bar = "#" * int(abs(v)/2000)
        print(f"    {m}: {v:>10,.0f} {'+' if v>=0 else '-'}{bar}")

if __name__ == "__main__":
    for p in sys.argv[1:]:
        main(p)
