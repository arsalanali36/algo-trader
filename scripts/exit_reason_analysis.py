#!/usr/bin/env python3
"""Why are trades exiting EARLY? — exit-reason breakdown + money-left-on-table.

The ride/cushion sweep showed the deployed aggressive trail COULD have made
~₹73k on the covered trades vs the ~₹9k actually booked — i.e. something is
closing positions BEFORE the trail gets to work. This script attributes that
gap to a cause.

For every completed trade in the range it:
  1. reads its recorded exit_reason (the WHY-it-closed tag on the exit leg),
  2. replays the trade's REAL 1-min premium bars from entry → EOD (15:15) under
     the DEPLOYED aggressive trail (6000/2500/100, cushion 0) — i.e. 'what this
     trade would have done if nothing had cut it short',
  3. left_on_table = aggressive-hold-to-EOD  −  actual booked P&L.

Then it groups by exit_reason so you can see which causes (RMS cap, manual
close, EOD squareoff, a strategy's own ATR/RSI exit, the trail itself, blank…)
are leaving the most money on the table.

Honest limits (same as path_aware_sl_sim):
  • Only ~66% of trades have real bars (expired weekly index = NO-DATA, TRAP
    #100) — left-on-table is summed over the COVERED trades only, count shown.
  • 'hold to EOD' ignores WHY the real exit happened (a daily-loss cap firing is
    protecting you on a bad day; holding everything to 15:15 would breach caps
    on those days). So left-on-table is an opportunity CEILING per reason, a
    place to look — not a 'you should have made this' promise. Read it as
    'which exit causes most deserve a closer look', not a P&L you forfeited.

Run ON THE VPS:
    venv/bin/python scripts/exit_reason_analysis.py [--from 2026-06-21] [--to ...]
                                                    [--no-fetch] [--csv out.csv]
"""
import sys, os, csv, argparse
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import _paths  # noqa
import order_store
# reuse the REAL replay + bar-loading — no second copy of the sim (Rule 6B).
from path_aware_sl_sim import load_bars, replay_aggr, _window, _mtm
try:
    import dhan_master
except Exception:
    dhan_master = None

# The DEPLOYED aggressive trail (7 discretionary strategies, 2026-07-14).
DEPLOYED_AGGR = dict(target_per_lot=6000, initial_sl_per_lot=2500, favour_step=100,
                     sl_move=100, aggressive_pct=30, aggressive_mult=2.0, min_cushion=0)


