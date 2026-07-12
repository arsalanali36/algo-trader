"""
dom_recost.py — re-cost an already-computed BS backtest with the REAL spread
measured from brother's DOM data, WITHOUT re-running any signal/BS pipeline.

The BS pass prices fills at option MID and applies zero spread slippage
(bs_option.reprice). We read each run's recorded per-trade premiums
(entry_prem/exit_prem/qty from runs/<slug>/results.js) and subtract the measured
half-spread cost:  slip_cost = h * (entry_prem + exit_prem) * qty   per trade,
where h = DOM-measured per-leg half-spread FRACTION for that premium band
(dom_cost.py). Then recompute Sharpe/net%/maxDD via the SAME engine.metrics()
the backtest used — so numbers are apples-to-apples with the recorded ones.

Scenarios per period: zero (=recorded), real (DOM median), 2x (regime stress).

EXACT for single-leg ATM-buy strategies (pivot #08, chain-zone, ORB, straddle).
Multi-leg structures (debit-vertical, backspread, iron-fly) record a NET
structure premium, so per-leg spread is understated here -> flagged, not trusted.

Usage:
  python dom_recost.py pivot_continuation
  python dom_recost.py pivot_continuation chain_zone_positional chain_zone_longatm orb_supertrend
"""
import os, sys, json
from collections import defaultdict
import numpy as np
import pandas as pd
import engine
import dom_cost

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
PERIODS = ["full", "train", "oos"]
SCEN = [("zero", 0.0), ("real", 1.0), ("2x_stress", 2.0)]

# single-leg ATM-buy runs where recorded entry_prem == the traded leg premium
# (so the re-cost is EXACT). Everything else is treated as approximate.
SINGLE_LEG_EXACT = {
    "pivot_continuation", "chain_zone_longatm", "chain_zone_positional",
    "orb_supertrend", "mid_orb_nifty", "long_straddle_orb",
}

def load_obj(slug):
    p = os.path.join(RUNS, slug, "results.js")
    raw = open(p, encoding="utf-8").read()
    obj, _ = json.JSONDecoder().raw_decode(raw[raw.index("{"):])
    return obj

def period_grid(all_days, trades, period):
    """daily-date grid for a period, matching how the engine marked equity per bar
    then grouped by day: full=all trading days; train/oos=split at the trade boundary."""
    ex = sorted(pd.to_datetime(t["exit_dt"]).date() for t in trades)
    lo, hi = ex[0], ex[-1]
    if period == "full":
        return [d for d in all_days]
    if period == "train":
        return [d for d in all_days if d <= hi]
    # oos
    return [d for d in all_days if d >= lo]

def recost_metrics(trades, grid, dc, factor):
    """rebuild the DAILY equity curve over `grid` with pnl adjusted by factor x DOM
    spread, then compute sharpe (engine._annualize_sharpe), net%, daily maxDD."""
    pnl_by_day = defaultdict(float)
    for t in trades:
        ep = abs(float(t.get("entry_prem", 0.0)))
        xp = abs(float(t.get("exit_prem", 0.0)))
        qty = float(t.get("qty", 0.0))
        slip = (dc.slip_for(ep) if ep > 0 else dc.flat_frac()) * factor
        adj = float(t["pnl"]) - slip * (ep + xp) * qty
        pnl_by_day[pd.to_datetime(t["exit_dt"]).date()] += adj
    eq = engine.START_CAP
    rows = []
    for d in grid:
        eq += pnl_by_day.get(d, 0.0)
        rows.append((pd.Timestamp(d), eq))
    eqdf = pd.DataFrame(rows, columns=["Datetime", "equity"])
    sh, _, _ = engine._annualize_sharpe(eqdf)
    e = eqdf.equity.values
    maxdd = float(((e / np.maximum.accumulate(e) - 1) * 100).min())
    net = (eq - engine.START_CAP) / engine.START_CAP * 100
    return {"sharpe": sh, "net_pct": net, "maxdd": maxdd, "final": eq}

def run(slug, dc):
    obj = load_obj(slug)
    combos, meta = obj["combos"], obj.get("meta", {})
    all_days = list(pd.to_datetime([row[0] for row in meta["candles"]]).date)
    exact = slug in SINGLE_LEG_EXACT
    tag = "" if exact else "  ⚠️ MULTI-LEG (net premium -> per-leg spread understated, approx only)"
    print(f"\n{'='*82}\n{slug}   ({meta.get('design','?')}){tag}")
    print(f"  DOM per-leg half-spread: real≈{dc.flat_frac()*100:.3f}% (by premium band) | 2x = regime stress")
    print(f"{'-'*82}")
    for period in PERIODS:
        key = f"bs|{period}"
        if key not in combos:
            continue
        trades = combos[key]["all_trades"]
        rec = combos[key]["metrics"]
        grid = period_grid(all_days, trades, period)
        m = {name: recost_metrics(trades, grid, dc, f) for name, f in SCEN}
        recorded = rec.get("sharpe")
        z = m["zero"]["sharpe"]
        flag = "OK" if recorded is None or abs(z - recorded) < 0.03 else f"⚠MISMATCH(rec {recorded:.3f})"
        head = f"  {period:<6}"
        print(head + " sharpe   " + "  ".join(f"{m[s[0]]['sharpe']:>9.3f}" for s in SCEN) + f"   [zero vs recorded: {flag}]")
        print(" "*8 + " net_%    " + "  ".join(f"{m[s[0]]['net_pct']:>9.2f}" for s in SCEN))
        print(" "*8 + " maxdd_%  " + "  ".join(f"{m[s[0]]['maxdd']:>9.2f}" for s in SCEN) + "   (daily-close DD)")
    hdr = f"           {'zero':>9}  {'real':>9}  {'2x_stress':>9}"
    print("  " + "-"*54 + "\n  columns:" + hdr[9:])
    print(f"  gate Sharpe>=1.0 |  n_trades(full)={combos.get('bs|full',{}).get('metrics',{}).get('trades','?')}")

def main():
    slugs = sys.argv[1:] or ["pivot_continuation"]
    dc = dom_cost.load()
    if dc is None:
        print("dom_spread_calib.json missing — run dom_spread.py first"); return
    for slug in slugs:
        if not os.path.isdir(os.path.join(RUNS, slug)):
            print(f"[skip] no run folder: {slug}"); continue
        run(slug, dc)

if __name__ == "__main__":
    main()
