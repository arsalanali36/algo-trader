#!/usr/bin/env python3
"""Does bigger LOT SIZE dilute cost? Framework = actual exits + max5 + Rs3000 lock.
Taken-set fixed at 1x (so trade selection is identical), then reprice the SAME
trades at 2x/3x/5x lot — only qty scales. Fixed brokerage (Rs20/order) stays,
% charges (STT/txn) + slippage scale. Shows net, net-per-1x-equiv, cost, cost%.

Run on VPS: venv/bin/python scripts/lot_scale.py
"""
import sys, os, argparse
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import path_aware_sl_sim as pas
import capital_pool_sim as cps

MAXTR, PLOCK = 5, 3000
MULTS = [1, 2, 3, 5, 10]


def _inr(x): return f"{round(x):,}"


def load(dfrom, dto):
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    raw = [t for t in raw if t.get("pnl") is not None]
    recs = []
    for t in raw:
        side = str(t["entry"]).upper(); ep = float(t["entry_price"]); qty = int(t["qty"])
        recs.append({"strat": t.get("strategy") or "?", "date": t.get("entry_date") or "",
                     "et": (t.get("entry_time") or "09:15")[:5], "side": side, "ep": ep,
                     "qty": qty, "und": cps._underlying(t.get("sym") or ""),
                     "actual": float(t["pnl"])})
    return recs


def charge(r, N):
    """Round-trip charge + slip for this trade at N× lot (qty×N)."""
    tr = {"side": r["side"], "qty": r["qty"] * N, "ep": r["ep"], "und": r["und"]}
    try:
        return cps.leg_charges(tr, r["actual"] * N, False, r["date"])
    except Exception:
        return 0.0


def run(dfrom, dto):
    recs = load(dfrom, dto)
    # --- taken-set at 1x (net = gross - charge_1x), max5 + Rs3000 lock ---
    g = defaultdict(list)
    for r in recs:
        r["net1"] = r["actual"] - charge(r, 1)
        g[(r["strat"], r["date"])].append(r)
    taken = []
    for items in g.values():
        items.sort(key=lambda x: x["et"])
        cum = 0.0; cnt = 0
        for r in items:
            if cnt >= MAXTR or cum >= PLOCK:
                continue
            cum += r["net1"]; cnt += 1; taken.append(r)
    print(f"\nFramework: actual exits + max{MAXTR} + Rs{PLOCK} lock — taken-set FIXED at 1x "
          f"({len(taken)} trades), then repriced at bigger lots.\n")
    print(f"  {'lot':>5} {'gross':>10} {'charges':>10} {'NET':>10} {'net/1x-equiv':>13} "
          f"{'cost/1x':>9} {'cost %gross':>11}")
    base_net = None
    for N in MULTS:
        gross = sum(r["actual"] * N for r in taken)
        ch = sum(charge(r, N) for r in taken)
        net = gross - ch
        net_pl = net / N          # normalised to 1x-lot-equivalent
        ch_pl = ch / N
        cost_pct = 100 * ch / gross if gross else 0
        if base_net is None:
            base_net = net_pl
        delta = net_pl - base_net
        print(f"  {N:>4}x {_inr(gross):>10} {_inr(ch):>10} {_inr(net):>10} "
              f"{_inr(net_pl):>13} {_inr(ch_pl):>9} {cost_pct:>10.1f}%"
              + (f"   (+{_inr(delta)} vs 1x)" if N > 1 else ""))
    print("\n  net/1x-equiv = total net ÷ N — bigger = per-lot cost bachat. cost/1x = charges ÷ N.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    a = ap.parse_args()
    run(a.dfrom, a.dto)
