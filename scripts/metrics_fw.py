#!/usr/bin/env python3
"""Sharpe + profit factor + expectancy for the framework (max5 + Rs3000 lock,
actual exits). Reuses distribution.load/take_framework (Rule 6B).

Run on VPS: venv/bin/python scripts/metrics_fw.py [--no-fetch]
"""
import sys, os, argparse, math, statistics, random
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import distribution as D


def _inr(x): return f"{round(x):,}"


def run(dfrom, dto, allow_fetch):
    recs = D.load(dfrom, dto, allow_fetch)
    taken, day = D.take_framework(recs)
    nets = [t["net"] for t in taken]
    dn = [day[d] for d in sorted(day)]
    n = len(nets)

    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x <= 0]
    gp = sum(wins); gl = abs(sum(losses))
    pf = gp / gl if gl else float("inf")
    exp = sum(nets) / n
    win_rate = 100*len(wins)/n

    # per-DAY sharpe (annualised) — capital-independent ratio
    mu = statistics.mean(dn); sd = statistics.pstdev(dn) or 1e-9
    sharpe_d = (mu / sd) * math.sqrt(252)
    # per-TRADE sharpe (per-trade units, not annualised)
    mut = statistics.mean(nets); sdt = statistics.pstdev(nets) or 1e-9
    sharpe_t = mut / sdt

    # maxDD on daily equity
    eq = 0.0; pk = 0.0; dd = 0.0
    for x in dn:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)

    # significance: sign-flip permutation on daily nets
    random.seed(42); obs = statistics.mean(dn); ge = 0; N = 5000
    for _ in range(N):
        s = statistics.mean([x if random.random() < 0.5 else -x for x in dn])
        if s >= obs: ge += 1
    p = ge / N

    print(f"\nFramework: max5 + Rs3000 lock, actual exits | {n} trades, {len(dn)} days")
    print(f"  Total net           Rs {_inr(sum(nets))}")
    print(f"  Profit Factor       {pf:.2f}   (gross win {_inr(gp)} / gross loss {_inr(gl)})")
    print(f"  Win rate            {win_rate:.0f}%   (avg win {_inr(sum(wins)/max(1,len(wins)))} / avg loss {_inr(sum(losses)/max(1,len(losses)))})")
    print(f"  Expectancy/trade    Rs {_inr(exp)}")
    print(f"  Sharpe (annualised, daily)  {sharpe_d:.2f}")
    print(f"  Sharpe (per-trade)          {sharpe_t:.2f}")
    print(f"  Max drawdown        Rs {_inr(dd)}   | MAR (net/maxDD) {sum(nets)/dd:.2f}")
    print(f"  Significance p      {p:.3f}   ({'significant' if p<0.05 else 'NOT significant'} @0.05)")
    print(f"\n  NOTE: 17 days is a thin sample — annualised Sharpe (x sqrt252) is a big")
    print(f"  extrapolation; treat as indicative, not proven.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
