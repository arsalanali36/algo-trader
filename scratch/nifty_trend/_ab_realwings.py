"""A/B: identical structure, identical trades logic — only the WING PRICE differs.
BS wings (legacy) vs REAL traded premium from the lake. The difference IS the BS error."""
import math
import bnf_hedged_backtest as bt
import bnf_920_strangle_intraday as base

g = base.load_grid()

def stats(df, label, fb0):
    n = len(df); net = df.net
    if n < 20:
        print(f"  {label:<34} n={n} (too few)"); return None
    sh = (net.mean()/net.std()*math.sqrt(252/ (df.hold.mean() or 1))) if net.std() else 0
    yrs = (df.exit_day.max() - df.day.min()).days/365.25
    sh = (net.mean()/net.std()*math.sqrt(n/yrs)) if net.std() and yrs else 0
    pf = net[net>0].sum()/(-net[net<0].sum()) if (net<0).any() else float("inf")
    eq = net.cumsum(); dd = (eq - eq.cummax()).min()
    fb = base._FALLBACK[0]-fb0[0]; tot = base._FALLBACK[1]-fb0[1]
    print(f"  {label:<34} n={n:<4} net=Rs{net.sum():>10,.0f}  Sharpe={sh:5.2f}  PF={pf:5.2f}  "
          f"win={100*(net>0).mean():4.1f}%  worst=Rs{net.min():>9,.0f}  maxDD=Rs{dd:>10,.0f}  "
          f"intrinsic-fallback={100*fb/max(tot,1):.2f}%")
    return dict(net=net.sum(), sharpe=sh, pf=pf, worst=net.min(), dd=dd, n=n)

print("="*140)
print("02.10.01 BNF hedged strangle — WING PRICE A/B  (sab kuch same, sirf wing ka daam badla)")
print("="*140)

out = {}
for off, wing in ((6,4), (5,5), (6,5)):
    tag = f"SELL ATM+-{off*100} / BUY ATM+-{(off+wing)*100}"
    print(f"\n{tag}   (wing width {wing*100} pts, wing offset {off+wing})")
    for real in (False, True):
        fb0 = list(base._FALLBACK)
        try:
            df = bt.run_positional(g, off, wing, 4000.0, 8000.0, 5,
                                   max_hold_days=1, exp_squareoff_days=2,
                                   real_wings=real)
        except ValueError as e:
            print(f"  {'REAL wings':<34} REFUSED — {str(e)[:95]}")
            continue
        out[(off,wing,real)] = stats(df, "REAL wings" if real else "BS wings", fb0)

print("\n" + "="*140)
print("BS ki galti (jahan dono chal sake)")
print("="*140)
for off, wing in ((6,4),(5,5)):
    a, b = out.get((off,wing,False)), out.get((off,wing,True))
    if a and b:
        print(f"  ATM+-{off*100}/+-{(off+wing)*100}:  net  BS Rs{a['net']:>10,.0f} -> REAL Rs{b['net']:>10,.0f}"
              f"  ({100*(b['net']-a['net'])/abs(a['net']):+.1f}%)   "
              f"Sharpe {a['sharpe']:.2f} -> {b['sharpe']:.2f}   "
              f"worst Rs{a['worst']:,.0f} -> Rs{b['worst']:,.0f}")
