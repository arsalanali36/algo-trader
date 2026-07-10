"""Rebuild Strategy #1 (Long Straddle @ ORB) + #2 (Debit Vertical @ ORB) on the FULL
2018-2026 (8.5yr) data, with fresh optimize + significance, at vrp=1.2 (realistic premium).
Writes both runs/<slug>/ dashboards (philosophy + glossary embedded via run_structure.write_run).
"""
import time
from itertools import product
import intraday_engine as ie
import bs_option as bs
import engine
import option_structures as osx
from intraday_optimize import split
from run_structure import write_run

NPERM = 500
t0 = time.time()
df = ie.load_1m()
lot = bs.get_nifty_lot()


def prep(tf):
    d = ie.resample(df, tf)
    dd = (d.set_index("Datetime").resample("1D")
          .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    return d, bs.realised_vol_map(dd.Close), dd


def opt(d, sig, struct, sm, grids):
    """grids: dict param->list. Evaluate on train/oos, rank by robust min(train,oos). vrp fixed 1.2."""
    dtr, doos = split(d)
    keys = list(grids)
    cands = []
    for vals in product(*[grids[k] for k in keys]):
        p = dict(zip(keys, vals)); p["vrp_mult"] = 1.2
        m1, _ = engine.metrics(osx.backtest_structure(dtr, sig, struct, p, sm, lot, 1, charges=True))
        m2, _ = engine.metrics(osx.backtest_structure(doos, sig, struct, p, sm, lot, 1, charges=True))
        if m1["trades"] < 40 or m2["trades"] < 20:
            continue
        robust = min(m1.get("sharpe", -9), m2.get("sharpe", -9))
        cands.append(dict(params=p, robust=robust, train_sharpe=m1["sharpe"], oos_sharpe=m2["sharpe"],
                          oos_net=m2.get("net_pct", 0), oos_dd=m2.get("maxdd", 0), oos_trades=m2["trades"]))
    cands.sort(key=lambda c: c["robust"], reverse=True)
    return cands


def build(slug, sig, struct, tf, grids):
    print(f"\n===== {slug} ({struct} @ {sig}, {tf}) =====", flush=True)
    d, sm, dd = prep(tf)
    cs = opt(d, sig, struct, sm, grids)
    if not cs:
        print("  no candidate passed screen"); return
    best = cs[0]
    print(f"  best {best['params']} robust={best['robust']:.2f} (tr {best['train_sharpe']:.2f}/oos {best['oos_sharpe']:.2f})", flush=True)
    real, p, n95 = osx.sig_test(d, sig, struct, best["params"], sm, lot, 1, n_perm=NPERM)
    print(f"  significance: sharpe={real:.2f} p={p:.4f} -> {'SIGNIFICANT' if p < 0.05 else 'NOT sig'}", flush=True)
    winner = dict(sig_name=sig, struct=struct, tf=tf, params=best["params"],
                  robust=best["robust"], train_sharpe=best["train_sharpe"], oos_sharpe=best["oos_sharpe"],
                  sig=dict(real_sharpe=round(real, 3), p_value=round(p, 4), null_p95=round(n95, 3),
                           null_mean=0.0, n_perm=NPERM, significant=bool(p < 0.05)),
                  opt=cs[:8])
    write_run(slug, winner, d, dd, sm, lot, lots=1)


# #1 — Long Straddle @ ORB breakout (5m)
build("long_straddle_orb", "orb_break", "long_straddle", "5m",
      dict(tp_frac=[0.3, 0.5], sl_frac=[1.0], or_min=[15, 30], orb_k=[0.5, 1.0]))

# #2 — Debit Vertical (Bull-Call / Bear-Put) @ ORB breakout (15m)
build("debit_vertical_orb", "orb_break", "vertical_debit", "15m",
      dict(tp_frac=[0.75, 1.0], sl_frac=[1.0], or_min=[15], orb_k=[1.0], wing_off=[6, 8, 10]))

print(f"\nALL DONE in {(time.time()-t0)/60:.1f} min", flush=True)
