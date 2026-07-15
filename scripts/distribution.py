#!/usr/bin/env python3
"""Risk distribution of the framework (max5 + Rs3000 lock, actual exits, no SL).

Shows what the user actually feels:
  1. per-trade realised P&L distribution (the loss tail)
  2. per-trade MAX HEAT = deepest adverse excursion Rs/lot BEFORE the trade exited
     (the fear: 'no SL means a trade can dig deep' — how deep, how often?)
  3. deep-heat OUTCOME: of trades that dug >X/lot, how many recovered to a win vs
     stayed a loss, and their net contribution (does riding the heat pay?)
  4. per-DAY net distribution (worst day)

Run on VPS: venv/bin/python scripts/distribution.py [--no-fetch]
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
        lots = 1
        if pas.dhan_master and sec_id:
            try:
                ls = pas.dhan_master.get_lot_size_by_sec_id(sec_id)
                if ls and qty: lots = max(1, round(qty/float(ls)))
            except Exception: pass
        bars = pas.load_bars(sec_id, sym, date, allow_fetch=allow_fetch)
        win = pas._window(bars, et, xt, hold_eod=False)
        # max heat (deepest adverse Rs, whole position) during the trade's life
        heat = 0.0
        for (hh, o, h, l, c) in win:
            adv = pas._mtm(side, ep, h, qty) if side == "SELL" else pas._mtm(side, ep, l, qty)
            heat = min(heat, adv)     # adv negative = loss
        tr = {"side": side, "qty": qty, "ep": ep, "und": cps._underlying(sym)}
        try:
            ch = cps.leg_charges(tr, actual, False, date)
        except Exception:
            ch = 0.0
        recs.append({"strat": t.get("strategy") or "?", "date": date, "et": et,
                     "net": actual - ch, "lots": lots, "covered": len(win) >= 1,
                     "heat_lot": (-heat / max(1, lots)) if win else None})
    return recs


def take_framework(recs):
    g = defaultdict(list)
    for r in recs:
        g[(r["strat"], r["date"])].append(r)
    taken = []; day = defaultdict(float)
    for items in g.values():
        items.sort(key=lambda x: x["et"])
        cum = 0.0; cnt = 0
        for it in items:
            if cnt >= MAXTR or cum >= PLOCK:
                continue
            cum += it["net"]; cnt += 1; taken.append(it); day[it["date"]] += it["net"]
    return taken, day


def hist(vals, edges, labels):
    counts = [0]*len(labels)
    for v in vals:
        for i in range(len(edges)-1):
            if edges[i] <= v < edges[i+1]:
                counts[i] += 1; break
    n = max(1, len(vals))
    for lab, c in zip(labels, counts):
        bar = "█" * round(40*c/n)
        print(f"  {lab:>16} | {c:>4} ({100*c/n:>4.1f}%) {bar}")


def run(dfrom, dto, allow_fetch):
    recs = load(dfrom, dto, allow_fetch)
    taken, day = take_framework(recs)
    nets = [t["net"] for t in taken]
    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x <= 0]
    print(f"\nFramework: max{MAXTR} + Rs{PLOCK} lock, actual exits.  Taken {len(taken)} trades, {len(day)} days")
    print(f"Total net Rs {_inr(sum(nets))} | win {100*len(wins)/max(1,len(nets)):.0f}% "
          f"| avg win Rs {_inr(sum(wins)/max(1,len(wins)))} | avg loss Rs {_inr(sum(losses)/max(1,len(losses)))}")
    print(f"WORST trade Rs {_inr(min(nets))} | BEST trade Rs {_inr(max(nets))}")

    print("\n=== 1. Per-trade NET P&L distribution ===")
    hist(nets, [-1e9, -4000, -2000, -1000, 0, 1000, 2000, 4000, 1e9],
         ["< -4000", "-4000..-2000", "-2000..-1000", "-1000..0",
          "0..1000", "1000..2000", "2000..4000", "> 4000"])

    print("\n=== 2. Per-trade MAX HEAT (deepest adverse Rs/lot before exit) ===")
    heats = [t["heat_lot"] for t in taken if t["heat_lot"] is not None]
    print(f"  (covered {len(heats)} trades; deepest heat Rs {_inr(max(heats))}/lot)")
    hist(heats, [0, 500, 1000, 2000, 3000, 5000, 1e9],
         ["0..500", "500..1000", "1000..2000", "2000..3000", "3000..5000", "> 5000"])

    print("\n=== 3. Deep-heat OUTCOME: did riding the heat pay? ===")
    for thr in (2000, 3000, 5000):
        deep = [t for t in taken if t["heat_lot"] is not None and t["heat_lot"] > thr]
        if not deep:
            print(f"  heat > Rs{thr}/lot: none"); continue
        rec = [t for t in deep if t["net"] > 0]
        contrib = sum(t["net"] for t in deep)
        print(f"  heat > Rs{thr}/lot: {len(deep):>3} trades | {len(rec)} recovered to WIN "
              f"({100*len(rec)/len(deep):.0f}%) | net contribution Rs {_inr(contrib)}")

    print("\n=== 4. Per-DAY net distribution ===")
    dn = list(day.values())
    print(f"  WORST day Rs {_inr(min(dn))} | BEST day Rs {_inr(max(dn))} | "
          f"green days {sum(1 for x in dn if x>0)}/{len(dn)}")
    hist(dn, [-1e9, -5000, -3000, -1000, 0, 1000, 3000, 5000, 1e9],
         ["< -5000", "-5000..-3000", "-3000..-1000", "-1000..0",
          "0..1000", "1000..3000", "3000..5000", "> 5000"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
