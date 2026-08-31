import numpy as np, pandas as pd
import bnf_920_strangle_intraday as base
import bnf_hedged_backtest as bt
g = base.load_grid(); STEP = base.STEP
DAY, TT, DT, SPOT, ATMK = g["DAY"], g["TT"], g["DT"], g["SPOT"], g["ATMK"]
days = np.array([str(d) for d in DAY])
uniq, first = np.unique(days, return_index=True)
last = {d: (first[k+1]-1 if k+1 < len(first) else len(days)-1) for k, d in enumerate(uniq)}
firstd = dict(zip(uniq, first))

print(f"{'structure':<26}{'trades':>8}{'CONTAMINATED':>14}{'%':>8}   <- koi leg lake (+-10) se bahar")
for off, wing in ((6,5),(6,4),(6,3),(4,4),(3,4),(2,3)):
    df = bt.run_positional(g, off, wing, 4000.0, 8000.0, 5, max_hold_days=1,
                           exp_squareoff_days=2, real_wings=(off+wing <= 10))
    bad = 0
    for _, r in df.iterrows():
        d0, dx = str(r["day"]), str(r["exit_day"])
        if d0 not in firstd or dx not in last: continue
        a, b = firstd[d0], last[dx]
        e = a
        while e <= b and TT[e] < bt.ENTRY_T: e += 1
        if e > b: continue
        atmk = round(SPOT[e]/STEP)*STEP
        sl = ATMK[e:b+1]
        for K in (atmk+off*STEP, atmk-off*STEP, atmk+(off+wing)*STEP, atmk-(off+wing)*STEP):
            if (np.abs(np.round((K - sl)/STEP)) > 10).any(): bad += 1; break
    print(f"  SELL+-{off*100:<4}/BUY+-{(off+wing)*100:<5}{len(df):>8}{bad:>14}{100*bad/max(len(df),1):>7.1f}%")
