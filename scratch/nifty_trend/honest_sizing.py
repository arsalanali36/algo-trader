#!/usr/bin/env python3
"""Honest CAGR — what a run actually returns once SIZING is part of the strategy.

WHY THIS EXISTS
  Every runs/<slug>/results.js is produced at bs_option.reprice(..., lots=1) against a
  hardcoded engine.START_CAP = 1_000_000. So its "net_pct" is:

      1 lot's rupee P&L  /  an arbitrary 10L we typed into engine.py line 21

  and metrics["annual_return"] is that number annualised. It is NOT a CAGR: there is no
  compounding (equity doubles -> next trade is still 1 lot) and the denominator is not
  capital-at-risk (Mid-Day ORB's worst drawdown is ~18k; the other ~9.8L never worked).
  Comparing that figure to an FD is comparing two different questions.

  This script answers the real one: pick a DRAWDOWN BUDGET (the actual binding
  constraint), size lots to it, let lots compound as equity grows, and report the CAGR
  and the realised drawdown that the SAME trade sequence would have produced.

METHOD (per strategy, on combos["bs|full"].all_trades = the real trade sequence)
  1. dd_per_lot  = the run's own Monte-Carlo WORST-5% maxdd, in rupees at 1 lot.
     Trade-order MC is already in results.js (combos[..].mc). Using worst5 instead of
     the realised maxdd means we size against a bad-luck ordering, not the lucky one
     that happened to get recorded. This is the haircut, and it is data-grounded rather
     than a guessed "x1.5 because backtests lie".
  2. lots       = floor(equity * dd_budget / dd_per_lot), recomputed monthly, min 1.
  3. pnl_N      = pnl_1lot * lots   (see CONSERVATIVE below)
  4. equity compounds trade by trade; CAGR + realised maxdd measured off that curve.

FAITHFULNESS (surfaced, not hidden)
  * CONSERVATIVE — linear P&L scaling. Zerodha brokerage is a FLAT Rs20/order, so at N
    lots the per-lot cost actually FALLS; real N-lot P&L is slightly better than linear.
    Linear is design-agnostic (works for straddle/spread/backspread alike, whose legs
    all_trades does not expose), so we take the conservative side. scripts/lot_scale.py
    measures the true cost-dilution on live fills if you want the exact number.
  * all_trades P&L is the bs_full pass, which predates the dom_slip + date-aware-STT
    recost. Where meta.real_cost exists we scale every trade by the recost ratio
    (real_cost.full.net_pct / bs_full.net_pct) so the sim starts from post-slip P&L.
  * maxdd here is measured trade-close to trade-close. The run's own maxdd is
    mark-to-market intra-trade, so it is the stricter number; --check reports both at
    lots=1 so the gap is visible instead of assumed away.
  * Capacity is NOT modelled beyond a premium-deployment check. DOM slip was calibrated
    at small size; sizing far past a few lots on anything but NIFTY/BANKNIFTY index
    options would need a fresh calibration. peak_deploy% is printed for that reason.

Run (from scratch/nifty_trend/):
    python honest_sizing.py --slug mid_orb_nifty --check      # sanity: lots=1 must
                                                              # reproduce real_cost net
    python honest_sizing.py --all --dd-budget 10,15 --max-lots 50
    python honest_sizing.py --all --json honest.json

ALWAYS pass --max-lots. Without a ceiling this compounds a 1686-trade edge into four-figure
crores. That absurdity is a FREE DIAGNOSTIC, not a result: any strategy whose honest CAGR
swings wildly with the cap is telling you its backtest is better than the truth. See
LESSONS.md TRAP #127.
"""
import os
import sys
import json
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
BASE_CAP = 1_000_000.0          # engine.START_CAP — the denominator every run's % uses


def _inr(x):
    """Indian lakh-style grouping: 1234567 -> 12,34,567"""
    s, neg = f"{abs(int(round(x)))}", x < 0
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-" if neg else "") + s


