"""Claim-2 REAL-DATA cross-check (TRAP #106/#109-safe) — held-strike overnight hold.

Winner from pivot_nextday_bt.py (band(3,4,5), eod entry) repriced on the REAL
Dhan option lake via real_struct2.grid (MONTH flag): BUY ATM CE/PE at day-D
15:00, HOLD the same strike overnight, SELL next-day 15:00. Holds that cross the
monthly expiry day are skipped (the rolling series switches contract there).
Real premiums include real VRP + real overnight theta. Charges + slippage on.
"""
import datetime as dt
import numpy as np
import pandas as pd
import engine
import bs_option as bs
import real_struct2 as r2
import pivot_nextday_bt as nb

CUT = dt.time(15, 0)
SLIP = 0.005


def run(flag="MONTH", tf="5m", band=(3, 4, 5), entry="eod"):
    lot = bs.get_nifty_lot()
    days, rows, lvls, _ = nb.day_frame()
    sig = nb.signals(days, rows, lvls, band)

    g = r2.grid(flag, tf)
    DT, DAY, TT = g["DT"], g["DAY"], g["TT"]
    # last bar at/<= 15:00 per lake day + expiry-day flags (ATM strike jump guard
    # not needed: monthly roll happens on monthly expiry; detect via next-expiry calc)
    import expiry_calendar as xcal
    cut_i = {}; open_i = {}
    for i in range(len(DT)):
        if TT[i] <= CUT:
            cut_i[DAY[i]] = i
        if DAY[i] not in open_i:
            open_i[DAY[i]] = i
    lake_days = sorted(cut_i.keys())
    exp_day = {d: (xcal is not None and d == bs._next_monthly_expiry(pd.Timestamp(d)).date())
               for d in lake_days}

    eq = engine.START_CAP; trades = []; eqc = []
    for j in range(len(lake_days) - 1):
        d0, d1 = lake_days[j], lake_days[j + 1]
        s = sig.get(d0, 0)
        if s == 0:
            eqc.append((DT[cut_i[d1]], eq)); continue
        if entry == "eod" and exp_day.get(d0):
            eqc.append((DT[cut_i[d1]], eq)); continue    # overnight hold would cross monthly roll
        i0 = cut_i[d0] if entry == "eod" else open_i[d1]
        i1 = cut_i[d1]
        side = "CE" if s > 0 else "PE"
        K = g["ATMK"][i0]
        ep = r2._px(g, i0, side, K)
        xp = r2._px(g, i1, side, K)
        if ep is None or xp is None or ep <= 0:
            eqc.append((DT[i1], eq)); continue
        gross = (xp - ep) * lot
        fee = bs.calc_charges(ep, xp, lot, entry_side="BUY")
        slip = SLIP * (ep + xp) * lot
        pnl = gross - fee - slip
        eq += pnl
        trades.append(dict(side="long" if s > 0 else "short", entry=ep, exit=xp,
                           qty=lot, pnl=pnl, points=round(xp - ep, 2),
                           entry_dt=str(DT[i0]), exit_dt=str(DT[i1]),
                           strike=int(K), opt=side, bars=1, reason="NextDay real"))
        eqc.append((DT[i1], eq))
    res = dict(trades=trades, equity=pd.DataFrame(eqc, columns=["Datetime", "equity"]),
               final=eq, mode="positional", variant="pivot_nextday_real", params={})
    m, _ = engine.metrics(res)
    a = np.array([t["pnl"] for t in trades])
    print(f"REAL {flag} lake  band={list(band)} entry={entry}")
    print(f"  window: {trades[0]['entry_dt'][:10]} -> {trades[-1]['exit_dt'][:10]}" if trades else "  no trades")
    print(f"  N={m['trades']} net%={m['net_pct']:.1f} sharpe={m['sharpe']:.2f} "
          f"maxDD={m['maxdd']:.1f} win%={m['win_rate']:.0f} worst={a.min():.0f} best={a.max():.0f}")
    # same window BS-model comparison (apples to apples)
    if trades:
        w0 = trades[0]["entry_dt"][:10]
        _, rows2, _, daily_close = nb.day_frame()
        sigma = bs.realised_vol_map(daily_close)
        res_sp = nb.backtest(days, rows, sig, entry, lot)
        sub = [t for t in res_sp["trades"] if t["entry_dt"][:10] >= w0]
        bstr = bs.reprice_positional(sub, sigma, lot, 1)
        eqb = engine.START_CAP; ec = []
        for t in bstr:
            eqb += t["pnl"]; ec.append((t["exit_dt"], eqb))
        rb = dict(trades=[dict(t, entry=t["entry_prem"], exit=t["exit_prem"]) for t in bstr],
                  equity=pd.DataFrame(ec, columns=["Datetime", "equity"]),
                  final=eqb, mode="positional", variant="x", params={})
        mb, _ = engine.metrics(rb)
        print(f"  BS-model SAME window: N={mb['trades']} net%={mb['net_pct']:.1f} "
              f"sharpe={mb['sharpe']:.2f} maxDD={mb['maxdd']:.1f}")
    return res


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    run("MONTH", "5m", (3, 4, 5), "eod")
    print()
    run("MONTH", "5m", (2, 3, 4, 5), "nxtopen")
