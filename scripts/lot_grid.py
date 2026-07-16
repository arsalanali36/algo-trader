#!/usr/bin/env python3
"""Grid: lots {2,3,5} x maxTrades {4,5,6} x profit-lock {5000,6000}.
Per-strategy cap, actual exits, real Zerodha charges + DOM slippage (scaled with
lots). Grouped by lot size. 'min(tr,oo)' = robustness (both halves must hold).

Run on VPS: venv/bin/python scripts/lot_grid.py
"""
import sys, os, argparse
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import capital_pool_sim as cps

LOTSIZES = [2, 3, 5]
MAXTR = [4, 5, 6]
LOCK = [5000, 6000]


def _inr(x): return f"{round(x):,}"


def load_raw(dfrom, dto):
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    return [t for t in raw if t.get("pnl") is not None]


def priced(raw, lots):
    recs = []
    for t in raw:
        side = str(t["entry"]).upper(); qty = int(t["qty"]) * lots
        gross = float(t["pnl"]) * lots
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
    day = defaultdict(float); wins = 0.0; loss = 0.0; taken = 0
    for items in g.values():
        items.sort(key=lambda x: x["et"])
        cum = 0.0; cnt = 0
        for r in items:
            if cnt >= maxT or cum >= lock:
                continue
            cum += r["net"]; cnt += 1; taken += 1; day[r["date"]] += r["net"]
            if r["net"] > 0: wins += r["net"]
            else: loss += -r["net"]
    dn = [day[d] for d in sorted(day)]
    tr = round(sum(v for d, v in day.items() if d in tr_d))
    oo = round(sum(v for d, v in day.items() if d in oo_d))
    eq = 0.0; pk = 0.0; dd = 0.0
    for x in dn:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    return dict(total=sum(dn), tr=tr, oo=oo, mn=min(tr, oo), green=sum(1 for x in dn if x > 0),
                nd=len(dn), worst=min(dn) if dn else 0, avg=sum(dn)/max(1, len(dn)),
                dd=dd, pf=wins/loss if loss else 99, tpd=taken/max(1, len(dn)))


def run(dfrom, dto):
    raw = load_raw(dfrom, dto)
    dates = sorted({(t.get("entry_date") or "") for t in raw}); cut = int(len(dates)*0.65)
    tr_d, oo_d = set(dates[:cut]), set(dates[cut:])
    print(f"\nGrid · per-strategy cap · real charges + DOM slippage (lot-scaled) · "
          f"{len(dates)} days (train ..{dates[cut-1]}, OOS {dates[cut]}..)")
    for lots in LOTSIZES:
        recs = priced(raw, lots)
        print(f"\n=== {lots} LOTS ===")
        print(f"  {'config':16} {'NET':>9} {'train':>8} {'oos':>8} {'min(t,o)':>9} "
              f"{'avg/day':>8} {'green':>6} {'worst':>9} {'maxDD':>9} {'trd/d':>6} {'PF':>5}")
        rows = []
        for maxT in MAXTR:
            for lock in LOCK:
                s = sim(recs, maxT, lock, tr_d, oo_d)
                rows.append((maxT, lock, s))
        rows.sort(key=lambda x: x[2]["mn"], reverse=True)
        for maxT, lock, s in rows:
            rob = " ✓" if s["mn"] > 0 else ""
            print(f"  max{maxT}+Rs{lock:<5} {_inr(s['total']):>9} {_inr(s['tr']):>8} "
                  f"{_inr(s['oo']):>8} {_inr(s['mn']):>9}{rob:<2} {_inr(s['avg']):>7} "
                  f"{s['green']:>2}/{s['nd']:<2} {_inr(s['worst']):>9} {_inr(s['dd']):>9} "
                  f"{s['tpd']:>5.1f} {s['pf']:>5.2f}")
    print("\n  ✓ = robust (train AND oos dono positive). worst = sabse bura din. "
          "trd/d = account-level trades/day. NET/worst/DD lot-size ke saath scale karte.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    a = ap.parse_args()
    run(a.dfrom, a.dto)
