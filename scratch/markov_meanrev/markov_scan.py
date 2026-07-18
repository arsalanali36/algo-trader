"""Scan for a REAL Markov streak edge across instruments x timeframes x direction.

For every (instrument, timeframe) we bucket 1-min bars into TF bars (session-aware),
then measure both:
  DIP  : P(next bar UP   | N consecutive DOWN bars)   -- buy-the-dip / reversion
  POP  : P(next bar DOWN | N consecutive UP bars)      -- fade-the-rip / reversion
...and the CONTINUATION view is just the complement. We report the cells whose
edge-vs-base is BOTH statistically significant (binomial p<0.05) AND backed by a
non-trivial average next-bar return in the tradeable direction.

Streaks + the "next bar" are kept WITHIN a session (reset each day) so the
overnight gap can't contaminate an intraday signal.

Run:  python markov_scan.py
"""
import math
import numpy as np
import pandas as pd

DATA = {
    "NIFTY": r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\nifty_1min.csv",
    "BANKNIFTY": r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\bnf_1min.csv",
}
TFS = [("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60), ("1d", None)]
MAX_N = 6


def _binom_p(k, n, p0):
    if n == 0:
        return float("nan")
    sd = math.sqrt(n * p0 * (1 - p0)) or 1e-9
    z = (k - n * p0) / sd
    return math.erfc(abs(z) / math.sqrt(2))


def load_1m(path):
    df = pd.read_csv(path, parse_dates=["Datetime"])
    df["day"] = df["Datetime"].dt.normalize()
    return df


def resample(df1m, minutes):
    if minutes is None:  # daily
        g = df1m.groupby("day")
        bars = g.agg(Close=("Close", "last")).reset_index()
        bars["day"] = bars["day"]
        return bars
    b = df1m.copy()
    b["bucket"] = b["Datetime"].dt.floor(f"{minutes}min")
    g = b.groupby("bucket")
    bars = g.agg(Close=("Close", "last"), day=("day", "first")).reset_index()
    return bars


def streak_edge(bars, direction):
    """Return list of (N, samples, p_reversal, edge, avg_ret_pct, pval)."""
    c = bars["Close"].values
    day = bars["day"].values
    ret = np.r_[np.nan, c[1:] / c[:-1] - 1]
    same_day = np.r_[False, day[1:] == day[:-1]]
    ret = np.where(same_day, ret, np.nan)          # first bar of day -> no ret
    state = np.where(ret >= 0, "up", "down")
    state = np.where(np.isnan(ret), "none", state)

    # base rate of the tradeable next-state, computed on valid bars only
    valid = state != "none"
    if direction == "down":                        # bet: next bar UP
        want, streak_state, base = "up", "down", (state[valid] == "up").mean()
    else:                                          # bet: next bar DOWN
        want, streak_state, base = "down", "up", (state[valid] == "down").mean()

    # consecutive run of streak_state, reset at day boundary
    run = np.zeros(len(c), dtype=int)
    for i in range(1, len(c)):
        if not same_day[i]:
            run[i] = 0
            continue
        run[i] = run[i - 1] + 1 if state[i] == streak_state else 0

    out = []
    for n in range(1, MAX_N + 1):
        # bars where a run>=n ended AND a same-day next bar exists with valid state
        idx = np.where(run >= n)[0]
        idx = idx[(idx + 1 < len(c))]
        idx = idx[same_day[idx + 1] & (state[idx + 1] != "none")]
        if len(idx) < 30:
            continue
        nxt = state[idx + 1]
        k = (nxt == want).sum()
        p_rev = k / len(idx)
        avg_ret = np.nanmean(ret[idx + 1]) * 100
        # sign the avg return in the tradeable direction (long dip / short pop)
        signed = avg_ret if direction == "down" else -avg_ret
        pv = _binom_p(k, len(idx), base)
        out.append((n, len(idx), p_rev, p_rev - base, signed, pv))
    return base, out


def main():
    print(f"{'inst':>10}{'tf':>5}{'dir':>5}{'N':>3}{'samp':>7}"
          f"{'P(rev)':>8}{'edge':>8}{'signedRet%':>11}{'p':>7}  flag")
    print("-" * 78)
    hits = []
    for inst, path in DATA.items():
        try:
            df1m = load_1m(path)
        except Exception as e:
            print(f"{inst}: load failed ({e})")
            continue
        for tf_name, mins in TFS:
            bars = resample(df1m, mins)
            for direction, dlabel in [("down", "DIP"), ("up", "POP")]:
                base, rows = streak_edge(bars, direction)
                for (n, samp, prev, edge, sret, pv) in rows:
                    sig = pv < 0.05
                    good = sig and edge > 0 and sret > 0   # reversion edge, right way, pays
                    flag = "<-- EDGE" if good else ("sig" if sig else "")
                    if sig or good:
                        print(f"{inst:>10}{tf_name:>5}{dlabel:>5}{n:>3}{samp:>7}"
                              f"{prev:>8.3f}{edge:>+8.3f}{sret:>+11.4f}{pv:>7.3f}  {flag}")
                    if good:
                        hits.append((inst, tf_name, dlabel, n, samp, edge, sret, pv))
    print("\n=== TRADEABLE REVERSION EDGES (sig + right direction + positive signed return) ===")
    if not hits:
        print("  none — no instrument/timeframe shows a monetizable Markov reversion edge.")
    else:
        for h in sorted(hits, key=lambda x: -x[6]):
            print(f"  {h[0]} {h[1]} {h[2]} N={h[3]}  samp={h[4]} edge={h[5]:+.3f} "
                  f"signedRet={h[6]:+.4f}% p={h[7]:.3f}")


if __name__ == "__main__":
    main()
