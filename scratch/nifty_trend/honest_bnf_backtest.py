"""02.10.01 BNF hedged strangle — pehla IMAANDAR backtest.

Rules (dono user ke standing orders):
  • koi Black-Scholes nahi — saare 4 legs REAL traded premium se  ([[feedback_no_blackscholes_backtest]])
  • jo trade lake imaandari se price na kar sake, use SKIP karo — banao mat  (TRAP #198)

Lake ±20 hone ke baad hi ye chalana hai. Pehle `lake_coverage_check.py` chalao —
BANKNIFTY/MONTH 02.10.01 ke legs pe ~100% CLEAN aana chahiye.

Usage:  python honest_bnf_backtest.py
"""
import math
import numpy as np
import bnf_hedged_backtest as bt
import bnf_920_strangle_intraday as base

LOTS, SL, TGT = 5, 4000.0, 8000.0


def stats(df, label):
    n = len(df)
    sk = df.attrs.get("skipped_unpriceable", 0)
    if n < 30:
        print(f"  {label:<34} n={n} (bahut kam — {sk} skip hue)"); return
    net = df.net
    yrs = (df.exit_day.max() - df.day.min()).days / 365.25
    sh = (net.mean() / net.std() * math.sqrt(n / yrs)) if net.std() and yrs else 0
    pf = net[net > 0].sum() / (-net[net < 0].sum()) if (net < 0).any() else float("inf")
    eq = net.cumsum(); dd = (eq - eq.cummax()).min()
    print(f"  {label:<34} n={n:<5} skip={sk:<4} net=Rs{net.sum():>11,.0f}  Sharpe={sh:>5.2f}  "
          f"PF={pf:>4.2f}  win={100*(net>0).mean():>4.1f}%  worst=Rs{net.min():>9,.0f}  "
          f"maxDD=Rs{dd:>10,.0f}")


def main():
    g = base.load_grid()
    print("=" * 122)
    print("02.10.01 BNF hedged strangle — REAL premium, saare 4 legs, unpriceable trades SKIPPED")
    print("=" * 122)
    for off, wing in ((6, 5), (6, 4), (5, 5), (4, 4)):
        tag = f"SELL ATM+-{off*100} / BUY ATM+-{(off+wing)*100}"
        df = bt.run_positional(g, off, wing, SL, TGT, LOTS, max_hold_days=1,
                               exp_squareoff_days=2, real_wings=True, strict_skip=True)
        stats(df, tag)
    print("\n  skip = wo trades jinhe lake imaandari se price nahi kar saka.")
    print("  skip zyada ho to result par bharosa mat karo — pehle lake aur chaudi karo.")


if __name__ == "__main__":
    main()
