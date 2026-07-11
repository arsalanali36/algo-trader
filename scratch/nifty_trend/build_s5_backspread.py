"""Strategy #05 — Ratio Backspread (sell 1 ATM + buy 2 OTM), directional on a spot signal.

Master-prompt spec: NIFTY options, 5m cadence, intraday (15:15 exit, max 2/day), 1x,
real per-leg Zerodha charges (2-lot leg = 2x turnover charges), skip-expiry policy A,
gates: Sharpe>=1, DD<=20%, trades>=100, p<0.05, min(train,OOS) ranking, MC median.

Signals screened: chain_zone (our validated #04 entry), orb_break, tod_orb_break.
Optimize sweeps structure knobs (tp_frac / sl_frac / bs_off) + signal knobs.
Reuses run_structure.write_run for the 3-pass dashboard (runs/ratio_backspread/).
"""
import time
import random
import numpy as np

import engine
import intraday_engine as ie
import bs_option as bs
import option_structures as osx
import run_structure as rs
from intraday_optimize import split

STRUCT = "ratio_backspread"
TF = "5m"
SLUG = "ratio_backspread"

GRID_COMMON = dict(tp_frac=[0.3, 0.5, 0.75, 1.0, 1.5], sl_frac=[0.5, 0.75, 1.0, 1.5],
                   bs_off=[2, 3, 4])
GRID_SIG = {
    "chain_zone": dict(touch_tol=[0.0, 5.0, 10.0], zone_age=[1, 2, 3], max_cs=[40.0, 60.0],
                       hawa=[False], chain_lookback=[10, 20]),
    "orb_break": dict(or_min=[15, 30, 45], orb_k=[0.5, 1.0, 1.5]),
    "tod_orb_break": dict(or_min=[15, 30], orb_k=[0.5, 1.0], h0=[10, 11], h1=[13, 14]),
}


def optimize(d, sig_name, sigma_map, lot, trials=300, seed=5):
    random.seed(seed + hash(sig_name) % 100)
    dtr, doos = split(d)
    grid = {**GRID_COMMON, **GRID_SIG[sig_name]}
    seen, cands = set(), []
    for _ in range(trials):
        p = {k: random.choice(v) for k, v in grid.items()}
        key = tuple(sorted((k, str(v)) for k, v in p.items()))
        if key in seen:
            continue
        seen.add(key)
        m1, _ = engine.metrics(osx.backtest_structure(dtr, sig_name, STRUCT, p, sigma_map, lot, 1, charges=True))
        if m1["trades"] < 40 or m1.get("sharpe", -9) < 0.4:
            continue
        m2, _ = engine.metrics(osx.backtest_structure(doos, sig_name, STRUCT, p, sigma_map, lot, 1, charges=True))
        if m2["trades"] < 20:
            continue
        cands.append(dict(params=p, robust=min(m1["sharpe"], m2["sharpe"]),
                          train_sharpe=m1["sharpe"], oos_sharpe=m2["sharpe"],
                          oos_net=m2.get("net_pct", 0), oos_dd=m2.get("maxdd", 0),
                          oos_trades=m2["trades"]))
    cands.sort(key=lambda c: c["robust"], reverse=True)
    return cands


def main():
    t0 = time.time()
    df1m = ie.load_1m()
    d = ie.resample(df1m, TF)
    dd = (d.set_index("Datetime").resample("1D")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    sigma_map = bs.realised_vol_map(dd.Close)
    lot = bs.get_nifty_lot()
    dtr, doos = split(d)

    # ---- screen: 3 signals, defaultish params ----
    print("SCREEN — ratio_backspread x signals @5m (charges ON)\n", flush=True)
    defs = {"chain_zone": dict(touch_tol=5.0, zone_age=2, max_cs=40.0, chain_lookback=20),
            "orb_break": dict(or_min=15, orb_k=1.0),
            "tod_orb_break": dict(or_min=30, orb_k=1.0, h0=10, h1=13)}
    ranked = []
    for sig_name, sp in defs.items():
        p = dict(tp_frac=0.75, sl_frac=0.75, bs_off=2, **sp)
        m1, _ = engine.metrics(osx.backtest_structure(dtr, sig_name, STRUCT, p, sigma_map, lot, 1, charges=True))
        m2, _ = engine.metrics(osx.backtest_structure(doos, sig_name, STRUCT, p, sigma_map, lot, 1, charges=True))
        rob = min(m1.get("sharpe", -9), m2.get("sharpe", -9))
        ranked.append((rob, sig_name))
        print(f"  {sig_name:14s} robust={rob:5.2f} (tr {m1['sharpe']:.2f}/oos {m2['sharpe']:.2f}) "
              f"tr={m2['trades']}", flush=True)
    ranked.sort(reverse=True)

    # ---- optimize top-2 signals, significance-gate the best ----
    print("\nOPTIMIZE + significance:", flush=True)
    winner = None
    for _, sig_name in ranked[:2]:
        cs = optimize(d, sig_name, sigma_map, lot, trials=300)
        if not cs:
            print(f"  {sig_name}: no candidate"); continue
        c = cs[0]
        real, p, n95 = osx.sig_test(d, sig_name, STRUCT, c["params"], sigma_map, lot, 1, n_perm=500)
        ok = p < 0.05
        print(f"  {sig_name:14s} robust={c['robust']:.2f} (tr {c['train_sharpe']:.2f}"
              f"/oos {c['oos_sharpe']:.2f}) sharpe={real:.2f} p={p:.3f} "
              f"{'*** SIGNIFICANT ***' if ok else 'not sig'}", flush=True)
        if ok and winner is None:
            winner = dict(sig_name=sig_name, struct=STRUCT, tf=TF, params=c["params"],
                          robust=c["robust"], train_sharpe=c["train_sharpe"],
                          oos_sharpe=c["oos_sharpe"],
                          sig=dict(real_sharpe=round(real, 3), p_value=round(p, 4),
                                   null_p95=round(n95, 3), null_mean=0.0, n_perm=500,
                                   significant=True),
                          opt=cs[:8])
    if winner is None:
        print("\nNo signal passed significance for the backspread. Honest result: iterate design.")
        return

    rs.write_run(SLUG, winner, d, dd, sigma_map, lot, 1)
    try:
        import build_compare
        build_compare.main()
    except Exception as e:
        print(f"  [compare] skipped ({e})", flush=True)
    print(f"\ndone in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
