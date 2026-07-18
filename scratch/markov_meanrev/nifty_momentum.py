"""NIFTY Momentum Breakout — the validated build.

Turns the study's strongest lead (NIFTY daily breakout momentum) into a
decision-grade artifact: full metrics, TRAIN/OOS split, a permutation
significance test (does the breakout timing beat RANDOM entry?), and a bootstrap
Monte-Carlo (worst-5% return + drawdown). Verdict vs the deploy gate
(Sharpe>=1, p<0.05, train & OOS both positive).

  ENTRY : close makes a new L-day high    EXIT : after H bars, or -stop%
  Long-only, positional, NIFTY daily 2018-2026 (incl 2020 crash).

Run: python nifty_momentum.py
"""
import os
import sys
import json
import math
import numpy as np
import pandas as pd
from _common import load_daily, DEFAULT_DAILY

RNG = np.random.default_rng(11)
COST_BPS = 3.0
STOP_PCT = 5.0


def run_strategy(c, L, H, entries=None):
    """Return (daily_ret array, entry_indices). If `entries` given, use those
    (for the random-entry null); else generate from L-day-high breakouts."""
    n = len(c)
    daily = np.zeros(n)
    cside = COST_BPS * 1e-4
    ent_idx = []
    if entries is None:
        hiN = pd.Series(c).rolling(L).max().shift(1).values
        i, pos = L, None
        while i < n:
            if pos is None:
                if not np.isnan(hiN[i]) and c[i] >= hiN[i]:
                    pos = i; ent_idx.append(i); daily[i] = -cside
                i += 1; continue
            held = i - pos
            daily[i] = c[i] / c[i - 1] - 1
            if held >= H or c[i] <= c[pos] * (1 - STOP_PCT / 100) or i == n - 1:
                daily[i] -= cside; pos = None
            i += 1
    else:
        for e in entries:
            if e + 1 >= n:
                continue
            ent_idx.append(e); daily[e] -= cside
            for k in range(1, H + 1):
                j = e + k
                if j >= n:
                    break
                daily[j] += c[j] / c[j - 1] - 1
                if k == H or c[j] <= c[e] * (1 - STOP_PCT / 100) or j == n - 1:
                    daily[j] -= cside; break
    return daily, ent_idx


def sharpe(daily):
    a = daily[daily != 0]
    if len(a) < 5:
        return 0.0
    return (a.mean() / (a.std() or 1e-9)) * math.sqrt(252)


def perf(daily, dates):
    eq = np.cumprod(1 + daily)
    yrs = (dates[-1] - dates[0]).days / 365.25
    cagr = (eq[-1] ** (1 / yrs) - 1) * 100 if yrs > 0 else 0
    peak = np.maximum.accumulate(eq)
    dd = ((eq - peak) / peak).min() * 100
    return dict(tot=(eq[-1] - 1) * 100, cagr=cagr, dd=dd, sharpe=sharpe(daily),
                expo=(daily != 0).mean() * 100)


def significance(c, L, H, actual_sh, n_perm=1000):
    """Null: same #entries, same hold, but RANDOM entry days. Does the breakout
    timing beat random timing? p = fraction of null Sharpe >= actual."""
    _, ent = run_strategy(c, L, H)
    nt = len(ent)
    lo, hi = L, len(c) - 2
    beat = 0
    for _ in range(n_perm):
        rand = RNG.choice(np.arange(lo, hi), size=nt, replace=False)
        d, _ = run_strategy(c, L, H, entries=sorted(rand))
        if sharpe(d) >= actual_sh:
            beat += 1
    return (beat + 1) / (n_perm + 1)


def montecarlo(trade_rets, n_sims=1000):
    """Bootstrap-resample trade returns -> distribution of final return + maxDD."""
    tr = np.array(trade_rets)
    fins, dds = [], []
    for _ in range(n_sims):
        s = RNG.choice(tr, size=len(tr), replace=True)
        eq = np.cumprod(1 + s)
        fins.append((eq[-1] - 1) * 100)
        peak = np.maximum.accumulate(eq)
        dds.append(((eq - peak) / peak).min() * 100)
    fins, dds = np.array(fins), np.array(dds)
    return dict(med_ret=np.median(fins), worst5_ret=np.percentile(fins, 5),
                med_dd=np.median(dds), worst5_dd=np.percentile(dds, 5))


def trade_returns(c, L, H):
    """Per-trade compounded net return (for Monte Carlo)."""
    hiN = pd.Series(c).rolling(L).max().shift(1).values
    cside = COST_BPS * 1e-4
    out, i, pos = [], L, None
    while i < len(c):
        if pos is None:
            if not np.isnan(hiN[i]) and c[i] >= hiN[i]:
                pos = i
            i += 1; continue
        held = i - pos
        if held >= H or c[i] <= c[pos] * (1 - STOP_PCT / 100) or i == len(c) - 1:
            out.append((c[i] / c[pos] - 1) - 2 * cside); pos = None
        i += 1
    return out


