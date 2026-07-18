"""Momentum breakout on NIFTY & BANKNIFTY DAILY over the FULL 8.5-year history.

The stock lake is only ~1.7yr, but index 1-min data goes back to 2018 (incl the
2020 COVID crash, 2022 correction, multiple bull runs) -> a proper long/robust
test of the 50d-breakout momentum result.

  ENTRY : close makes a new L-day high      EXIT : after H bars, or stop
Long-only, positional. Reports CAGR / maxDD / Sharpe vs Buy & Hold, sweeps
(L,H), and splits TRAIN (2018-2022) vs OOS (2023-2026).

Run: python index_momentum_bt.py
"""
import os
import math
import numpy as np
import pandas as pd
from _common import load_daily, DEFAULT_DAILY

HERE = os.path.dirname(os.path.abspath(__file__))
BNF_1MIN = r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE\scratch\nifty_trend\bnf_1min.csv"
BNF_DAILY = os.path.join(HERE, "bnf_daily.csv")


def ensure_bnf():
    if os.path.exists(BNF_DAILY):
        return
    df = pd.read_csv(BNF_1MIN, parse_dates=["Datetime"])
    df["Date"] = df["Datetime"].dt.normalize()
    d = df.groupby("Date").agg(Close=("Close", "last")).reset_index()
    d.to_csv(BNF_DAILY, index=False)


def bt(df, L, H, cost_bps, stop_pct=5.0, window=None):
    if window:
        df = df[(df["Date"] >= window[0]) & (df["Date"] <= window[1])].reset_index(drop=True)
    c = df["Close"].values
    dates = df["Date"].values
    if len(c) < L + 5:
        return None
    hiN = pd.Series(c).rolling(L).max().shift(1).values   # prior L-day high
    daily_ret = np.full(len(c), np.nan)                    # strategy daily ret while in pos
    cside = cost_bps * 1e-4
    i = L
    pos = None
    while i < len(c):
        if pos is None:
            if not np.isnan(hiN[i]) and c[i] >= hiN[i]:
                pos = i
                daily_ret[i] = -cside                       # entry cost on entry day
            i += 1
            continue
        start = pos
        held = i - start
        r = c[i] / c[i - 1] - 1
        daily_ret[i] = r
        stop = c[i] <= c[start] * (1 - stop_pct / 100)
        if held >= H or stop or i == len(c) - 1:
            daily_ret[i] -= cside                           # exit cost
            pos = None
        i += 1

    port = pd.Series(np.nan_to_num(daily_ret, nan=0.0), index=pd.to_datetime(dates))
    bh = pd.Series(c, index=pd.to_datetime(dates)).pct_change().fillna(0.0)
    yrs = (port.index[-1] - port.index[0]).days / 365.25

    def m(x):
        eq = (1 + x).cumprod()
        cagr = (eq.iloc[-1] ** (1 / yrs) - 1) * 100
        dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
        act = x[x != 0]
        sh = (act.mean() / (act.std() or 1e-9)) * math.sqrt(252)
        return (eq.iloc[-1] - 1) * 100, cagr, dd, sh

    tot, cagr, dd, sh = m(port)
    btot, bcagr, bdd, bsh = m(bh)
    expo = (port != 0).mean() * 100
    trades = int(((port != 0) & (port.shift(1, fill_value=0) == 0)).sum())
    return dict(L=L, H=H, trades=trades, tot=tot, cagr=cagr, dd=dd, sharpe=sh,
                expo=expo, bh_tot=btot, bh_cagr=bcagr, bh_dd=bdd, bh_sharpe=bsh)


def show(tag, r):
    if not r:
        print(f"  {tag}: (no result)"); return
    print(f"  {tag}: {r['trades']:>3} tr | tot {r['tot']:>+7.1f}% | CAGR {r['cagr']:>+5.1f}% | "
          f"DD {r['dd']:>6.1f}% | Sharpe {r['sharpe']:>+5.2f} | expo {r['expo']:>4.0f}% "
          f"|| B&H tot {r['bh_tot']:>+6.1f}% CAGR {r['bh_cagr']:>+5.1f}% DD {r['bh_dd']:>6.1f}% Sh {r['bh_sharpe']:>+5.2f}")


def main():
    ensure_bnf()
    cost = 3.0   # index futures ~3bps/side realistic
    data = {"NIFTY": load_daily(DEFAULT_DAILY),
            "BANKNIFTY": load_daily(BNF_DAILY)}
    for inst, df in data.items():
        print(f"\n===== {inst}  {df['Date'].min().date()}..{df['Date'].max().date()} "
              f"({len(df)} days) | cost {cost}bps/side =====")
        print("  -- (L,H) sweep, full sample --")
        for L in [20, 50, 100, 200]:
            for H in [5, 10, 20]:
                show(f"L{L:>3}/H{H:>2}", bt(df, L, H, cost))
        print("  -- winner-ish L50/H10: TRAIN vs OOS --")
        show("TRAIN 18-22", bt(df, 50, 10, cost, window=(pd.Timestamp("2018-01-01"), pd.Timestamp("2022-12-31"))))
        show("OOS   23-26", bt(df, 50, 10, cost, window=(pd.Timestamp("2023-01-01"), pd.Timestamp("2026-12-31"))))


if __name__ == "__main__":
    main()
