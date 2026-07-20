"""Task 6 follow-up — can PF/Sharpe be lifted honestly? (user Q after 6b).

6b showed PF only rises with IV-rank. The honest question isn't "carve out the good
buckets" (in-sample cherry-pick) — it's: does a MILDER gate, a more-principled VRP-spread
gate, or a structural (no-IV) change SURVIVE OUT-OF-SAMPLE? Every idea reported full +
train + OOS (split at EVO_END 2025-07-01), so overfit shows up immediately.

Run: python -X utf8 vrp_ungated_ideas.py
"""
import datetime as dt
import numpy as np
import pandas as pd

import real_struct2 as r2
import bs_option as bs
import optlake_load as ol
from vrp_ungated_backtest import backtest, metrics, oos_split, significance, FLAG, TF, IV_LOOKBACK


def line(tag, tr):
    m = metrics(tr); trm, oom = oos_split(tr); s = significance(tr)
    print(f"  {tag:34s} n={m['n']:3d}  PF={m['pf']:5.2f}  Sh={m['sharpe']:6.2f}  "
          f"net={m['net_pct']:6.1f}%  win={m['win_rate']:4.0f}%  p={s['p_value']}  "
          f"| train PF={trm['pf']:.2f}/n{trm['n']}  OOS PF={oom['pf']:.2f}/n{oom['n']} net={oom['net_pct']:.0f}%")
    return m, trm, oom


def main():
    lot = bs.get_nifty_lot() or 65
    bs.SLIP_ENABLED = True; bs.SLIP_MULT = 1.0
    g = r2.grid(FLAG, TF)
    ivr = ol.iv_rank_daily(FLAG, TF, IV_LOOKBACK)

    # leak-free VRP-spread eligible days: day-open ATM IV > trailing 20-day median (shifted)
    af = ol.atm_frame(FLAG, TF); af["d"] = pd.to_datetime(af.Datetime).dt.date
    iv_open = af.groupby("d").atm_iv.first()
    iv_med = iv_open.rolling(20, min_periods=10).median().shift(1)
    vrp_days = {d for d in iv_open.index if pd.notna(iv_med.get(d)) and iv_open[d] > iv_med[d]}
    print(f"lot={lot}  IV-rank days={len(ivr)}  VRP-spread(open>trailmed) days={len(vrp_days)}\n")

    # baseline (ungated, from 6a) for reference
    print("── baseline ungated (cycle_start | iron_condor, wing5) ──")
    line("ungated", backtest(g, ivr, lot, "cycle_start", "iron_condor", 5, 3, iv_min=0.0))

    # IDEA 1 — milder IV-rank gate sweep, does it survive OOS?
    print("\n── IDEA 1: IV-rank gate sweep (does a milder-than-0.80 gate survive OOS?) ──")
    for thr in (0.3, 0.4, 0.5, 0.6, 0.7):
        line(f"iv_rank >= {thr:.1f}",
             backtest(g, ivr, lot, "cycle_start", "iron_condor", 5, 3, iv_min=thr))

    # IDEA 2 — VRP-spread gate (principled: sell only when premium is rich vs its own recent level)
    print("\n── IDEA 2: VRP-spread gate (IV_open > trailing-20d-median, leak-free) ──")
    line("VRP-spread, cycle_start", backtest(g, ivr, lot, "cycle_start", "iron_condor", 5, 3,
                                             iv_min=0.0, allow_days=vrp_days))
    line("VRP-spread, dte4",        backtest(g, ivr, lot, "dte4", "iron_condor", 5, 3,
                                             iv_min=0.0, allow_days=vrp_days))

    # IDEA 3 — structural, NO IV pick: avoid expiry-day 0DTE gamma (exit day-before-expiry)
    print("\n── IDEA 3: structural (no IV filter) — exit 1 day before expiry (avoid 0DTE gamma) ──")
    line("ungated + pre-expiry exit",
         backtest(g, ivr, lot, "cycle_start", "iron_condor", 5, 3, iv_min=0.0, exit_before_dte=1))
    line("dte4 + pre-expiry exit",
         backtest(g, ivr, lot, "dte4", "iron_condor", 5, 3, iv_min=0.0, exit_before_dte=1))

    # IDEA 4 — best honest combo: milder gate + later entry + pre-expiry exit (all OOS-checked)
    print("\n── IDEA 4: combos (milder gate + dte4 + pre-expiry exit) ──")
    for thr in (0.4, 0.5, 0.6):
        line(f"iv>={thr} + dte4 + pre-exp exit",
             backtest(g, ivr, lot, "dte4", "iron_condor", 5, 3, iv_min=thr, exit_before_dte=1))
    line("VRP-spread + dte4 + pre-exp exit",
         backtest(g, ivr, lot, "dte4", "iron_condor", 5, 3, iv_min=0.0,
                  allow_days=vrp_days, exit_before_dte=1))


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    main()