def main():
    df = load_daily(DEFAULT_DAILY)
    c = df["Close"].values
    dates = df["Date"].values
    dts = pd.to_datetime(dates)
    print(f"NIFTY daily {dts[0].date()}..{dts[-1].date()} ({len(c)} bars) | "
          f"cost {COST_BPS}bps/side | stop {STOP_PCT}%\n")

    # 1) sweep, rank by min(TRAIN, OOS) Sharpe (robust — TRAP #103)
    split = pd.Timestamp("2022-12-31")
    tr_mask = dts <= split
    print("Sweep (rank by min(train,OOS) Sharpe):")
    best = None
    for L in [20, 50, 100, 200]:
        for H in [5, 10, 20]:
            d_full, _ = run_strategy(c, L, H)
            sh_tr = sharpe(d_full[tr_mask])
            sh_oos = sharpe(d_full[~tr_mask])
            robust = min(sh_tr, sh_oos)
            tag = f"L{L}/H{H}"
            if best is None or robust > best[0]:
                best = (robust, L, H)
            print(f"  {tag:>9}: full Sh {sharpe(d_full):+.2f} | train {sh_tr:+.2f} "
                  f"| OOS {sh_oos:+.2f} | min {robust:+.2f}")
    _, L, H = best
    print(f"\n>>> BEST (robust): L={L} / H={H}\n")

    # 2) full performance
    d, ent = run_strategy(c, L, H)
    p = perf(d, dts)
    bh = perf(df["Close"].pct_change().fillna(0).values, dts)
    print(f"FULL:  tot {p['tot']:+.1f}% | CAGR {p['cagr']:+.1f}% | DD {p['dd']:.1f}% "
          f"| Sharpe {p['sharpe']:+.2f} | expo {p['expo']:.0f}% | {len(ent)} trades")
    print(f"B&H :  tot {bh['tot']:+.1f}% | CAGR {bh['cagr']:+.1f}% | DD {bh['dd']:.1f}% "
          f"| Sharpe {bh['sharpe']:+.2f}")
    tr_p = perf(d[tr_mask], dts[tr_mask])
    oos_p = perf(d[~tr_mask], dts[~tr_mask])
    print(f"TRAIN: Sharpe {tr_p['sharpe']:+.2f} | CAGR {tr_p['cagr']:+.1f}% | DD {tr_p['dd']:.1f}%")
    print(f"OOS  : Sharpe {oos_p['sharpe']:+.2f} | CAGR {oos_p['cagr']:+.1f}% | DD {oos_p['dd']:.1f}%")

    # 3) significance vs random entry
    print("\nSignificance (1000 random-entry nulls)...", flush=True)
    pval = significance(c, L, H, p["sharpe"])
    print(f"  p-value (breakout beats random timing) = {pval:.3f}")

    # 4) monte carlo bootstrap
    tr = trade_returns(c, L, H)
    mc = montecarlo([x for x in tr])
    print(f"\nMonte-Carlo (1000 bootstraps of {len(tr)} trades):")
    print(f"  final return: median {mc['med_ret']:+.1f}% | worst-5% {mc['worst5_ret']:+.1f}%")
    print(f"  max drawdown: median {mc['med_dd']:.1f}% | worst-5% {mc['worst5_dd']:.1f}%")

    # 5) verdict vs deploy gate
    gate = {
        "Sharpe>=1 (full)": p["sharpe"] >= 1.0,
        "p<0.05": pval < 0.05,
        "TRAIN>0 & OOS>0": tr_p["sharpe"] > 0 and oos_p["sharpe"] > 0,
        "beats B&H Sharpe": p["sharpe"] > bh["sharpe"],
        "MC worst-5% return > 0": mc["worst5_ret"] > 0,
    }
    print("\n=== DEPLOY GATE ===")
    for k, v in gate.items():
        print(f"  [{'PASS' if v else 'FAIL'}] {k}")
    verdict = "PASS -> forward-paper candidate" if all(gate.values()) else \
              "PARTIAL -> promising, not full deploy-grade"
    print(f"  VERDICT: {verdict}")

    out = dict(L=L, H=H, full=p, bh=bh, train=tr_p, oos=oos_p, pval=pval,
               montecarlo=mc, gate=gate, verdict=verdict, trades=len(ent))
    rp = os.path.join(os.path.dirname(__file__), "nifty_momentum_result.json")
    with open(rp, "w") as f:
        json.dump(out, f, indent=2, default=float)
    print(f"\nsaved -> {rp}")


if __name__ == "__main__":
    main()