def load_run(slug):
    """runs/<slug>/results.js -> (meta, combo dict for bs|full)."""
    p = os.path.join(RUNS, slug, "results.js")
    with open(p, encoding="utf-8") as f:
        s = f.read()
    s = s[s.index("{"):].rstrip().rstrip(";")
    R = json.loads(s)
    return R["meta"], R["combos"]["bs|full"]


def recost_ratio(meta, combo):
    """Scale bs_full trade P&L onto the post-slip / date-aware-STT recost basis."""
    rc = (meta.get("real_cost") or {}).get("full") or {}
    bs_net = combo["metrics"].get("net_pct")
    rc_net = rc.get("net_pct")
    if not bs_net or rc_net is None or bs_net == 0:
        return 1.0, False
    return rc_net / bs_net, True


def dd_per_lot_rs(combo, use):
    """Rupee drawdown of ONE lot. use='mc_worst5' (default) or 'realised'."""
    m = combo["metrics"]
    realised = abs(m["maxdd"]) / 100.0 * BASE_CAP
    tbl = (combo.get("mc") or {}).get("table") or {}
    dds = tbl.get("maxdd") or []
    # results.js mc.table rows are ordered [original, worst5, median, best5]
    worst5 = abs(dds[1]) / 100.0 * BASE_CAP if len(dds) > 1 else None
    if use == "realised" or not worst5:
        return realised, realised, worst5
    return max(worst5, realised), realised, worst5


def curve_stats(eq, cap, years):
    """CAGR % + maxdd % off an equity curve."""
    peak, mdd = cap, 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = min(mdd, (v - peak) / peak * 100.0)
    final = eq[-1] if eq else cap
    if final <= 0:
        return -100.0, mdd, final
    cagr = ((final / cap) ** (1.0 / years) - 1.0) * 100.0
    return cagr, mdd, final