def _dur_min(entry_date, entry_time, exit_date, exit_time):
    """Holding minutes from entry to exit (HH:MM). None if unparseable."""
    try:
        e = datetime.strptime(f"{entry_date} {entry_time}", "%Y-%m-%d %H:%M")
        x = datetime.strptime(f"{exit_date} {exit_time}", "%Y-%m-%d %H:%M")
        return max(0, int((x - e).total_seconds() // 60))
    except Exception:
        return None


def _lots(sec_id, qty):
    if dhan_master and sec_id:
        try:
            ls = dhan_master.get_lot_size_by_sec_id(sec_id)
            if ls and qty:
                return max(1, round(qty / float(ls)))
        except Exception:
            pass
    return 1


def main():
    ap = argparse.ArgumentParser()
    ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    ap.add_argument("--from", dest="dfrom", default="2026-06-21")
    ap.add_argument("--to", dest="dto", default=ist.strftime("%Y-%m-%d"))
    ap.add_argument("--no-fetch", action="store_true", help="disk bars only, no Dhan calls")
    ap.add_argument("--csv", default=os.path.join(ROOT, "data", "exit_reason_analysis.csv"))
    args = ap.parse_args()

    trades = order_store.trades_for_range(args.dfrom, args.dto)["details"]
    trades = [t for t in trades if t.get("pnl") is not None]
    print(f"Completed trades {args.dfrom}..{args.dto}: {len(trades)}", flush=True)

    # reason -> aggregates
    G = {}
    rows_out = []
    n_cov = 0
    for i, t in enumerate(trades):
        reason = t.get("exit_reason") or "(blank)"
        side, ep, qty = t["entry"], t["entry_price"], t["qty"]
        actual = float(t["pnl"])
        sec_id, trad_sym = t.get("sec_id"), t.get("sym") or ""
        date_str = t.get("entry_date") or ""
        et, xt = t.get("entry_time", ""), t.get("exit_time", "")
        lots = _lots(sec_id, qty)
        dur = _dur_min(date_str, et, t.get("exit_date") or date_str, xt)

        # hold entry..EOD (ignore actual exit), replay deployed aggressive trail.
        bars = load_bars(sec_id, trad_sym, date_str, allow_fetch=not args.no_fetch)
        win = _window(bars, et, "", hold_eod=True)   # xt ignored in hold_eod
        covered = len(win) >= 1
        held = None
        if covered:
            n_cov += 1
            fb = _mtm(side, ep, win[-1][4], qty)      # EOD-close = no-fire fallback
            _, held = replay_aggr(win, side, ep, qty, DEPLOYED_AGGR, lots, fb)

        g = G.setdefault(reason, {"n": 0, "actual": 0.0, "dur": [], "cov": 0,
                                  "held": 0.0, "cov_actual": 0.0})
        g["n"] += 1
        g["actual"] += actual
        if dur is not None:
            g["dur"].append(dur)
        if covered:
            g["cov"] += 1
            g["held"] += held
            g["cov_actual"] += actual

        rows_out.append({"date": date_str, "sym": trad_sym, "strategy": t.get("strategy"),
                         "reason": reason, "actual": round(actual, 2),
                         "held_eod_aggr": round(held, 2) if held is not None else "NO_DATA",
                         "left_on_table": round(held - actual, 2) if held is not None else "",
                         "dur_min": dur, "entry_time": et, "exit_time": xt})
        if (i + 1) % 50 == 0:
            print(f"  ...{i+1}/{len(trades)}  covered={n_cov}", flush=True)

    if rows_out:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader(); w.writerows(rows_out)

    def _inr(x): return f"{round(x):,}"
    # rank reasons by money left on table (covered only), biggest first.
    ranked = sorted(G.items(), key=lambda kv: (kv[1]["held"] - kv[1]["cov_actual"]),
                    reverse=True)

    print("\n" + "=" * 100)
    print(f"EXIT-REASON BREAKDOWN  ({n_cov}/{len(trades)} covered = "
          f"{100*n_cov/max(1,len(trades)):.0f}% had real bars)")
    print("=" * 100)
    hdr = (f"{'exit reason':26s} {'n':>4} {'actual₹':>11} {'avg₹':>8} "
           f"{'avgMin':>7} {'cov':>4} {'held-EOD₹':>11} {'LEFT-ON-TABLE₹':>15}")
    print(hdr); print("-" * 100)
    tot_left = 0.0
    for reason, g in ranked:
        avg = g["actual"] / g["n"]
        avgmin = (sum(g["dur"]) / len(g["dur"])) if g["dur"] else 0
        left = g["held"] - g["cov_actual"]     # covered-only, apples-to-apples
        tot_left += left
        held_str = _inr(g["held"]) if g["cov"] else "—"
        left_str = _inr(left) if g["cov"] else "—"
        print(f"{reason[:26]:26s} {g['n']:>4} {_inr(g['actual']):>11} "
              f"{_inr(avg):>8} {avgmin:>7.0f} {g['cov']:>4} {held_str:>11} {left_str:>15}")
    print("-" * 100)
    print(f"{'TOTAL left on table (covered)':26s} "
          f"{'':>4} {'':>11} {'':>8} {'':>7} {'':>4} {'':>11} {_inr(tot_left):>15}")
    print("\nLEFT-ON-TABLE = (deployed-aggressive trail held to 15:15) − actual booked,"
          "\n  covered trades only. POSITIVE = that exit cause cut trades short vs letting"
          "\n  the trail run. It is an opportunity CEILING (ignores WHY the exit fired —"
          "\n  a daily-loss cap is protecting you), so read big numbers as 'look here', not"
          "\n  'you lost this'.")
    print(f"\nCSV (per-trade): {args.csv}")


if __name__ == "__main__":
    main()
