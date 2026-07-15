#!/usr/bin/env python3
"""COMBINED framework test:
  per-TRADE  = tight-SL trail (init SL 500, step 200, ride) -> shrinks rode, locks peak
  per-DAY    = max 5 trades + Rs3000 profit-lock per (strategy,date)

Compares 4 configs on the same trades (real Zerodha charges + DOM slip):
  A actual exits,  no caps      (baseline)
  B actual exits,  max5 + +3000
  C tight-SL exits, no caps
  D tight-SL exits, max5 + +3000   <- the combined framework

Run on VPS: venv/bin/python scripts/combined.py [--no-fetch]
"""
import sys, os, argparse
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import path_aware_sl_sim as pas
import capital_pool_sim as cps

TIGHT = dict(target_per_lot=3000, initial_sl_per_lot=500, favour_step=200, sl_move=200,
             aggressive_pct=20, aggressive_mult=2.0, min_cushion=0)
MAXTR, PLOCK, LCAP = 5, 3000, 10**9


def _inr(x): return f"{round(x):,}"


def load(dfrom, dto, allow_fetch):
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    raw = [t for t in raw if t.get("pnl") is not None]
    recs = []
    for t in raw:
        side = str(t["entry"]).upper(); ep = float(t["entry_price"]); qty = int(t["qty"])
        g_act = float(t["pnl"]); sym = t.get("sym") or ""; sec_id = t.get("sec_id")
        date = t.get("entry_date") or ""; et = (t.get("entry_time") or "09:15")[:5]
        xt = (t.get("exit_time") or "15:15")[:5]
        lots = 1
        if pas.dhan_master and sec_id:
            try:
                ls = pas.dhan_master.get_lot_size_by_sec_id(sec_id)
                if ls and qty: lots = max(1, round(qty/float(ls)))
            except Exception: pass
        bars = pas.load_bars(sec_id, sym, date, allow_fetch=allow_fetch)
        win = pas._window(bars, et, xt, hold_eod=True)   # entry..EOD (tight-SL rides till trail/EOD)
        fb = pas._mtm(side, ep, win[-1][4], qty) if win else g_act
        if win:
            g_tight = pas.replay_aggr(win, side, ep, qty, TIGHT, lots, fb, ride=True)[1]
        else:
            g_tight = g_act
        tr = {"side": side, "qty": qty, "ep": ep, "und": cps._underlying(sym)}
        try:
            ch_a = cps.leg_charges(tr, g_act, False, date)
            ch_t = cps.leg_charges(tr, g_tight, False, date)
        except Exception:
            ch_a = ch_t = 0.0
        recs.append({"strat": t.get("strategy") or "?", "date": date, "et": et,
                     "na": g_act - ch_a, "nt": g_tight - ch_t})
    return recs


def sim(recs, key, capped):
    g = defaultdict(list)
    for r in recs:
        g[(r["strat"], r["date"])].append(r)
    total = 0.0; taken = 0
    day = defaultdict(float)
    for items in g.values():
        items.sort(key=lambda x: x["et"])
        cum = 0.0; cnt = 0
        for it in items:
            if capped and (cnt >= MAXTR or cum >= PLOCK or cum <= -LCAP):
                continue
            v = it[key]; cum += v; cnt += 1; taken += 1
            total += v; day[it["date"]] += v
    return total, taken, day


def _split(day, tr_d, oo_d):
    return (round(sum(v for d, v in day.items() if d in tr_d)),
            round(sum(v for d, v in day.items() if d in oo_d)))


def _maxdd(day):
    eq = 0.0; peak = 0.0; dd = 0.0
    for d in sorted(day):
        eq += day[d]; peak = max(peak, eq); dd = max(dd, peak - eq)
    return dd


def run(dfrom, dto, allow_fetch):
    recs = load(dfrom, dto, allow_fetch)
    dates = sorted({r["date"] for r in recs}); cut = int(len(dates)*0.65)
    tr_d, oo_d = set(dates[:cut]), set(dates[cut:])
    print(f"\nTrades {len(recs)} | {len(dates)} days | train ..{dates[cut-1]} OOS {dates[cut]}..")
    print(f"tight-SL cfg: init {TIGHT['initial_sl_per_lot']}/lot, step {TIGHT['favour_step']}, ride")
    print(f"daily caps: max {MAXTR} trades + Rs{PLOCK} profit-lock\n")

    configs = [("A actual, no caps",  "na", False),
               ("B actual + caps",    "na", True),
               ("C tight-SL, no caps", "nt", False),
               ("D tight-SL + caps",  "nt", True)]
    print(f"  {'config':22s} {'net':>9} {'train':>8} {'oos':>8} {'taken':>6} {'maxDD':>8}")
    for label, key, capped in configs:
        net, tk, day = sim(recs, key, capped)
        tr, oo = _split(day, tr_d, oo_d)
        print(f"  {label:22s} {_inr(net):>9} {_inr(tr):>8} {_inr(oo):>8} {tk:>6} {_inr(_maxdd(day)):>8}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
