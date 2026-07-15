#!/usr/bin/env python3
"""Per-strategy DAILY DISCIPLINE optimiser — does capping each strategy's day
(max trades / profit-lock / loss-cap) beat taking every signal?

User's thesis: cost scales linearly with trade count, edge doesn't. So stop a
strategy for the day once it (a) took N trades, (b) locked +Rs profit, or
(c) lost -Rs. Uses each trade's REAL recorded gross P&L minus real Zerodha
charges + DOM slippage (cps.leg_charges, Rule 6B). No exit-rule change, no pool
— pure 'when to stop trading today' test, per (strategy, date).

Run on VPS: venv/bin/python scripts/daily_caps.py
"""
import sys, os, argparse, itertools
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import capital_pool_sim as cps

INF = 10**9
MAXTR   = [1, 2, 3, 4, 5, INF]
PTGT    = [2000, 3000, 4000, 6000, INF]
LCAP    = [2000, 3000, 4000, INF]


def _inr(x): return f"{round(x):,}"


def load(dfrom, dto):
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    raw = [t for t in raw if t.get("pnl") is not None]
    recs = []
    for t in raw:
        gross = float(t["pnl"])
        sym = t.get("sym") or t.get("symbol") or ""
        tr = {"side": str(t["entry"]).upper(), "qty": int(t["qty"]),
              "ep": float(t["entry_price"]), "und": cps._underlying(sym)}
        try:
            ch = cps.leg_charges(tr, gross, False, t.get("entry_date"))
        except Exception:
            ch = 0.0
        recs.append({"strat": t.get("strategy") or t.get("strat") or "?",
                     "date": t.get("entry_date") or "", "et": (t.get("entry_time") or "09:15")[:5],
                     "net": gross - ch})
    return recs


def apply_caps(recs, max_tr, ptgt, lcap):
    g = defaultdict(list)
    for r in recs:
        g[(r["strat"], r["date"])].append(r)
    total = 0.0; taken = 0; skipped = 0
    day_nets = defaultdict(float)
    for items in g.values():
        items.sort(key=lambda x: x["et"])
        cum = 0.0; cnt = 0
        for it in items:
            if cnt >= max_tr or cum >= ptgt or cum <= -lcap:
                skipped += 1; continue
            cum += it["net"]; cnt += 1; taken += 1
            total += it["net"]; day_nets[it["date"]] += it["net"]
    return total, taken, skipped, day_nets


def run(dfrom, dto):
    recs = load(dfrom, dto)
    dates = sorted({r["date"] for r in recs})
    cut = int(len(dates) * 0.65)
    train_d, oos_d = set(dates[:cut]), set(dates[cut:])
    print(f"\nTrades {len(recs)} | {len(dates)} days | train ..{dates[cut-1]} OOS {dates[cut]}..\n")

    def split(day_nets):
        return (round(sum(v for d, v in day_nets.items() if d in train_d)),
                round(sum(v for d, v in day_nets.items() if d in oos_d)))

    base_net, base_tk, _, base_dn = apply_caps(recs, INF, INF, INF)
    btr, boo = split(base_dn)
    print(f"BASELINE (no caps): net {_inr(base_net)} | train {_inr(btr)} | oos {_inr(boo)} | "
          f"trades {base_tk}\n")

    # ── one lever at a time ──
    print("── LEVER: max trades/day only ──")
    for m in MAXTR[:-1]:
        n, tk, sk, dn = apply_caps(recs, m, INF, INF); tr, oo = split(dn)
        print(f"  max {m}: net {_inr(n):>9} (train {_inr(tr):>8} oos {_inr(oo):>8}) taken {tk} skip {sk}")
    print("\n── LEVER: daily profit-lock only ──")
    for p in PTGT[:-1]:
        n, tk, sk, dn = apply_caps(recs, INF, p, INF); tr, oo = split(dn)
        print(f"  +Rs{p}: net {_inr(n):>9} (train {_inr(tr):>8} oos {_inr(oo):>8}) taken {tk} skip {sk}")
    print("\n── LEVER: daily loss-cap only ──")
    for l in LCAP[:-1]:
        n, tk, sk, dn = apply_caps(recs, INF, INF, l); tr, oo = split(dn)
        print(f"  -Rs{l}: net {_inr(n):>9} (train {_inr(tr):>8} oos {_inr(oo):>8}) taken {tk} skip {sk}")

    # ── full grid, ranked by MIN(train,OOS) robust ──
    rows = []
    for m, p, l in itertools.product(MAXTR, PTGT, LCAP):
        if m == INF and p == INF and l == INF:
            continue
        n, tk, sk, dn = apply_caps(recs, m, p, l); tr, oo = split(dn)
        rows.append(dict(m=m, p=p, l=l, net=n, tk=tk, sk=sk, tr=tr, oo=oo, mn=min(tr, oo)))
    rows.sort(key=lambda r: r["mn"], reverse=True)
    lbl = lambda v: "∞" if v >= INF else str(v)
    print("\n── FULL GRID top 15 by MIN(train,OOS) ──")
    print(f"  {'maxTr':>5} {'+lock':>6} {'-cap':>6} | {'net':>9} {'train':>8} {'oos':>8} "
          f"{'taken':>6} {'skip':>5}")
    for r in rows[:15]:
        print(f"  {lbl(r['m']):>5} {lbl(r['p']):>6} {lbl(r['l']):>6} | "
              f"{_inr(r['net']):>9} {_inr(r['tr']):>8} {_inr(r['oo']):>8} {r['tk']:>6} {r['sk']:>5}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    a = ap.parse_args()
    run(a.dfrom, a.dto)
