"""Kis wing-offset tak REAL premium bharosemand hai? fallback% = jhooth ka paimana."""
import math
import bnf_hedged_backtest as bt
import bnf_920_strangle_intraday as base
g = base.load_grid()

def run(off, wing, real):
    fb0 = list(base._FALLBACK)
    df = bt.run_positional(g, off, wing, 4000.0, 8000.0, 5,
                           max_hold_days=1, exp_squareoff_days=2, real_wings=real)
    fb = base._FALLBACK[0]-fb0[0]; tot = base._FALLBACK[1]-fb0[1]
    net = df.net; n = len(df)
    yrs = (df.exit_day.max()-df.day.min()).days/365.25
    sh = (net.mean()/net.std()*math.sqrt(n/yrs)) if net.std() and yrs else 0
    pf = net[net>0].sum()/(-net[net<0].sum()) if (net<0).any() else float("inf")
    return dict(n=n, net=net.sum(), sh=sh, pf=pf, win=100*(net>0).mean(),
                worst=net.min(), fb=100*fb/max(tot,1))

print("="*132)
print("REAL wings — kis offset tak bharosa? (fallback% = kitni baar lake se bahar ja ke NAKLI intrinsic daam mila)")
print("="*132)
print(f"{'structure':<34}{'fallback':>10}{'n':>7}{'net Rs':>14}{'Sharpe':>9}{'PF':>7}{'win%':>7}{'worst Rs':>12}   verdict")
for off, wing in ((6,4),(6,3),(6,2),(5,3),(4,4),(4,3),(3,4)):
    if off+wing > 10: continue
    try:
        r = run(off, wing, True)
    except Exception as e:
        print(f"  SELL+-{off*100}/BUY+-{(off+wing)*100:<6} ERROR {e}"); continue
    v = "TRUSTWORTHY" if r["fb"] < 1.0 else ("shaky" if r["fb"] < 4 else "GARBAGE - lake se bahar")
    print(f"  SELL+-{off*100:<5}/ BUY+-{(off+wing)*100:<5} (w{wing*100})"
          f"{r['fb']:>9.2f}%{r['n']:>7}{r['net']:>14,.0f}{r['sh']:>9.2f}{r['pf']:>7.2f}"
          f"{r['win']:>7.1f}{r['worst']:>12,.0f}   {v}")

print("\nBS-wing baseline (reference):")
for off, wing in ((6,5),(6,4)):
    r = run(off, wing, False)
    print(f"  SELL+-{off*100:<5}/ BUY+-{(off+wing)*100:<5} (w{wing*100})"
          f"{r['fb']:>9.2f}%{r['n']:>7}{r['net']:>14,.0f}{r['sh']:>9.2f}{r['pf']:>7.2f}"
          f"{r['win']:>7.1f}{r['worst']:>12,.0f}   BS = evidence NAHI")
