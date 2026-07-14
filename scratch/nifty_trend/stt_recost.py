"""
stt_recost.py — re-cost recorded BS runs with the DATE-AWARE charges model
(charges.py, 2026-07-14) layered ON TOP of the DOM-spread re-cost (dom_recost.py),
WITHOUT re-running any pipeline. Read-only, same discipline as dom_recost.

Every recorded run priced ALL its trades at the PRE-Oct-2024 charge rates
(0.0625% STT sell / 0.053% txn). The correct fee is regime-dependent on the
trade's ENTRY date (charges.OPT_REGIMES). Per trade the fee DELTA is:

    d_stt = (stt_sell(when) - 0.000625) * sell_turn
    d_txn = (txn(when)      - 0.00053 ) * total_turn
    d_gst = 0.18 * d_txn                      (GST rides on txn; brokerage/SEBI unchanged)
    delta = d_stt + d_txn + d_gst             (0 for pre-Oct-2024 entries by construction)

sell_turn: BUY-entry trades sell at EXIT (sell_turn = exit_prem*qty); SELL-entry
runs (naked / credit / iron-fly) sell at ENTRY. Multi-leg runs record NET premium
-> per-leg turnover understated -> both DOM and STT deltas understated (flagged,
same caveat as dom_recost).

Scenarios per period (bs|full/train/oos):
    rec       recorded  (old charges, zero slip)
    +dom      + DOM real half-spread              (SLIP_RECOST_2026-07-13 'real')
    +stt      + date-aware charges delta only     (isolates the STT-refresh effect)
    +both     + DOM + charges delta               (the new honest number)

Usage:  python -X utf8 stt_recost.py            # all runs in runs/index.json
        python -X utf8 stt_recost.py slug1 ...
"""
import os
import sys
import json
from collections import defaultdict

import numpy as np
import pandas as pd

import engine
import dom_cost
import charges as chg
from dom_recost import load_obj, period_grid, SINGLE_LEG_EXACT

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
PERIODS = ["full", "train", "oos"]

# runs whose ENTRY is the SELL side (credit received at entry). Everything else = BUY entry.
SELL_ENTRY = {"chain_zone_naked", "chain_zone_credit", "shortvol_ironfly"}

OLD_STT, OLD_TXN = 0.000625, 0.00053   # the rates baked into every recorded fee


def fee_delta(t, sell_entry):
    """date-aware charge delta vs the recorded (pre-Oct-2024-rate) fee, per trade."""
    ep = abs(float(t.get("entry_prem", 0.0)))
    xp = abs(float(t.get("exit_prem", 0.0)))
    qty = float(t.get("qty", 0.0))
    r = chg.opt_rates(t["entry_dt"])
    sell_turn = (ep if sell_entry else xp) * qty
    total = (ep + xp) * qty
    d_stt = (r["stt_sell"] - OLD_STT) * sell_turn
    d_txn = (r["txn"] - OLD_TXN) * total
    return d_stt + 1.18 * d_txn


def recost(trades, grid, dc, dom_factor, stt_on, sell_entry):
    pnl_by_day = defaultdict(float)
    tot_slip = tot_stt = 0.0
    for t in trades:
        ep = abs(float(t.get("entry_prem", 0.0)))
        xp = abs(float(t.get("exit_prem", 0.0)))
        qty = float(t.get("qty", 0.0))
        slip = (dc.slip_for(ep) if ep > 0 else dc.flat_frac()) * dom_factor * (ep + xp) * qty
        dfee = fee_delta(t, sell_entry) if stt_on else 0.0
        tot_slip += slip
        tot_stt += dfee
        pnl_by_day[pd.to_datetime(t["exit_dt"]).date()] += float(t["pnl"]) - slip - dfee
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
    return {"sharpe": sh, "net_pct": net, "maxdd": maxdd,
            "slip_rs": tot_slip, "stt_rs": tot_stt}


SCEN = [("rec", 0.0, False), ("+dom", 1.0, False), ("+stt", 0.0, True), ("+both", 1.0, True)]


def run(slug, dc):
    obj = load_obj(slug)
    combos, meta = obj["combos"], obj.get("meta", {})
    all_days = list(pd.to_datetime([row[0] for row in meta["candles"]]).date)
    sell_entry = slug in SELL_ENTRY
    exact = slug in SINGLE_LEG_EXACT
    tag = "" if exact else "  [MULTI-LEG: net-premium -> both deltas understated, approx]"
    print(f"\n{'='*96}\n{slug}   ({meta.get('design','?')}) entry={'SELL' if sell_entry else 'BUY'}{tag}")
    print(f"{'-'*96}")
    out = {}
    for period in PERIODS:
        key = f"bs|{period}"
        if key not in combos:
            continue
        trades = combos[key]["all_trades"]
        grid = period_grid(all_days, trades, period)
        m = {name: recost(trades, grid, dc, f, s, sell_entry) for name, f, s in SCEN}
        out[period] = m
        n = len(trades)
        # regime mix of this period's trades
        post24 = sum(1 for t in trades if str(t["entry_dt"])[:10] >= "2024-10-01")
        post26 = sum(1 for t in trades if str(t["entry_dt"])[:10] >= "2026-04-01")
        print(f"  {period:<6} sharpe  " + "  ".join(f"{m[s[0]]['sharpe']:>9.3f}" for s in SCEN)
              + f"   n={n} (post-Oct24 {post24}, post-Apr26 {post26})")
        print(f"  {'':<6} net_%   " + "  ".join(f"{m[s[0]]['net_pct']:>9.2f}" for s in SCEN)
              + f"   slip Rs{m['+both']['slip_rs']:,.0f} | stt-delta Rs{m['+both']['stt_rs']:,.0f}")
    print("  columns:  " + "  ".join(f"{s[0]:>9}" for s in SCEN))
    return out


def main():
    if len(sys.argv) > 1:
        slugs = sys.argv[1:]
    else:
        slugs = [r["slug"] for r in json.load(open(os.path.join(RUNS, "index.json")))]
    dc = dom_cost.load()
    if dc is None:
        print("dom_spread_calib.json missing — run dom_spread.py first")
        return
    res = {}
    for slug in slugs:
        if not os.path.isdir(os.path.join(RUNS, slug)):
            print(f"[skip] no run folder: {slug}")
            continue
        res[slug] = run(slug, dc)
    # gate-cross summary: crossed 1.0 under +both but NOT under +dom alone
    print(f"\n{'='*96}\nGATE-CROSS (Sharpe >=1.0) — newly below the gate because of the STT refresh:")
    any_flag = False
    for slug, periods in res.items():
        for period, m in periods.items():
            dom_ok = m["+dom"]["sharpe"] >= 1.0
            both_ok = m["+both"]["sharpe"] >= 1.0
            if dom_ok and not both_ok:
                any_flag = True
                print(f"  !! {slug} {period}: +dom {m['+dom']['sharpe']:.3f} -> +both {m['+both']['sharpe']:.3f}")
    if not any_flag:
        print("  none — no run that survived DOM-spread drops below 1.0 from the STT refresh alone")


if __name__ == "__main__":
    main()
