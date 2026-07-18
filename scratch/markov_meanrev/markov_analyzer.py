"""Markov-chain analysis of daily closes (the video's core idea).

Two outputs:
  1. 2-state transition matrix  P(next-day state | today's state)
  2. Streak conditioning        P(up tomorrow | N consecutive DOWN closes)
                                P(down tomorrow | N consecutive UP closes)
     ...each with sample size + a binomial significance test vs the base rate,
     AND the average next-day return (a 55% hit-rate is worthless if the wins
     are tiny and the losses large — magnitude is what a strategy monetises).

Run:  python markov_analyzer.py [daily_csv]  [--start 2018-01-01] [--end ...]
Default data = nifty_daily.csv (resample via _common.py first).
"""
import sys
import math
import numpy as np
from _common import load_daily, DEFAULT_DAILY


def _binom_p(k, n, p0):
    """Two-sided p-value that observed k/n differs from base rate p0
    (normal approximation to the binomial — no scipy dependency)."""
    if n == 0:
        return float("nan")
    mean = n * p0
    sd = math.sqrt(n * p0 * (1 - p0)) or 1e-9
    z = (k - mean) / sd
    # two-sided via erfc
    return math.erfc(abs(z) / math.sqrt(2))


def transition_matrix(df):
    cur = df["state"].iloc[1:-1].values      # today
    nxt = df["state"].iloc[2:].values         # tomorrow
    states = ["up", "down"]
    print("\n=== 2-STATE TRANSITION MATRIX  P(tomorrow | today) ===")
    print(f"{'':>8}" + "".join(f"{'->'+s:>10}" for s in states) + f"{'n':>8}")
    for s in states:
        row_mask = cur == s
        n = row_mask.sum()
        line = f"{s:>8}"
        for t in states:
            p = (nxt[row_mask] == t).mean() if n else float("nan")
            line += f"{p:>10.3f}"
        print(line + f"{n:>8}")
    base_up = (df["state"] == "up").mean()
    print(f"\nBase rate  P(up any day) = {base_up:.3f}   ({len(df)} bars)")
    return base_up


def _streak_len_down(states):
    """For each index i, how many consecutive DOWN closes end at i (inclusive)."""
    run = np.zeros(len(states), dtype=int)
    c = 0
    for i, s in enumerate(states):
        c = c + 1 if s == "down" else 0
        run[i] = c
    return run


def streak_sweep(df, base_up, direction="down", max_n=8):
    """P(reversal next day | N consecutive closes in `direction`)."""
    states = df["state"].values
    ret = df["ret"].values
    if direction == "down":
        run = _streak_len_down(states)
        target_next = "up"                     # we bet on the bounce
        base = base_up
        label = "consecutive DOWN closes -> next day UP"
    else:
        run = _streak_len_down(np.where(states == "up", "down", "up"))  # flip
        target_next = "down"
        base = 1 - base_up
        label = "consecutive UP closes   -> next day DOWN"

    print(f"\n=== STREAK CONDITIONING:  {label} ===")
    print(f"{'N':>3}{'samples':>9}{'P(reversal)':>13}{'vs base':>9}"
          f"{'avg next ret%':>15}{'p-value':>9}  signif")
    for n in range(1, max_n + 1):
        # days where a streak of EXACTLY >= n ended, and a 'next day' exists
        idx = np.where(run[:-1] >= n)[0]
        if len(idx) < 5:
            print(f"{n:>3}{len(idx):>9}   (too few samples)")
            continue
        nxt_state = states[idx + 1]
        nxt_ret = ret[idx + 1]
        k = (nxt_state == target_next).sum()
        p_rev = k / len(idx)
        avg_ret = np.nanmean(nxt_ret) * 100
        pv = _binom_p(k, len(idx), base)
        star = "***" if pv < 0.01 else "**" if pv < 0.05 else "*" if pv < 0.10 else ""
        edge = p_rev - base
        print(f"{n:>3}{len(idx):>9}{p_rev:>13.3f}{edge:>+9.3f}"
              f"{avg_ret:>+15.3f}{pv:>9.3f}  {star}")
    print("  (*** p<0.01  ** p<0.05  * p<0.10   vs base rate; "
          "avg next ret = mean of the day AFTER the streak)")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    path = args[0] if args else DEFAULT_DAILY
    start = end = None
    for i, a in enumerate(sys.argv):
        if a == "--start":
            start = sys.argv[i + 1]
        if a == "--end":
            end = sys.argv[i + 1]
    df = load_daily(path, start, end)
    print(f"Loaded {len(df)} daily bars  {df['Date'].min().date()} .. {df['Date'].max().date()}"
          f"  from {path}")
    base_up = transition_matrix(df)
    streak_sweep(df, base_up, "down")
    streak_sweep(df, base_up, "up")


if __name__ == "__main__":
    main()
