#!/usr/bin/env python3
"""Mechanism A vs B for the Rs3000 daily profit-lock (max5, actual exits).

A = block NEW entries once realised day-total >= 3000; the current trade runs to
    its OWN exit (can overshoot, can give back).
B = the instant day-total (realised + the open trade's LIVE favourable MTM)
    TOUCHES 3000, close the open trade there -> day locks at exactly 3000.
    This is give-back protection: even if that trade later reverses to a loss,
    B banked the +3000. Bar-level (covered trades) so the give-back is real.

Compares net / train / oos / maxDD / PF / win% / avg-day.

Run on VPS: venv/bin/python scripts/mechanism_b.py [--no-fetch]
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
        bars = pas.load_bars(sec_id, sym, date, allow_fetch=allow_fetch)
        win = pas._window(bars, et, xt, hold_eod=False)
        tr = {"side": side, "qty": qty, "ep": ep, "und": cps._underlying(sym)}
        try:
            ch = cps.leg_charges(tr, actual, False, date)
        except Exception:
            ch = 0.0
        recs.append({"strat": t.get("strategy") or "?", "date": date, "et": et,
                     "side": side, "ep": ep, "qty": qty, "actual_g": actual,
                     "charge": ch, "win": win})
    return recs


def _fav_touch_gross(rec, need_gross):
    """Does this trade's LIVE favourable MTM reach `need_gross` before its own
    exit? Returns need_gross if yes (locked there), else the trade's actual gross."""
    if need_gross <= 0:
        return need_gross
    for (hh, o, h, l, c) in rec["win"]:
        fav = pas._mtm(rec["side"], rec["ep"], l, rec["qty"]) if rec["side"] == "SELL" \
            else pas._mtm(rec["side"], rec["ep"], h, rec["qty"])
        if fav >= need_gross:
            return need_gross
    return rec["actual_g"]


def sim(recs, mode):
    g = defaultdict(list)
    for r in recs:
        g[(r["strat"], r["date"])].append(r)
    total = 0.0; day = defaultdict(float); taken = 0
    wins = 0.0; loss = 0.0; nwin = 0; ntr = 0
    for items in g.values():
        items.sort(key=lambda x: x["et"])
        cum_g = 0.0; cnt = 0
        for it in items:
            if cnt >= MAXTR or cum_g >= PLOCK:
                continue
            if mode == "A":
                g_contrib = it["actual_g"]
            else:  # B: lock the day at 3000 if this trade's fav touches it
                need = PLOCK - cum_g
                g_contrib = _fav_touch_gross(it, need)
            net = g_contrib - it["charge"]
            cum_g += g_contrib; cnt += 1; taken += 1; ntr += 1
            total += net; day[it["date"]] += net
            if net > 0: wins += net; nwin += 1
            else: loss += -net
    return total, day, taken, wins, loss, nwin, ntr


def _dd(day):
    eq = 0.0; pk = 0.0; dd = 0.0
    for d in sorted(day):
        eq += day[d]; pk = max(pk, eq); dd = max(dd, pk - eq)
    return dd


def run(dfrom, dto, allow_fetch):
    recs = load(dfrom, dto, allow_fetch)
    dates = sorted({r["date"] for r in recs}); cut = int(len(dates)*0.65)
    tr_d, oo_d = set(dates[:cut]), set(dates[cut:])
    print(f"\nmax{MAXTR} + Rs{PLOCK} lock | {len(recs)} trades | {len(dates)} days "
          f"(train ..{dates[cut-1]}, OOS {dates[cut]}..)\n")
    print(f"  {'mode':>26} {'net':>9} {'train':>8} {'oos':>8} {'PF':>5} {'win%':>5} "
          f"{'maxDD':>8} {'taken':>6}")
    for mode, lab in (("A", "A (current trade rides)"), ("B", "B (hard-lock at 3000)")):
        net, day, tk, w, l, nw, nt = sim(recs, mode)
        tr = round(sum(v for d, v in day.items() if d in tr_d))
        oo = round(sum(v for d, v in day.items() if d in oo_d))
        pf = w/l if l else float("inf")
        print(f"  {lab:>26} {_inr(net):>9} {_inr(tr):>8} {_inr(oo):>8} {pf:>5.2f} "
              f"{100*nw/max(1,nt):>4.0f}% {_inr(_dd(day)):>8} {tk:>6}")
    print("\n  B lower net but caps give-back — dekho OOS/maxDD me farak.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
