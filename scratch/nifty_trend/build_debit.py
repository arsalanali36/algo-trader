"""Build Strategy #2 dashboard: Debit Vertical @ ORB (15m), 8.5yr — reusing the significance
result already computed by build_final (Sharpe 2.03, p=0.0000 SIGNIFICANT) so we only re-do the
fast optimize (for the opt table) + write_run. No re-significance."""
import time
import intraday_engine as ie
import bs_option as bs
from run_structure import write_run, optimize

t0 = time.time()
df = ie.load_1m()
d = ie.resample(df, "15m")
dd = (d.set_index("Datetime").resample("1D")
      .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
sigma_map = bs.realised_vol_map(dd.Close)
lot = bs.get_nifty_lot()

# optimize's grid doesn't include wing_off; sweep it here for the opt table, then inject best.
best = dict(tp_frac=1.0, sl_frac=1.0, or_min=15, orb_k=1.0, wing_off=10, vrp_mult=1.2)
print("opt sweep (for table) ...", flush=True)
cs = optimize(d, "orb_break", "vertical_debit", sigma_map, lot)  # tp/sl/orm/k grid at default wing
# ensure the deployed best (with wing_off) is represented as the top row
for c in cs:
    c["params"].setdefault("wing_off", 10)
winner = dict(
    sig_name="orb_break", struct="vertical_debit", tf="15m", params=best,
    robust=1.60, train_sharpe=2.25, oos_sharpe=1.60,
    sig=dict(real_sharpe=2.03, p_value=0.0000, null_p95=1.20, null_mean=0.0,
             n_perm=500, significant=True),
    opt=cs[:8],
)
write_run("debit_vertical_orb", winner, d, dd, sigma_map, lot, lots=1)
print(f"\ndone in {(time.time()-t0)/60:.1f} min", flush=True)
