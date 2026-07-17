"""Ars chain — ASLI expired-option premium pe repricing. Ek number, do nahi.

Ab tak sab BS-modelled tha, aur uska sigma input maana hua tha (vrp 1.0 vs 1.2 vs 1.3 —
teen alag jawaab). Lake me asli premium pada hai, to maanne ki zaroorat khatam.

HELD-STRIKE, rolling-ATM NAHI. real_struct2._px(g, i, side, K) hi use karta hai — wo
K ko har bar pe current-ATM ke against offset me badalta hai. real_struct.py ne yahi
nahi kiya tha (rolling-ATM column ko held maan liya) → trend-day ka intrinsic loss chhup
gaya → Sharpe 8.9 ka artifact → usi din retract (TRAP #109). Isliye: sirf _px.

Coverage: lake 2021-07 se. Pre-2021 trades cover NAHI honge — ginti report hoti hai,
chupchaap skip nahi (TRAP #107 shape).

Diagnostic/one-off. Padhta hai, likhta kuch nahi.
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import arschain_backtest as ab   # noqa: E402
import bs_option as bs           # noqa: E402
import engine as eng             # noqa: E402  canonical Sharpe (Rule 6B — apna mat likhna)
import real_struct2 as rs2       # noqa: E402  held-strike engine

CACHE = os.path.join(_HERE, "trades_cache.pkl")
STEP = 50.0


def canonical(day_pnl, all_days):
    eq, run = [], eng.START_CAP
    for d in all_days:
        run += day_pnl.get(d, 0.0)
        eq.append((pd.Timestamp(d), run))
    eqdf = pd.DataFrame(eq, columns=["Datetime", "equity"])
    sh, _so, _r = eng._annualize_sharpe(eqdf)
    e = eqdf.equity.values
    pk = np.maximum.accumulate(e)
    return sh, ((e / pk - 1) * 100).min()


def main():
    print("\n  lake grid (WEEK, 5m) — held-strike...", flush=True)
    g = rs2.grid("WEEK", "5m")
    idx = {pd.Timestamp(t): i for i, t in enumerate(g["DT"])}
    print("  grid bars: %d | %s -> %s" % (
        len(g["DT"]), pd.Timestamp(g["DT"][0]).date(), pd.Timestamp(g["DT"][-1]).date()))

    cache = pickle.load(open(CACHE, "rb"))
    cont5 = ab.load_5m(None)
    daily = ab.daily_from_5m(cont5)
    all_days = list(daily["date"])
    lot = int(bs.get_nifty_lot())

    print("\n  " + "=" * 96)
    print("  ASLI PREMIUM (lake) — BUY vs SELL @ ATM, 1 lot, held-strike")
    print("  " + "-" * 96)
    print("  %-30s %6s %7s %12s %8s %7s %8s" % (
        "config / side", "trades", "cover", "NET Rs", "win%", "PF", "Sharpe"))

    for label in cache:
        trades = cache[label]
        for side_mode in ("BUY", "SELL"):
            rows, miss = [], 0
            for t in trades:
                i = idx.get(pd.Timestamp(t["entry_dt"]))
                j = idx.get(pd.Timestamp(t["exit_dt"]))
                if i is None or j is None:
                    miss += 1
                    continue
                # ATM at ENTRY = the strike we actually took, then HELD
                K = float(g["ATMK"][i])
                if side_mode == "BUY":
                    opt = "CE" if t["side"] == "long" else "PE"
                else:                                  # naked sell: long->PE, short->CE
                    opt = "PE" if t["side"] == "long" else "CE"
                ep, xp = rs2._px(g, i, opt, K), rs2._px(g, j, opt, K)
                if ep <= 0:
                    miss += 1
                    continue
                qty = lot
                gross = (xp - ep) * qty if side_mode == "BUY" else (ep - xp) * qty
                entry_side = "BUY" if side_mode == "BUY" else "SELL"
                fee = bs.calc_charges(ep, xp, qty, entry_side=entry_side,
                                      when=pd.Timestamp(t["entry_dt"]))
                slip = bs.slip_cost_leg(ep, xp, qty)
                rows.append((pd.Timestamp(t["exit_dt"]).date(), gross - fee - slip))
            if not rows:
                continue
            pnls = [p for _d, p in rows]
            dp = {}
            for d, p in rows:
                dp[d] = dp.get(d, 0.0) + p
            sh, dd = canonical(dp, all_days)
            w = [p for p in pnls if p > 0]
            l = [p for p in pnls if p <= 0]
            pf = (sum(w) / abs(sum(l))) if l and sum(l) else float("inf")
            cov = 100.0 * len(pnls) / (len(pnls) + miss)
            print("  %-30s %6d %6.0f%% %12s %7.1f%% %7.2f %8.2f" % (
                label[:28] + " " + side_mode, len(pnls), cov, f"{sum(pnls):,.0f}",
                100.0 * len(w) / len(pnls), pf, sh))
    print("  " + "=" * 96)
    print("  cover = lake me mile trades (baaki 2021-07 se pehle ke — chupchaap skip nahi)")
    print()


if __name__ == "__main__":
    main()