def simulate(trades, cap, dd_budget, dd_lot, lot_size, ratio, fixed_lots=None,
             max_lots=None):
    """Monthly-resized, compounding lot sizing over the real trade sequence.

    max_lots = capacity ceiling. DOM slip was calibrated at small size; past some lot
    count the fills modelled here stop existing. Without a ceiling this function will
    happily compound a 1686-trade edge into four-figure crores, which is a statement
    about unbounded compounding, not about the strategy.
    """
    equity = cap
    eq, lots_seen, peak_deploy = [], [], 0.0
    cur_month, lots = None, 1
    for t in trades:
        mon = t["entry_dt"][:7]
        if fixed_lots:
            lots = fixed_lots
        elif mon != cur_month:
            cur_month = mon
            lots = max(1, int(equity * dd_budget / dd_lot)) if dd_lot > 0 else 1
            if max_lots:
                lots = min(lots, max_lots)
        lots_seen.append(lots)
        prem = abs(t.get("entry_prem") or 0.0) * lot_size * lots
        if equity > 0:
            peak_deploy = max(peak_deploy, prem / equity * 100.0)
        equity += t["pnl"] * ratio * lots
        eq.append(equity)
    return eq, lots_seen, peak_deploy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--capital", type=float, default=BASE_CAP)
    ap.add_argument("--dd-budget", default="10,15,20,25", help="tolerable maxdd %% list")
    ap.add_argument("--dd-basis", default="mc_worst5", choices=["mc_worst5", "realised"])
    ap.add_argument("--check", action="store_true", help="lots=1 sanity vs recorded metrics")
    ap.add_argument("--max-lots", type=int, default=None,
                    help="capacity ceiling (DOM slip calibrated at small size)")
    ap.add_argument("--json")
    a = ap.parse_args()

    idx = json.load(open(os.path.join(RUNS, "index.json"), encoding="utf-8"))
    if a.slug:
        idx = [r for r in idx if r["slug"] == a.slug]
    elif not a.all:
        ap.error("pass --slug <name> or --all")

    budgets = [float(x) / 100.0 for x in a.dd_budget.split(",")]
    out = []

    for r in idx:
        slug = r["slug"]
        try:
            meta, combo = load_run(slug)
        except Exception as e:
            print(f"[skip] {slug}: {e}")
            continue
        m = combo["metrics"]
        trades = combo["all_trades"]
        if not trades:
            continue
        years = m.get("years") or 1.0
        lot_size = meta.get("lot_size") or 65
        ratio, had_rc = recost_ratio(meta, combo)
        dd_lot, realised_lot, worst5_lot = dd_per_lot_rs(combo, a.dd_basis)

        print("=" * 82)
        print(f"{r.get('title') or slug}")
        print(f"  window {meta['window'][0]} -> {meta['window'][1]}  ({years:.2f} yr, "
              f"{len(trades)} trades, lot={lot_size})  p={r.get('p_value')}  sig={r.get('significant')}")
        print(f"  recorded @1 lot : net {m['net_pct']:.2f}%  maxdd {m['maxdd']:.2f}%  "
              f"sharpe {m['sharpe']:.2f}  'annual_return' {m.get('annual_return', 0):.2f}%")
        print(f"  recost ratio    : {ratio:.4f}" + ("" if had_rc else "  (no real_cost — bs basis)"))
        print(f"  DD of 1 lot     : realised Rs{_inr(realised_lot)}"
              + (f" | MC worst-5% Rs{_inr(worst5_lot)}" if worst5_lot else " | MC n/a")
              + f"  -> sizing on Rs{_inr(dd_lot)} ({a.dd_basis})")

        if a.check:
            eq, _, _ = simulate(trades, a.capital, 0, dd_lot, lot_size, ratio, fixed_lots=1)
            cagr, mdd, final = curve_stats(eq, a.capital, years)
            net = (final - a.capital) / a.capital * 100.0
            rc = (meta.get("real_cost") or {}).get("full") or {}
            rcn = rc.get("net_pct")
            print(f"  [check] lots=1 replay: net {net:.2f}%  (real_cost says "
                  + (f"{rcn:.2f}%" if rcn is not None else "n/a")
                  + f")  maxdd {mdd:.2f}% (recorded {m['maxdd']:.2f}%, mark-to-market)"
                  f"  CAGR {cagr:.2f}%")

        rows = []
        for b in budgets:
            eq, lots_seen, dep = simulate(trades, a.capital, b, dd_lot, lot_size, ratio,
                                          max_lots=a.max_lots)
            cagr, mdd, final = curve_stats(eq, a.capital, years)
            rows.append(dict(dd_budget=round(b * 100, 1), lots_start=lots_seen[0],
                             lots_end=lots_seen[-1], lots_max=max(lots_seen),
                             cagr=round(cagr, 2), realised_maxdd=round(mdd, 2),
                             final=round(final), peak_deploy_pct=round(dep, 1)))
        print(f"  {'DDbudget':>9} {'lots':>12} {'CAGR':>8} {'realDD':>8} "
              f"{'final':>14} {'peakDeploy':>11}")
        for x in rows:
            print(f"  {x['dd_budget']:>8.0f}% {str(x['lots_start']) + '->' + str(x['lots_end']):>12}"
                  f" {x['cagr']:>7.1f}% {x['realised_maxdd']:>7.1f}%"
                  f" {'Rs' + _inr(x['final']):>14} {x['peak_deploy_pct']:>10.1f}%")
        out.append(dict(slug=slug, title=r.get("title"), years=years,
                        recorded_net_pct=m["net_pct"], recorded_maxdd=m["maxdd"],
                        recorded_annual=m.get("annual_return"), sharpe=m["sharpe"],
                        p_value=r.get("p_value"), significant=r.get("significant"),
                        deployed=r.get("deployed"), dd_per_lot=round(dd_lot),
                        recost_ratio=round(ratio, 4), rows=rows))

    if a.json:
        json.dump(out, open(a.json, "w", encoding="utf-8"), indent=1)
        print(f"\n[json] {a.json}")


if __name__ == "__main__":
    main()
