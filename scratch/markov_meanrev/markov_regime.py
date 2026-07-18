"""Does buy-the-dip mean-reversion work in HIGH-VOLATILITY regimes?

The video's core claim: mean-reversion shines in high-vol / recession periods
(2007-08 gave +150%). We test that directly on NIFTY daily 2018-2026 (includes
the 2020 COVID crash) by splitting every dip-entry by the India-VIX level on the
signal day into LOW / MID / HIGH terciles, and measuring the bounce probability
+ net P&L in each regime.

Run: python markov_regime.py [--n 3] [--cost-bps 3]
"""
import sys
import math
import numpy as np
import pandas as pd
from _common import load_daily, DEFAULT_DAILY

VIX = r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\india_vix_daily.csv"


def _binom_p(k, n, p0):
    sd = math.sqrt(n * p0 * (1 - p0)) or 1e-9
    return math.erfc(abs((k - n * p0) / sd) / math.sqrt(2))


def main():
    argv = sys.argv[1:]

    def opt(k, d, cast=float):
        return cast(argv[argv.index(k) + 1]) if k in argv else d

    n = opt("--n", 3, int)
    cost = opt("--cost-bps", 3.0)
    max_hold = opt("--max-hold", 10, int)

    df = load_daily(DEFAULT_DAILY)
    vix = pd.read_csv(VIX)
    vix["Date"] = pd.to_datetime(vix["Date"])
    vix = vix.rename(columns={"Close": "vix"})[["Date", "vix"]]
    df = df.merge(vix, on="Date", how="left")
    df["vix"] = df["vix"].ffill()

    lo, hi = df["vix"].quantile([0.33, 0.66])
    print(f"NIFTY daily {df['Date'].min().date()}..{df['Date'].max().date()} | "
          f"N={n} | cost {cost}bps/side | VIX terciles: <{lo:.1f} / {lo:.1f}-{hi:.1f} / >{hi:.1f}\n")

    c = df["Close"].values
    vx = df["vix"].values
    down = np.r_[False, c[1:] < c[:-1]]
    run = np.zeros(len(c), int)
    for i in range(1, len(c)):
        run[i] = run[i - 1] + 1 if down[i] else 0

    # collect dip entries -> (regime, bounce?, net%)
    recs = []
    i = n
    pos = None
    while i < len(c):
        if pos is None:
            if run[i] >= n:
                pos = (i, c[i], vx[i])
            i += 1
            continue
        ei, ep, ev = pos
        held = i - ei
        up_close = c[i] > c[i - 1]
        if up_close or held >= max_hold:
            gross = (c[i] - ep) / ep * 100
            net = gross - 2 * cost / 100
            reg = "HIGH" if ev > hi else "LOW" if ev < lo else "MID"
            recs.append((reg, up_close, net, ev))
            pos = None
        i += 1

    R = pd.DataFrame(recs, columns=["regime", "bounce", "net", "vix"])
    print(f"{'regime':>7}{'trades':>8}{'P(bounce)':>11}{'p':>7}{'win%':>7}"
          f"{'avgNet%':>10}{'totNet%':>9}{'PF':>6}")
    for reg in ["LOW", "MID", "HIGH"]:
        r = R[R.regime == reg]
        if len(r) < 5:
            print(f"{reg:>7}{len(r):>8}   (too few)"); continue
        k = r["bounce"].sum()
        pv = _binom_p(k, len(r), 0.535)     # vs NIFTY base up-rate
        pf = r.loc[r.net > 0, "net"].sum() / (-r.loc[r.net < 0, "net"].sum() or 1e-9)
        tot = (np.prod(1 + r["net"].values / 100) - 1) * 100
        print(f"{reg:>7}{len(r):>8}{k/len(r):>11.3f}{pv:>7.3f}"
              f"{(r.net>0).mean()*100:>7.1f}{r.net.mean():>+10.3f}{tot:>+9.1f}{pf:>6.2f}")
    print(f"\nALL:   {len(R)} trades | avgNet {R.net.mean():+.3f}% | "
          f"total {(np.prod(1+R.net.values/100)-1)*100:+.1f}%")


if __name__ == "__main__":
    main()
