"""BACKTEST vs LIVE reconciliation — how much does each deployed strategy's LIVE/paper
result actually agree with its BACKTEST run, day by day?

Why this exists: a backtest is only trustworthy if the LIVE bot runs the SAME logic.
In this repo the backtest engine (intraday_engine/run_hunt) and the live traders
(orb_trader.py, range_trader.py, ...) are TWO different implementations that were never
proven signal-identical. This tool quantifies the gap so we know exactly which
strategies diverge, on which days, and by how much — the ground truth that a
single-source-of-truth refactor (making live call the backtest signal) must close.

Read-only: reads runs/<slug>/results.js (via backtest_calendar) + order_store. No order
path, no writes.

    python _ops/backtest_live_recon.py                    # last ~15 days, all deployed runs
    python _ops/backtest_live_recon.py --from 2026-07-13 --to 2026-07-22
    python _ops/backtest_live_recon.py --strat orb_v1     # one strategy
    python _ops/backtest_live_recon.py --json             # machine-readable

Caveat honestly surfaced: LIVE multi-leg structures (straddle=CE+PE, condor…) record
each LEG as its own order_store round-trip, while the backtest counts the STRUCTURE as
one trade → count columns won't line up for multi-leg strategies even when the P&L is
close. The P&L / same-day presence columns are the meaningful signal-divergence read.
"""
import sys
import os
import json
import argparse
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _paths  # noqa: E402  (sys.path bootstrap)
import order_store  # noqa: E402
import backtest_calendar as bc  # noqa: E402

RUNS_INDEX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "scratch", "nifty_trend", "runs", "index.json")


def _ist_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).date()


def _deployed_pairs():
    """[(run_slug, live_config_key)] for runs that carry a deployed config_key."""
    try:
        idx = json.load(open(RUNS_INDEX, encoding="utf-8"))
    except Exception:
        return []
    return [(r["slug"], r["deployed"]) for r in idx
            if isinstance(r, dict) and isinstance(r.get("deployed"), str) and r.get("deployed")]


def _live_by_date(ck, lo, hi):
    """Live order_store round-trips for one config_key, bucketed by EXIT date."""
    res = order_store.trades_for_range(lo, hi)
    out = {}
    for t in (res.get("details") or []):
        if t.get("strategy") != ck:
            continue
        d = str(t.get("exit_date") or "")[:10]
        if not d:
            continue
        b = out.setdefault(d, {"count": 0, "pnl": 0.0})
        b["count"] += 1
        b["pnl"] += (t.get("pnl") or 0)
    return out


def reconcile(lo, hi, only_ck=None):
    rows = []
    for slug, ck in _deployed_pairs():
        if only_ck and ck != only_ck:
            continue
        try:
            bt = bc.calendar_summary(slug, "bs", "full", from_date=lo, to_date=hi)
        except Exception as e:
            rows.append({"ck": ck, "slug": slug, "error": str(e)})
            continue
        btsum = bt.get("summary", {}) or {}
        lv = _live_by_date(ck, lo, hi)
        days = sorted(set(list(btsum) + list(lv)))
        per_day = []
        both_days = sig_agree = 0
        for d in days:
            b, l = btsum.get(d), lv.get(d)
            bt_has, lv_has = bool(b and (b.get("count") or 0)), bool(l and l["count"])
            if bt_has and lv_has:
                both_days += 1
            # "signal agree" = both traded that day OR both idle
            if bt_has == lv_has:
                sig_agree += 1
            per_day.append({
                "date": d,
                "bt": {"count": (b.get("count") if b else 0), "pnl": round((b.get("pnl") if b else 0) or 0, 0)},
                "live": {"count": (l["count"] if l else 0), "pnl": round(l["pnl"], 0) if l else 0},
                "same_presence": bt_has == lv_has,
            })
        rows.append({
            "ck": ck, "slug": slug,
            "bt_total": {"count": sum((btsum[d].get("count") or 0) for d in btsum),
                         "pnl": round(sum((btsum[d].get("pnl") or 0) for d in btsum), 0)},
            "live_total": {"count": sum(lv[d]["count"] for d in lv),
                           "pnl": round(sum(lv[d]["pnl"] for d in lv), 0)},
            "days": len(days), "presence_agree": sig_agree,
            "presence_score": (round(100 * sig_agree / len(days)) if days else None),
            "per_day": per_day,
        })
    return rows


def _print(rows, lo, hi):
    print(f"BACKTEST vs LIVE reconciliation  ({lo} → {hi})")
    print(f"  presence-agree% = of the days EITHER traded, on how many did BOTH agree "
          f"(both traded, or both idle). Low % = live signal ≠ backtest signal.")
    print("=" * 82)
    for r in rows:
        if r.get("error"):
            print(f"{r['ck']:16s}  SKIP ({r['error']})")
            continue
        bt, lv = r["bt_total"], r["live_total"]
        print(f"\n{r['ck']:16s} (run {r['slug']})   presence-agree {r['presence_score']}%   "
              f"BT {bt['count']:3d}tr/{bt['pnl']:+8.0f}   LIVE {lv['count']:3d}tr/{lv['pnl']:+8.0f}")
        for d in r["per_day"]:
            bts = f"{d['bt']['count']:2d}tr/{d['bt']['pnl']:+7.0f}" if d["bt"]["count"] else "   --    "
            lvs = f"{d['live']['count']:2d}tr/{d['live']['pnl']:+7.0f}" if d["live"]["count"] else "   --    "
            print(f"    {d['date']}   BT {bts}   LIVE {lvs}{'' if d['same_presence'] else '   <-- diverge'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="lo", default=None)
    ap.add_argument("--to", dest="hi", default=None)
    ap.add_argument("--strat", default=None, help="one config_key")
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    hi = a.hi or _ist_today().isoformat()
    lo = a.lo or (datetime.strptime(hi, "%Y-%m-%d").date() - timedelta(days=a.days)).isoformat()
    rows = reconcile(lo, hi, only_ck=a.strat)
    if a.json:
        print(json.dumps({"from": lo, "to": hi, "strategies": rows}, indent=2, default=str))
    else:
        _print(rows, lo, hi)


if __name__ == "__main__":
    main()
