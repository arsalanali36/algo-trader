#!/usr/bin/env python3
"""Risk:Reward sweep — Fixed vs Aggressive, SL in {1000,1500,2000,2500},
ratios 1:1 / 1:2 / 1:3 (target = ratio x SL). Reuses path_aware_sl_sim's
replay engine (Rule 6B). Reports covered-subset net + TARGET-hit %.

Run on VPS: venv/bin/python scripts/rr_sweep.py [--from ..] [--to ..]
"""
import sys, os, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import path_aware_sl_sim as P

SLS = [1000, 1500, 2000, 2500]
RATIOS = [("1:1", 1), ("1:2", 2), ("1:3", 3)]
STEP = 100  # aggressive favour_step / sl_move


def _inr(x): return f"{round(x):,}"


def run(dfrom, dto, allow_fetch):
    trades = order_store.trades_for_range(dfrom, dto)["details"]
    trades = [t for t in trades if t.get("pnl") is not None]
    print(f"Completed trades {dfrom}..{dto}: {len(trades)}", flush=True)
    cov = P._prep_covered(trades, allow_fetch, hold_eod=False)
    n = len(cov)
    print(f"Covered (real 1-min bars): {n}/{len(trades)}  "
          f"({100*n/max(1,len(trades)):.0f}%)\n", flush=True)
    actual = sum(c["actual"] for c in cov)
    print(f"Actual net (covered {n}): Rs {_inr(actual)}\n")

    def leg(tgt, sl):
        # returns net, %TARGET-hit, %real-SL-hit(loss), %rode-to-actual
        net = 0.0; hT = hS = hN = 0
        for c in cov:
            st, rs = P.replay_legacy(c["win"], c["side"], c["ep"], c["qty"],
                                     tgt*c["lots"], sl*c["lots"], c["fallback"])
            net += rs
            if st == "TARGET": hT += 1
            elif st == "SL": hS += 1
            else: hN += 1
        return net, hT, hS, hN

    def agg(tgt, isl):
        # split "SL" (loss, level<0) from "TRAIL_SL" (profit-lock, level>=0)
        net = 0.0; hT = hLoss = hLock = hN = 0
        cfg = P._agg_cfg(tgt, isl, STEP)
        for c in cov:
            st, rs = P.replay_aggr(c["win"], c["side"], c["ep"], c["qty"],
                                   cfg, c["lots"], c["fallback"])
            net += rs
            if st == "TARGET": hT += 1
            elif st == "TRAIL_SL": hLock += 1     # profit locked by trail (>=0)
            elif st == "SL": hLoss += 1           # real stop-loss (<0)
            else: hN += 1
        return net, hT, hLoss, hLock, hN

    pc = lambda x: f"{100*x/n:>4.0f}%"
    for label, r in RATIOS:
        print("=" * 90)
        print(f"RATIO {label}   (Target = {r} x SL)   [per-lot Rs]   n={n} covered")
        print("=" * 90)
        print(f"  {'SL':>6} {'Tgt':>6} |  FIXED: {'net':>9} {'Tgt':>5} {'SL':>5} {'rode':>5}"
              f"  |  AGGR: {'net':>9} {'Tgt':>5} {'loss':>5} {'lock':>5} {'rode':>5}")
        for sl in SLS:
            tgt = sl * r
            ln, lT, lS, lN = leg(tgt, sl)
            an, aT, aLoss, aLock, aN = agg(tgt, sl)
            print(f"  {sl:>6,} {tgt:>6,} |  {'':7}{_inr(ln):>9} {pc(lT)} {pc(lS)} {pc(lN)}"
                  f"  |  {'':6}{_inr(an):>9} {pc(aT)} {pc(aLoss)} {pc(aLock)} {pc(aN)}")
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-21")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
