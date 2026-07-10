"""Strategy #3 — Long Strangle @ ORB breakout (NIFTY, option structure).

Sibling of #1 Long Straddle but the CE/PE are bought OTM (+2/-2 strikes per STRUCTS) —
cheaper premium, needs a bigger move. Reuses the run_structure.py pipeline end-to-end so it
inherits policy-A (skip-expiry default ON), the 3-pass/3-period dashboard, and significance.

Screens 5m + 15m, picks the better robust=min(train,oos) TF, 500-perm significance, writes
runs/long_strangle_orb/. Single clean template run (no new engine/HTML).
"""
import time
import intraday_engine as ie
import bs_option as bs
import run_structure as rs

STRUCT, SIG, SLUG = "long_strangle", "orb_break", "long_strangle_orb"
NPERM = 500

t0 = time.time()
df1m = ie.load_1m()
lot = bs.get_nifty_lot()
print(f"lot={lot}", flush=True)

best = None
for tf in ("5m", "15m"):
    d = ie.resample(df1m, tf)
    dd = (d.set_index("Datetime").resample("1D")
          .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    sm = bs.realised_vol_map(dd.Close)
    cs = rs.optimize(d, SIG, STRUCT, sm, lot, trials=200)
    if not cs:
        print(f"  {tf}: no optimize candidate passed screen", flush=True)
        continue
    c = cs[0]
    print(f"  {tf}: best robust={c['robust']:.2f} (tr {c['train_sharpe']:.2f}/oos {c['oos_sharpe']:.2f}) {c['params']}", flush=True)
    if best is None or c["robust"] > best["cand"]["robust"]:
        best = dict(cand=c, cs=cs, tf=tf, d=d, dd=dd, sm=sm)

if best is None:
    print("no candidate — strangle failed the screen on both TFs"); raise SystemExit

c, d = best["cand"], best["d"]
real, p, n95 = rs.osx.sig_test(d, SIG, STRUCT, c["params"], best["sm"], lot, 1, n_perm=NPERM)
print(f"\nWINNER tf={best['tf']}: sharpe={real:.2f} p={p:.4f} -> "
      f"{'SIGNIFICANT' if p < 0.05 else 'NOT sig'}", flush=True)

winner = dict(sig_name=SIG, struct=STRUCT, tf=best["tf"], params=c["params"], robust=c["robust"],
              train_sharpe=c["train_sharpe"], oos_sharpe=c["oos_sharpe"],
              sig=dict(real_sharpe=round(real, 3), p_value=round(p, 4), null_p95=round(n95, 3),
                       null_mean=0.0, n_perm=NPERM, significant=bool(p < 0.05)),
              opt=best["cs"][:8])
rs.write_run(SLUG, winner, d, best["dd"], best["sm"], lot, 1)
print(f"\nDONE in {(time.time()-t0)/60:.1f} min", flush=True)
