#!/usr/bin/env python3
"""How much FASTER does Fixed 1:1 exit vs the actual live exit — and what does
that do to capital rotation? Faster exit = capital frees sooner = more of the
CAPITAL_BLOCKED signals could have been taken.

Measures per fixed 1:1 SL: avg holding-min (fixed vs actual), % that exit
earlier, rotation multiplier (actual_avg_hold / fixed_avg_hold), win%, net.
Reuses path_aware_sl_sim bar loading (Rule 6B).

Run on VPS: venv/bin/python scripts/rotation.py [--no-fetch]
"""
import sys, os, argparse
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
import path_aware_sl_sim as P

SLS = [1000, 1500, 2000, 2500]   # 1:1 => target == SL


def _mins(hhmm):
    try:
        h, m = hhmm[:5].split(":"); return int(h)*60 + int(m)
    except Exception:
        return None


def _replay_fixed_timed(bars, side, entry_px, qty, target_rs, sl_rs):
    """Fixed target/SL, returns (status, rs, exit_hhmm or None-if-rode)."""
    for (hhmm, o, h, l, c) in bars:
        if side == "SELL":
            adv = P._mtm(side, entry_px, h, qty); fav = P._mtm(side, entry_px, l, qty)
        else:
            adv = P._mtm(side, entry_px, l, qty); fav = P._mtm(side, entry_px, h, qty)
        if adv <= -sl_rs:
            return "SL", -sl_rs, hhmm
        if fav >= target_rs:
            return "TARGET", target_rs, hhmm
    return "RODE", None, None


def _inr(x): return f"{round(x):,}"


def run(dfrom, dto, allow_fetch):
    trades = order_store.trades_for_range(dfrom, dto)["details"]
    trades = [t for t in trades if t.get("pnl") is not None]
    # build covered list keeping entry/exit times
    cov = []
    for t in trades:
        sec_id = t.get("sec_id"); trad_sym = t.get("sym") or ""
        date_str = t.get("entry_date") or ""
        et, xt = t.get("entry_time", ""), t.get("exit_time", "")
        bars = P.load_bars(sec_id, trad_sym, date_str, allow_fetch=allow_fetch)
        win = P._window(bars, et, xt, False)
        if not win:
            continue
        lots = 1
        if P.dhan_master and sec_id:
            try:
                ls = P.dhan_master.get_lot_size_by_sec_id(sec_id)
                if ls and t["qty"]: lots = max(1, round(t["qty"]/float(ls)))
            except Exception: pass
        cov.append(dict(win=win, side=t["entry"], ep=t["entry_price"], qty=t["qty"],
                        lots=lots, actual=float(t["pnl"]), et=et, xt=xt))
    n = len(cov)
    print(f"\nCovered {n}/{len(trades)}\n")

    # actual avg holding minutes (entry->actual exit), where both times known
    act_holds = [(_mins(c["xt"]) - _mins(c["et"])) for c in cov
                 if _mins(c["xt"]) is not None and _mins(c["et"]) is not None
                 and _mins(c["xt"]) >= _mins(c["et"])]
    act_avg = sum(act_holds)/max(1, len(act_holds))
    print(f"ACTUAL avg holding: {act_avg:.0f} min  (n={len(act_holds)})\n")
    print(f"  {'SL(1:1)':>8} {'net':>9} {'win%':>5} {'Tgt%':>5} {'SL%':>5} {'rode%':>5} "
          f"{'avgHold':>8} {'faster%':>8} {'rotation':>9}")
    for sl in SLS:
        net = 0.0; wins = 0; hT = hS = hR = 0
        holds = []; faster = 0; timed = 0
        for c in cov:
            st, rs, xh = _replay_fixed_timed(c["win"], c["side"], c["ep"], c["qty"],
                                             sl*c["lots"], sl*c["lots"])
            if st == "RODE":
                rs2 = c["actual"]; hR += 1
                # rode = exits at actual exit; holding = actual holding
                em = _mins(c["xt"]); sm = _mins(c["et"])
                if em is not None and sm is not None and em >= sm:
                    holds.append(em - sm); timed += 1
            else:
                rs2 = rs
                if st == "TARGET": hT += 1
                else: hS += 1
                em = _mins(xh); sm = _mins(c["et"])
                if em is not None and sm is not None and em >= sm:
                    hm = em - sm; holds.append(hm); timed += 1
                    axm = _mins(c["xt"])
                    if axm is not None and em < axm: faster += 1
            net += rs2
            if rs2 > 0: wins += 1
        avgh = sum(holds)/max(1, len(holds))
        rot = act_avg/max(1e-9, avgh)
        print(f"  {sl:>8,} {_inr(net):>9} {100*wins/n:>4.0f}% {100*hT/n:>4.0f}% "
              f"{100*hS/n:>4.0f}% {100*hR/n:>4.0f}% {avgh:>6.0f}m "
              f"{100*faster/max(1,timed):>6.0f}% {rot:>7.2f}x")
    print("\n  rotation = actual_avg_hold / fixed_avg_hold  (higher = capital frees faster)")
    print("  faster% = of resolved trades, how many exited BEFORE their actual exit time")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default="2026-06-21")
    ap.add_argument("--to", dest="dto", default="2026-07-15")
    ap.add_argument("--no-fetch", action="store_true")
    a = ap.parse_args()
    run(a.dfrom, a.dto, allow_fetch=not a.no_fetch)
