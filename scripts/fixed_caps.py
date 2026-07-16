#!/usr/bin/env python3
"""FIXED per-trade target/SL (e.g. SL 500 / target 1000) + daily caps (max5 +
Rs3000 lock). The exact combo we never ran. Per-trade exit = fixed target/SL
(replay_legacy, bar-by-bar, SL-before-target), THEN daily discipline on top.
Compares vs 'actual exits + caps' (the current winner, +23,617).

Real Zerodha charges + DOM slip. Run on VPS: venv/bin/python scripts/fixed_caps.py [--no-fetch]
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
# (label, SL/lot, target/lot)  — None,None = actual exits (baseline winner)
COMBOS = [
    ("actual exits",      None, None),
    ("SL500 / T1000",     500,  1000),
    ("SL500 / T500",      500,  500),
    ("SL1000 / T1000",    1000, 1000),
    ("SL500 / T1500",     500,  1500),
    ("SL1000 / T2000",    1000, 2000),
]


def _inr(x): return f"{round(x):,}"


def load(dfrom, dto, allow_fetch):
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    raw = [t for t in raw if t.get("pnl") is not None]
    recs = []
    for t in raw:
        side = str(t["entry"]).upper(); ep = float(t["entry_price"]); qty = int(t["qty"])
        actual = float(t["pnl"]); sym = t.get("sym") or ""; sec_id = t.get("sec_id")
        date = t.get("entry_date") or ""; et = (t.get("entry_time") or "09:15")[:5]
        xt = (t.get("exit_time") or "15:15")[:5]
        lots = 1
        if pas.dhan_master and sec_id:
            try:
                ls = pas.dhan_master.get_lot_size_by_sec_id(sec_id)
                if ls and qty: lots = max(1, round(qty/float(ls)))
            except Exception: pass
        bars = pas.load_bars(sec_id, sym, date, allow_fetch=allow_fetch)
        win = pas._window(bars, et, xt, hold_eod=False)   # entry..actual exit
        recs.append({"strat": t.get("strategy") or "?", "date": date, "et": et,
                     "side": side, "ep": ep, "qty": qty, "lots": lots,
                     "und": cps._underlying(sym), "actual": actual, "win": win})
    return recs


def per_trade_net(r, sl, tgt):
    if sl is None:                    # actual exit
        gross = r["actual"]
    elif not r["win"]:                # no bars → fall back to actual
        gross = r["actual"]
    else:
        _, gross = pas.replay_legacy(r["win"], r["side"], r["ep"], r["qty"],
                                     tgt*r["lots"], sl*r["lots"], r["actual"])
    tr = {"side": r["side"], "qty": r["qty"], "ep": r["ep"], "und": r["und"]}
    try:
        ch = cps.leg_charges(tr, gross, False, r["date"])
    except Exception:
        ch = 0.0
    return gross - ch


def run(dfrom, dto, allow_fetch):
    recs = load(dfrom, dto, allow_fetch)
    dates = sorted({r["date"] for r in recs}); cut = int(len(dates)*0.65)
    tr_d, oo_d = set(dates[:cut]), set(dates[cut:])
    print(f"\nFIXED SL/target + max{MAXTR} + Rs{PLOCK} lock | {len(recs)} trades | "
          f"{len(dates)} days (train ..{dates[cut-1]}, OOS {dates[cut]}..)\n")
    print(f"  {'config':16} {'NET':>9} {'train':>8} {'oos':>8} {'taken':>6} {'maxDD':>8}")
    for label, sl, tgt in COMBOS:
        g = defaultdict(list)
        for r in recs:
            g[(r["strat"], r["date"])].append((r["et"], per_trade_net(r, sl, tgt), r["date"]))
        total = 0.0; taken = 0; day = defaultdict(float)
        for items in g.values():
            items.sort(key=lambda x: x[0])
            cum = 0.0; cnt = 0
            for _, net, dt in items:
                if cnt >= MAXTR or cum >= PLOCK:
                    continue
                cum += net; cnt += 1; taken += 1; total += net; day[dt] += net
        tr = round(sum(v for d, v in day.items() if d in tr_d))
        oo = round(sum(v for d, v in day.items() if d in oo_d))
        eq = 0.0; pk = 0.0; dd = 0.0
        for d in sorted(day):
            eq += day[d]; pk = max(pk, eq); dd = max(dd, pk - eq)
        mark = "  <- winner" if sl is None else ""
        print(f"  {label:16} {_inr(total):>9} {_inr(tr):>8} {_inr(oo):>8} {taken:>6} {_inr(dd):>8}{mark}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
