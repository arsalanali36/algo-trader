"""
02.10.01 BNF hedged strangle — RIGOR checks (reproducible).
Runs on the tuned live config (SELL ATM±6, BUY ±11 wings, SL Rs4k / Target Rs8k, 5 lots).

1) WING-PRICING sensitivity: is the BS-wing assumption fragile? (wings ±11 are 1 strike
   beyond the OptChainLake ±10 window, so priced by Black-Scholes.) Stress the wing
   premium 0.5x..2.0x — if Sharpe stays strong, the BS estimate doesn't drive the edge.
2) SIGNIFICANCE: bootstrap P(mean net <= 0) + Sharpe 5-95 pct CI + per-year green count.

Result (2026-08-26): wing-pricing INSENSITIVE (Sharpe 2.41-4.21 across 0.5-2.0x);
bootstrap P(mean<=0)=0.0000, Sharpe CI [2.71, 3.80], 6/6 years green.
"""
import warnings; warnings.filterwarnings("ignore")
import math, numpy as np, pandas as pd
import bnf_hedged_backtest as H

np.random.seed(11)
g = H.base.load_grid()


def _oos(d):
    dd = d.sort_values("day").reset_index(drop=True)
    cut = dd.day.iloc[int(len(dd) * 0.65)]
    o = dd[dd.day >= cut].net.values
    return o.mean() / o.std() * math.sqrt(252) if len(o) > 1 and o.std() > 0 else 0.0


def main():
    print("== 1) WING-PRICING sensitivity (SL Rs4k / Target Rs8k) ==")
    for wm in (0.5, 1.0, 1.5, 2.0):
        d = H.run_hedged(g, basket_sl=4000, basket_tgt=8000, wing_mult=wm)
        net = d.net.values
        sh = net.mean() / net.std() * math.sqrt(252)
        print(f"   wing premium x{wm:<4} Sharpe={sh:5.2f}  OOS={_oos(d):5.2f}  net=Rs{net.sum():>9,.0f}")

    print("\n== 2) SIGNIFICANCE (bootstrap, 10k) ==")
    d = H.run_hedged(g, basket_sl=4000, basket_tgt=8000)
    net = d.net.values; N = len(net)
    means = np.array([net[np.random.randint(0, N, N)].mean() for _ in range(10000)])
    p_le0 = float((means <= 0).mean())
    shs = sorted(net[np.random.randint(0, N, N)].mean() / net[np.random.randint(0, N, N)].std() * math.sqrt(252)
                 for _ in range(3000))
    print(f"   n={N}  mean=Rs{net.mean():.0f}/trade  Sharpe={net.mean()/net.std()*math.sqrt(252):.2f}")
    print(f"   bootstrap P(mean net <= 0) = {p_le0:.4f}   ({'REAL edge' if p_le0 < 0.05 else 'NOT significant'})")
    print(f"   bootstrap Sharpe 5-95 pct = {shs[int(.05*len(shs))]:.2f} - {shs[int(.95*len(shs))]:.2f}")
    yr = d.copy(); yr['y'] = pd.to_datetime(yr.day).dt.year
    yg = yr.groupby('y').net.sum()
    print(f"   green years: {(yg > 0).sum()}/{len(yg)}  {dict(yg.round(0).astype(int))}")


if __name__ == "__main__":
    main()
