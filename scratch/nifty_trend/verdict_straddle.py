"""One-off verdict run for Strategy #1 candidate: Long Straddle @ ORB breakout (5m).

Resolves the p=0.050 boundary honestly:
  1. optimize (cached, fast) -> best params by min(train,oos)
  2. 1000-perm rotation significance at vrp_mult=1.0 (realized-vol premium)
  3. VRP stress: same strategy with premium 20% richer (vrp_mult=1.2 ~ real-market IV>RV)
     -> does the edge survive paying realistic premium? + 300-perm significance there too.
Ship only if: p<0.05 at 1000 perms AND still profitable under VRP stress.
"""
import time
import intraday_engine as ie
import bs_option as bs
import engine
import option_structures as osx
from run_structure import optimize

t0 = time.time()
df = ie.load_1m()
d = ie.resample(df, "5m")
dd = (d.set_index("Datetime").resample("1D")
      .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
sm = bs.realised_vol_map(dd.Close)
lot = bs.get_nifty_lot()

print("=== optimize (ORB breakout x long_straddle, 5m) ===", flush=True)
cs = optimize(d, "orb_break", "long_straddle", sm, lot)
for c in cs[:8]:
    print(f"  {c['params']}  robust={c['robust']:.2f} (tr {c['train_sharpe']:.2f}/oos {c['oos_sharpe']:.2f})", flush=True)
best = cs[0]["params"]
print(f"\nBEST: {best}", flush=True)

print("\n=== significance @ vrp 1.0 (1000 perms) ===", flush=True)
real, p, n95 = osx.sig_test(d, "orb_break", "long_straddle", best, sm, lot, 1, n_perm=1000)
print(f"real_sharpe={real:.2f}  p={p:.4f}  null_p95={n95:.2f}  -> {'SIGNIFICANT' if p < 0.05 else 'NOT significant'}", flush=True)

print("\n=== VRP stress: premium 20% richer (vrp_mult=1.2) ===", flush=True)
bp = dict(best); bp["vrp_mult"] = 1.2
res = osx.backtest_structure(d, "orb_break", "long_straddle", bp, sm, lot, 1, charges=True)
m, _ = engine.metrics(res)
print(f"stressed: trades={m['trades']} net={m.get('net_pct',0):.1f}% sharpe={m.get('sharpe',0):.2f} "
      f"maxDD={m.get('maxdd',0):.1f}% win={m.get('win_rate',0):.1f}%", flush=True)
real2, p2, n952 = osx.sig_test(d, "orb_break", "long_straddle", bp, sm, lot, 1, n_perm=300)
print(f"stressed sig: real={real2:.2f} p={p2:.4f} null_p95={n952:.2f} -> {'SIGNIFICANT' if p2 < 0.05 else 'NOT significant'}", flush=True)

print(f"\n=== VRP stress harder: 40% richer (vrp_mult=1.4) ===", flush=True)
bp2 = dict(best); bp2["vrp_mult"] = 1.4
res2 = osx.backtest_structure(d, "orb_break", "long_straddle", bp2, sm, lot, 1, charges=True)
m2, _ = engine.metrics(res2)
print(f"stressed40: trades={m2['trades']} net={m2.get('net_pct',0):.1f}% sharpe={m2.get('sharpe',0):.2f} "
      f"maxDD={m2.get('maxdd',0):.1f}%", flush=True)

print(f"\ndone in {(time.time()-t0)/60:.1f} min", flush=True)
