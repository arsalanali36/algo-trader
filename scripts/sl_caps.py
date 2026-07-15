#!/usr/bin/env python3
"""Per-trade DISASTER-SL sweep UNDER the daily-cap framework.

Framework (fixed): max 5 trades/day + Rs3000 realized profit-lock, block new
entries (mechanism A). NO per-trade target (the day-lock books profit).

This sweep asks: keeping each strategy's OWN exit for the UPSIDE, add a hard
per-trade loss-floor of Rs X/lot underneath it — which X? A trade that hits
-X/lot before its actual exit is cut at -X (catches blowups + trades that would
recover are cut too — the trade-off). Winners = untouched (actual). Then the
daily caps run on top. Real Zerodha charges + DOM slip.

Run on VPS: venv/bin/python scripts/sl_caps.py [--no-fetch]
"""
import sys, os, argparse
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import path_aware_sl_sim as pas
import capital_pool_sim as cps

MAXTR, PLOCK, LCAP = 5, 3000, 10**9
SLS = [1000, 1500, 2000, 2500, 3000, None]   # None = no extra SL (= actual exits, config B)


def _inr(x): return f"{round(x):,}"


def _disaster(bars, side, ep, qty, actual, sl_rs):
    """Loss-floor only. If adverse MTM hits -sl_rs before actual exit -> -sl_rs;
    else keep actual (upside untouched)."""
    if sl_rs is None or not bars:
        return actual
    for (hhmm, o, h, l, c) in bars:
        adv = pas._mtm(side, ep, h, qty) if side == "SELL" else pas._mtm(side, ep, l, qty)
        if adv <= -sl_rs:
            return -sl_rs
    return actual


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


def eval_sl(recs, sl, tr_d, oo_d):
    g = defaultdict(list)
    for r in recs:
        gross = _disaster(r["win"], r["side"], r["ep"], r["qty"], r["actual"],
                          (sl * r["lots"]) if sl else None)
        tr = {"side": r["side"], "qty": r["qty"], "ep": r["ep"], "und": r["und"]}
        try:
            ch = cps.leg_charges(tr, gross, False, r["date"])
        except Exception:
            ch = 0.0
        g[(r["strat"], r["date"])].append((r["et"], gross - ch, r["date"]))
    total = 0.0; taken = 0; day = defaultdict(float)
    for items in g.values():
        items.sort(key=lambda x: x[0])
        cum = 0.0; cnt = 0
        for _, net, dt in items:
            if cnt >= MAXTR or cum >= PLOCK or cum <= -LCAP:
                continue
            cum += net; cnt += 1; taken += 1; total += net; day[dt] += net
    tr = round(sum(v for d, v in day.items() if d in tr_d))
    oo = round(sum(v for d, v in day.items() if d in oo_d))
    eq = 0.0; pk = 0.0; dd = 0.0
    for d in sorted(day):
        eq += day[d]; pk = max(pk, eq); dd = max(dd, pk - eq)
    return total, tr, oo, taken, dd


def run(dfrom, dto, allow_fetch):
    recs = load(dfrom, dto, allow_fetch)
    dates = sorted({r["date"] for r in recs}); cut = int(len(dates)*0.65)
    tr_d, oo_d = set(dates[:cut]), set(dates[cut:])
    print(f"\nTrades {len(recs)} | {len(dates)} days | caps: max {MAXTR} + Rs{PLOCK} lock, no target\n")
    print(f"  {'per-trade SL':>14} {'net':>9} {'train':>8} {'oos':>8} {'taken':>6} {'maxDD':>8}")
    for sl in SLS:
        net, tr, oo, tk, dd = eval_sl(recs, sl, tr_d, oo_d)
        lab = "none (actual)" if sl is None else f"Rs{sl}/lot"
        print(f"  {lab:>14} {_inr(net):>9} {_inr(tr):>8} {_inr(oo):>8} {tk:>6} {_inr(dd):>8}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
