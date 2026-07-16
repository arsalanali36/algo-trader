#!/usr/bin/env python3
"""Significance of the 3-lot / max6 / Rs5000 config.
  1. daily sign-flip permutation p (17 days — conservative, low power)
  2. trade-level sign-flip p (236 trades — more power, assumes trade independence)
  3. day-bootstrap Monte-Carlo (resample the 17 days -> % profitable + percentiles)
Real Zerodha charges + DOM slippage. Run on VPS: venv/bin/python scripts/sig_test.py
"""
import sys, os, argparse, math, statistics, random
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import capital_pool_sim as cps

LOTS, MAXT, LOCK = 3, 6, 5000
N = 20000


def _inr(x): return f"{round(x):,}"


def taken_series(dfrom, dto):
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    raw = [t for t in raw if t.get("pnl") is not None]
    g = defaultdict(list)
    for t in raw:
        side = str(t["entry"]).upper(); qty = int(t["qty"]) * LOTS
        gross = float(t["pnl"]) * LOTS
        tr = {"side": side, "qty": qty, "ep": float(t["entry_price"]),
              "und": cps._underlying(t.get("sym") or "")}
        try: ch = cps.leg_charges(tr, gross, False, t.get("entry_date") or "")
        except Exception: ch = 0.0
        g[(t.get("strategy") or "?", t.get("entry_date") or "")].append(
            ((t.get("entry_time") or "09:15")[:5], gross - ch, t.get("entry_date") or ""))
    trades = []; day = defaultdict(float)
    for items in g.values():
        items.sort(key=lambda x: x[0])
        cum = 0.0; cnt = 0
        for et, net, dt in items:
            if cnt >= MAXT or cum >= LOCK: continue
            cum += net; cnt += 1; trades.append(net); day[dt] += net
    dn = [day[d] for d in sorted(day)]
    return trades, dn


def signflip_p(vals):
    obs = statistics.mean(vals); ge = 0
    for _ in range(N):
        if statistics.mean([v if random.random() < 0.5 else -v for v in vals]) >= obs:
            ge += 1
    return ge / N


def boot_days(dn):
    n = len(dn); tots = []
    for _ in range(N):
        tots.append(sum(dn[int(random.random()*n)] for _ in range(n)))
    tots.sort()
    return (sum(1 for x in tots if x > 0) / N,
            tots[int(0.05*N)], tots[int(0.50*N)], tots[int(0.95*N)])


def run(dfrom, dto):
    random.seed(42)
    trades, dn = taken_series(dfrom, dto)
    total = sum(dn); mu = statistics.mean(dn); sd = statistics.pstdev(dn) or 1e-9
    sharpe = (mu/sd)*math.sqrt(252)
    wins = [t for t in trades if t > 0]; loss = [t for t in trades if t <= 0]
    pf = sum(wins)/abs(sum(loss)) if loss else 99
    print(f"\n3 LOTS · max{MAXT} · Rs{LOCK} lock · {len(trades)} trades · {len(dn)} days")
    print(f"  Total net Rs {_inr(total)} | mean/day Rs {_inr(mu)} | PF {pf:.2f} | "
          f"Sharpe(ann) {sharpe:.2f} | win-days {sum(1 for x in dn if x>0)}/{len(dn)}")
    p_day = signflip_p(dn)
    p_trade = signflip_p(trades)
    print(f"\n  Daily sign-flip p     = {p_day:.3f}   ({'SIGNIFICANT' if p_day<0.05 else 'NOT significant'} @0.05, 17-day = low power)")
    print(f"  Trade sign-flip p     = {p_trade:.3f}   ({'SIGNIFICANT' if p_trade<0.05 else 'NOT significant'} @0.05, assumes trade independence = optimistic)")
    pct, p5, p50, p95 = boot_days(dn)
    print(f"\n  Day-bootstrap MC ({N:,} resamples of the 17 days):")
    print(f"    profitable outcomes : {100*pct:.1f}%")
    print(f"    5th percentile      : Rs {_inr(p5)}   (bad-luck 17-day run)")
    print(f"    median              : Rs {_inr(p50)}")
    print(f"    95th percentile     : Rs {_inr(p95)}")
    print(f"\n  NOTE: sign-flip p is SCALE-INVARIANT — 3-lot vs 1-lot ka p SAME (lot size sirf "
          f"magnitude scale karta, significance config/selection pe depend karta).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    a = ap.parse_args()
    run(a.dfrom, a.dto)
