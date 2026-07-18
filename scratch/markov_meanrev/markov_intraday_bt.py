"""Intraday Markov mean-reversion backtest (the edge the scan actually found).

The scan showed a real, significant reversion edge on 5-min NIFTY/BANKNIFTY:
after N consecutive DOWN bars price tends to bounce, after N consecutive UP bars
it tends to fade. This backtests that as a tradeable strategy and — crucially —
shows GROSS vs NET so you can see whether the tiny per-trade edge survives cost.

Strategy (both-sided mean reversion, intraday, session-aware):
  LONG  after N consecutive DOWN bars ;  SHORT after N consecutive UP bars
  EXIT  on first opposite-direction bar close, OR stop %, OR max-hold, OR 15:15 EOD
  House rules: no entry after 15:15, force-exit at EOD.

Run: python markov_intraday_bt.py NIFTY --tf 5 --sweep
     python markov_intraday_bt.py NIFTY --tf 5 --n 5 --cost-bps 1.5 --sl 0.4 --max-hold 6
"""
import sys
import math
import numpy as np
import pandas as pd

DATA = {
    "NIFTY": r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\nifty_1min.csv",
    "BANKNIFTY": r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\bnf_1min.csv",
}
NO_ENTRY_AFTER = (15, 15)   # house rule


def resample(path, minutes):
    df = pd.read_csv(path, parse_dates=["Datetime"])
    df["day"] = df["Datetime"].dt.normalize()
    df["bucket"] = df["Datetime"].dt.floor(f"{minutes}min")
    g = df.groupby("bucket")
    bars = g.agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
                 Close=("Close", "last"), day=("day", "first")).reset_index()
    bars.rename(columns={"bucket": "dt"}, inplace=True)
    return bars


def backtest(bars, n=5, cost_bps=1.5, sl_pct=0.4, max_hold=6):
    dt = bars["dt"].values
    c = bars["Close"].values
    day = bars["day"].values
    hr = bars["dt"].dt.hour.values
    mi = bars["dt"].dt.minute.values
    same_day = np.r_[False, day[1:] == day[:-1]]

    # per-bar direction vs previous bar (within session)
    up = np.r_[False, (c[1:] > c[:-1]) & same_day[1:]]
    dn = np.r_[False, (c[1:] < c[:-1]) & same_day[1:]]
    up_run = np.zeros(len(c), int)
    dn_run = np.zeros(len(c), int)
    for i in range(1, len(c)):
        if not same_day[i]:
            continue
        up_run[i] = up_run[i - 1] + 1 if up[i] else 0
        dn_run[i] = dn_run[i - 1] + 1 if dn[i] else 0

    def past_cutoff(i):
        return (hr[i], mi[i]) >= NO_ENTRY_AFTER

    trades = []
    i = 1
    pos = None   # (side, entry_i, entry_px)
    while i < len(c):
        if pos is None:
            if not past_cutoff(i):
                if dn_run[i] >= n:
                    pos = ("long", i, c[i])
                elif up_run[i] >= n:
                    pos = ("short", i, c[i])
            i += 1
            continue
        side, ei, ep = pos
        held = i - ei
        eod = (not same_day[i]) or ((hr[i], mi[i]) >= (15, 15))
        if not same_day[i]:
            # gap over day boundary -> exit at prior bar close (last of prev day)
            xp = c[i - 1]
        else:
            xp = c[i]
        if side == "long":
            revert = c[i] > c[i - 1]                    # a bounce = exit
            stop = c[i] <= ep * (1 - sl_pct / 100)
        else:
            revert = c[i] < c[i - 1]
            stop = c[i] >= ep * (1 + sl_pct / 100)
        if revert or stop or held >= max_hold or eod:
            sgn = 1 if side == "long" else -1
            gross = sgn * (xp - ep) / ep * 100
            net = gross - 2 * cost_bps / 100
            reason = ("EOD" if eod else "stop" if stop else
                      "revert" if revert else "maxhold")
            trades.append(dict(dt=dt[ei], side=side, entry=ep, exit=xp,
                               bars=held, gross=gross, net=net, reason=reason))
            pos = None
            if eod and same_day[i]:   # allow re-eval same bar next loop? keep simple
                pass
        i += 1
    return pd.DataFrame(trades)


def stats(td, years):
    if td.empty:
        return "0 trades"
    n = len(td)
    g = td["gross"].sum()
    net = td["net"].sum()
    win = (td["net"] > 0).mean() * 100
    avg = td["net"].mean()
    sd = td["net"].std() or 1e-9
    tpy = n / years
    sharpe = (td["net"].mean() / sd) * math.sqrt(tpy)   # per-trade Sharpe annualised
    pf_num = td.loc[td.net > 0, "net"].sum()
    pf_den = -td.loc[td.net < 0, "net"].sum() or 1e-9
    pf = pf_num / pf_den
    return (f"{n:>5} tr | win {win:>4.1f}% | gross {g:>+7.2f}% | NET {net:>+7.2f}% | "
            f"avg {avg:>+.4f}% | PF {pf:>4.2f} | Sharpe~{sharpe:>4.2f}")


def main():
    argv = sys.argv[1:]
    inst = next((a for a in argv if a in DATA), "NIFTY")

    def opt(k, d, cast=float):
        return cast(argv[argv.index(k) + 1]) if k in argv else d

    tf = opt("--tf", 5, int)
    cost = opt("--cost-bps", 1.5)
    sl = opt("--sl", 0.4)
    mh = opt("--max-hold", 6, int)
    bars = resample(DATA[inst], tf)
    years = (bars["dt"].iloc[-1] - bars["dt"].iloc[0]).days / 365.25
    print(f"{inst} {tf}m | {len(bars)} bars | {years:.1f} yrs | "
          f"cost {cost}bps/side | SL {sl}% | maxHold {mh} bars\n")

    if "--sweep" in argv:
        for n in range(2, 8):
            td = backtest(bars, n=n, cost_bps=cost, sl_pct=sl, max_hold=mh)
            print(f"  N={n}: {stats(td, years)}")
    else:
        n = opt("--n", 5, int)
        td = backtest(bars, n=n, cost_bps=cost, sl_pct=sl, max_hold=mh)
        print(f"N={n}: {stats(td, years)}")
        if not td.empty:
            print("  reasons:", td["reason"].value_counts().to_dict())


if __name__ == "__main__":
    main()
