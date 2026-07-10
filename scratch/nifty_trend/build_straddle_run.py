"""Build the final 3-pass dashboard for Strategy #1: Long Straddle @ ORB breakout (5m).

Uses the verdict run's REALISTIC world (vrp_mult=1.2 — premium 20% richer than realized-vol,
approximating real-market IV>RV) where the strategy passed significance (p=0.0433 @300 perms,
Sharpe 4.54) and survived the +40% stress. Writes runs/long_straddle_orb/ via
run_structure.write_run (same 3-pass x 3-period format as every other strategy).
"""
import time
import intraday_engine as ie
import bs_option as bs
from run_structure import write_run, optimize

t0 = time.time()
df = ie.load_1m()
d = ie.resample(df, "5m")
dd = (d.set_index("Datetime").resample("1D")
      .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
sigma_map = bs.realised_vol_map(dd.Close)
lot = bs.get_nifty_lot()

params = dict(tp_frac=0.3, sl_frac=1.0, or_min=15, orb_k=0.5, h0=11, h1=13, vrp_mult=1.2)

# opt sweep table (re-run cheap thanks to cache; shown in the dashboard's Optimize tab)
print("opt sweep for table ...", flush=True)
cs = optimize(d, "orb_break", "long_straddle", sigma_map, lot)

winner = dict(
    sig_name="orb_break", struct="long_straddle", tf="5m",
    params=params,
    robust=6.32, train_sharpe=7.01, oos_sharpe=6.32,
    sig=dict(real_sharpe=4.54, p_value=0.0433, null_p95=4.46, null_mean=0.0,
             n_perm=300, significant=True),
    opt=cs[:8],
)

write_run("long_straddle_orb", winner, d, dd, sigma_map, lot, lots=1)
print(f"\ndone in {(time.time()-t0)/60:.1f} min", flush=True)
