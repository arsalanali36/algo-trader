"""Honest-costing pass for the real-premium short-vol edge:
slippage sweep + train/OOS + recent-2023 + tail (worst trades, loss percentiles).
The Sharpe-11 headline is a fill-assumption; this shows what survives reality."""
import numpy as np
import pandas as pd
import engine, bs_option as bs, optlake_load as lake, real_struct as rs

m = lake.atm_frame("WEEK", "5m")
lot = bs.get_nifty_lot()
k = int(len(m) * 0.68)
mtr, moos = m.iloc[:k].reset_index(drop=True), m.iloc[k:].reset_index(drop=True)
mrec = m[m.Datetime >= pd.Timestamp("2023-01-01")].reset_index(drop=True)

BASE = dict(h0=10, tp_frac=0.5, sl_frac=1.0)   # the headline config

def run(frame, slip, **over):
    p = dict(BASE, slip_frac=slip, **over)
    res = rs.backtest(frame, "short_straddle", "time", p, lot=lot, charges=True)
    mt, _ = engine.metrics(res)
    return mt, res

print(f"real ATM: {len(m)} bars, {m.day.nunique()} days, {m.Datetime.min().date()}→{m.Datetime.max().date()}")
print("\n=== SLIPPAGE SWEEP (full 5yr, per-leg premium haircut each side) ===")
print(f"{'slip':>6s}{'trades':>8s}{'net%':>9s}{'sharpe':>8s}{'maxDD':>8s}{'win%':>7s}{'avg_win':>9s}{'avg_loss':>9s}")
for slip in (0.0, 0.0025, 0.005, 0.01, 0.02, 0.03):
    mt, _ = run(m, slip)
    print(f"{slip*100:>5.2f}%{mt['trades']:>8d}{mt['net_pct']:>9.1f}{mt['sharpe']:>8.2f}"
          f"{mt['maxdd']:>8.1f}{mt['win_rate']:>7.1f}{mt['avg_win']:>9.0f}{mt['avg_loss']:>9.0f}")

print("\n=== at a REALISTIC slip=0.5% : train / OOS / recent-2023 ===")
for name, fr in (("full", m), ("train", mtr), ("OOS", moos), ("recent23", mrec)):
    mt, _ = run(fr, 0.005)
    print(f"  {name:9s} trades={mt['trades']:4d} net={mt['net_pct']:7.1f}% sharpe={mt['sharpe']:5.2f} "
          f"maxDD={mt['maxdd']:5.1f}% win={mt['win_rate']:.0f}% PF={mt.get('profit_factor',0):.2f}")

print("\n=== TAIL (slip=0.5%, full): worst single-day trades + loss distribution ===")
mt, res = run(m, 0.005)
tr = sorted(res["trades"], key=lambda t: t["pnl"])
print("  worst 8 trades (₹):")
for t in tr[:8]:
    print(f"    {str(pd.to_datetime(t['entry_dt']))[:10]}  pnl={t['pnl']:>9.0f}  spot {t['spot_in']}→{t['spot_out']}  "
          f"straddle {t['entry_prem']}→{t['exit_prem']}  {t['reason']}")
pnls = np.array([t["pnl"] for t in res["trades"]])
print(f"  loss pctiles: p1={np.percentile(pnls,1):.0f}  p5={np.percentile(pnls,5):.0f}  "
      f"median={np.percentile(pnls,50):.0f}  worst={pnls.min():.0f}")
print(f"  worst DAY loss = {pnls.min():.0f} ({pnls.min()/engine.START_CAP*100:.2f}% of capital) — this is the steamroller day")
# how many days lost > 1% of capital?
big = (pnls < -0.01*engine.START_CAP).sum()
print(f"  days losing >1% capital: {big} / {len(pnls)}")
