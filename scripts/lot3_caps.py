#!/usr/bin/env python3
"""User's idea: 3 lots + max 1-2 trades/day + Rs5000 daily profit-lock.
Consistency-focused. Real Zerodha charges + DOM slippage (bs.slip_cost_leg,
scales with 3x qty). Per-(strategy,date) cap; account-level daily consistency.

Run on VPS: venv/bin/python scripts/lot3_caps.py
"""
import sys, os, argparse, statistics
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import capital_pool_sim as cps

LOTS = 3
# (maxTrades/strat/day, Rs profit-lock/strat/day)
CONFIGS = [
    (1, 5000), (2, 5000), (3, 5000),
    (1, 3000), (2, 3000),
    (2, 4000),
    (5, 3000),   # current live config (reference)
]


def _inr(x): return f"{round(x):,}"


def load(dfrom, dto):
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    raw = [t for t in raw if t.get("pnl") is not None]
    recs = []
    for t in raw:
        side = str(t["entry"]).upper(); qty = int(t["qty"]) * LOTS
        gross = float(t["pnl"]) * LOTS
        tr = {"side": side, "qty": qty, "ep": float(t["entry_price"]),
              "und": cps._underlying(t.get("sym") or "")}
        try:
            ch = cps.leg_charges(tr, gross, False, t.get("entry_date") or "")
        except Exception:
            ch = 0.0
        recs.append({"strat": t.get("strategy") or "?", "date": t.get("entry_date") or "",
                     "et": (t.get("entry_time") or "09:15")[:5], "net": gross - ch})
    return recs


def sim(recs, maxT, lock, tr_d, oo_d):
    g = defaultdict(list)
    for r in recs:
        g[(r["strat"], r["date"])].append(r)
    taken = 0; day = defaultdict(float); wins = 0.0; loss = 0.0; nt = 0
    for items in g.values():
        items.sort(key=lambda x: x["et"])
        cum = 0.0; cnt = 0
        for r in items:
            if cnt >= maxT or cum >= lock:
                continue
            cum += r["net"]; cnt += 1; taken += 1; nt += 1
            day[r["date"]] += r["net"]
            if r["net"] > 0: wins += r["net"]
            else: loss += -r["net"]
    dn = [day[d] for d in sorted(day)]
    total = sum(dn)
    tr = round(sum(v for d, v in day.items() if d in tr_d))
    oo = round(sum(v for d, v in day.items() if d in oo_d))
    green = sum(1 for x in dn if x > 0)
    eq = 0.0; pk = 0.0; dd = 0.0
    for x in dn:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    pf = wins / loss if loss else float("inf")
    return dict(total=total, tr=tr, oo=oo, taken=taken, green=green, ndays=len(dn),
                worst=min(dn) if dn else 0, best=max(dn) if dn else 0,
                avg=total/max(1, len(dn)), dd=dd, pf=pf)


def run(dfrom, dto):
    recs = load(dfrom, dto)
    dates = sorted({r["date"] for r in recs}); cut = int(len(dates)*0.65)
    tr_d, oo_d = set(dates[:cut]), set(dates[cut:])
    print(f"\n{LOTS} LOTS · per-strategy cap · real charges + DOM slippage (3x scaled) · "
          f"{len(dates)} days (train ..{dates[cut-1]}, OOS {dates[cut]}..)\n")
    print(f"  {'config':16} {'NET':>9} {'train':>8} {'oos':>8} {'avg/day':>8} "
          f"{'green':>7} {'worst':>8} {'best':>8} {'trd/day':>8} {'maxDD':>8} {'PF':>5}")
    for maxT, lock in CONFIGS:
        s = sim(recs, maxT, lock, tr_d, oo_d)
        lab = f"max{maxT} + Rs{lock}"
        star = "  <- live" if (maxT, lock) == (5, 3000) else ""
        print(f"  {lab:16} {_inr(s['total']):>9} {_inr(s['tr']):>8} {_inr(s['oo']):>8} "
              f"{_inr(s['avg']):>8} {s['green']:>3}/{s['ndays']:<3} {_inr(s['worst']):>8} "
              f"{_inr(s['best']):>8} {s['taken']/s['ndays']:>7.1f} {_inr(s['dd']):>8} {s['pf']:>5.2f}{star}")
    print("\n  avg/day + green-days + worst = 'consistently kitna' ka jawab. trd/day = account-level "
          "(saari strategies mila ke). NET = 17-din total @ 3 lots.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    a = ap.parse_args()
    run(a.dfrom, a.dto)
