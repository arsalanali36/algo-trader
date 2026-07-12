"""Claim-2 real-lake check, VPS side — signals precomputed locally (pivot_signals.csv).
BUY ATM CE/PE at day-D 15:00 (or next-day open), HELD strike, sell next-day 15:00.
MONTH lake, real premiums, charges + 0.5% slippage. Skip monthly-roll crossings.
"""
import datetime as dt
import numpy as np
import pandas as pd
import engine
import bs_option as bs
import real_struct2 as r2

CUT = dt.time(15, 0)
SLIP = 0.005


def run(col, entry, flag="MONTH", tf="5m"):
    lot = bs.get_nifty_lot()
    sigdf = pd.read_csv("pivot_signals.csv")
    sig = {pd.Timestamp(r.day).date(): int(getattr(r, col)) for r in sigdf.itertuples()}
    g = r2.grid(flag, tf)
    DT, DAY, TT = g["DT"], g["DAY"], g["TT"]
    cut_i = {}; open_i = {}
    for i in range(len(DT)):
        if TT[i] <= CUT:
            cut_i[DAY[i]] = i
        if DAY[i] not in open_i:
            open_i[DAY[i]] = i
    lake_days = sorted(cut_i.keys())
    exp_day = {d: d == bs._next_monthly_expiry(pd.Timestamp(d)).date() for d in lake_days}

    eq = engine.START_CAP; trades = []; eqc = []
    for j in range(len(lake_days) - 1):
        d0, d1 = lake_days[j], lake_days[j + 1]
        s = sig.get(d0, 0)
        if s == 0 or (entry == "eod" and exp_day.get(d0)):
            eqc.append((DT[cut_i[d1]], eq)); continue
        i0 = cut_i[d0] if entry == "eod" else open_i[d1]
        i1 = cut_i[d1]
        side = "CE" if s > 0 else "PE"
        K = g["ATMK"][i0]
        ep = r2._px(g, i0, side, K); xp = r2._px(g, i1, side, K)
        if ep is None or xp is None or ep <= 0:
            eqc.append((DT[i1], eq)); continue
        pnl = (xp - ep) * lot - bs.calc_charges(ep, xp, lot, entry_side="BUY") - SLIP * (ep + xp) * lot
        eq += pnl
        trades.append(dict(side="long" if s > 0 else "short", entry=ep, exit=xp, qty=lot,
                           pnl=pnl, points=round(xp - ep, 2), entry_dt=str(DT[i0]),
                           exit_dt=str(DT[i1]), bars=1, reason="NextDay real"))
        eqc.append((DT[i1], eq))
    res = dict(trades=trades, equity=pd.DataFrame(eqc, columns=["Datetime", "equity"]),
               final=eq, mode="positional", variant="pivot_nextday_real", params={})
    m, _ = engine.metrics(res)
    a = np.array([t["pnl"] for t in trades])
    print(f"{col}/{entry}: window {trades[0]['entry_dt'][:10]}->{trades[-1]['exit_dt'][:10]} "
          f"N={m['trades']} net%={m['net_pct']:.1f} sharpe={m['sharpe']:.2f} "
          f"maxDD={m['maxdd']:.1f} win%={m['win_rate']:.0f} worst={a.min():.0f} best={a.max():.0f}")
    # yearly breakdown (regime honesty)
    ydf = pd.DataFrame(trades); ydf["y"] = ydf.exit_dt.str[:4]
    for y, gy in ydf.groupby("y"):
        print(f"   {y}: N={len(gy):3d} pnl={gy.pnl.sum():>9.0f}")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    run("dir_345", "eod")
    print()
    run("dir_2345", "nxtopen")
