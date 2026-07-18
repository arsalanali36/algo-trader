"""Daily buy-the-dip Markov mean-reversion on a BASKET of stocks (video's real home).

Stocks swing 1-3%/day and mean-revert, so per-trade moves dwarf the index's
intraday micro-edge -> costs no longer necessarily eat it. We pool across the
basket (the video applies the rule to 25-90 stocks) and report GROSS vs NET vs
an equal-weight Buy & Hold benchmark, plus significance.

Data: _equity/Equity/<SYM>/<SYM>_<date>.csv  (per-day 1-min bars from the lake).
Daily bar = first/max/min/last of each day's 1-min bars.

Run: python stock_meanrev.py            # sweep N, both stats
     python stock_meanrev.py --n 3 --cost-bps 10 --max-hold 10 --sl 5
"""
import os
import sys
import glob
import math
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
EQ = os.path.join(HERE, "_equity", "Equity")
CACHE = os.path.join(HERE, "_equity", "_daily_cache")


def stock_daily(sym):
    """Build (and cache) a daily OHLC series for one symbol."""
    os.makedirs(CACHE, exist_ok=True)
    cpath = os.path.join(CACHE, f"{sym}.csv")
    if os.path.exists(cpath):
        return pd.read_csv(cpath, parse_dates=["Date"])
    rows = []
    for f in sorted(glob.glob(os.path.join(EQ, sym, f"{sym}_*.csv"))):
        try:
            d = pd.read_csv(f)
            if d.empty:
                continue
            rows.append(dict(
                Date=pd.to_datetime(d["Datetime"].iloc[0]).normalize(),
                Open=d["Open"].iloc[0], High=d["High"].max(),
                Low=d["Low"].min(), Close=d["Close"].iloc[-1]))
        except Exception:
            continue
    if not rows:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    df = pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)
    df.to_csv(cpath, index=False)
    return df


def symbols():
    return sorted(d for d in os.listdir(EQ) if os.path.isdir(os.path.join(EQ, d)))


def _binom_p(k, n, p0):
    sd = math.sqrt(n * p0 * (1 - p0)) or 1e-9
    return math.erfc(abs((k - n * p0) / sd) / math.sqrt(2))


def bt_stock(df, n, cost_bps, sl_pct, max_hold):
    """Buy-the-dip on ONE stock. Returns list of trade net %s + streak samples."""
    c = df["Close"].values
    if len(c) < n + 3:
        return [], 0, 0, 0.0
    down = np.r_[False, c[1:] < c[:-1]]
    run = np.zeros(len(c), int)
    for i in range(1, len(c)):
        run[i] = run[i - 1] + 1 if down[i] else 0

    # streak-conditioning counts (for significance): after run>=n, next day up?
    idx = np.where(run[:-1] >= n)[0]
    up_next = int((c[idx + 1] > c[idx]).sum()) if len(idx) else 0

    trades = []
    i = n
    pos = None
    while i < len(c):
        if pos is None:
            if run[i] >= n:
                pos = (i, c[i])
            i += 1
            continue
        ei, ep = pos
        held = i - ei
        up_close = c[i] > c[i - 1]
        stop = c[i] <= ep * (1 - sl_pct / 100)
        if up_close or stop or held >= max_hold:
            gross = (c[i] - ep) / ep * 100
            trades.append(gross - 2 * cost_bps / 100)
            pos = None
        i += 1
    return trades, len(idx), up_next, (c[-1] / c[0] - 1) * 100


def run(n, cost_bps, sl_pct, max_hold, syms):
    all_net, tot_samp, tot_up = [], 0, 0
    bh_list, strat_list = [], []
    for s in syms:
        df = stock_daily(s)
        trades, samp, up, bh = bt_stock(df, n, cost_bps, sl_pct, max_hold)
        tot_samp += samp
        tot_up += up
        if trades:
            all_net += trades
            comp = np.prod([1 + t / 100 for t in trades]) - 1
            strat_list.append(comp * 100)
            bh_list.append(bh)
    if not all_net:
        return None
    net = np.array(all_net)
    base_up = 0.5  # daily up-rate ~0.5 baseline for the bounce bet
    p_bounce = tot_up / tot_samp if tot_samp else float("nan")
    pv = _binom_p(tot_up, tot_samp, base_up) if tot_samp else float("nan")
    sd = net.std() or 1e-9
    # trades span ~1.7 yrs across the basket
    return dict(
        n=n, trades=len(net), win=(net > 0).mean() * 100,
        avg=net.mean(), total_gross=None,
        p_bounce=p_bounce, samp=tot_samp, pval=pv,
        pf=(net[net > 0].sum() / (-net[net < 0].sum() or 1e-9)),
        sharpe=(net.mean() / sd) * math.sqrt(len(net) / 1.7),
        avg_strat=np.mean(strat_list), avg_bh=np.mean(bh_list),
    )


def main():
    argv = sys.argv[1:]

    def opt(k, d, cast=float):
        return cast(argv[argv.index(k) + 1]) if k in argv else d

    cost = opt("--cost-bps", 10.0)
    sl = opt("--sl", 5.0)
    mh = opt("--max-hold", 10, int)
    syms = symbols()
    print(f"Basket: {len(syms)} stocks | {', '.join(syms)}")
    print(f"cost {cost}bps/side | SL {sl}% | maxHold {mh}d | "
          f"(bounce bet vs 50% base)\n")

    def show(r):
        if not r:
            print("  (no trades)"); return
        print(f"  N={r['n']}: {r['trades']:>5} tr | P(bounce) {r['p_bounce']:.3f} "
              f"(n={r['samp']}, p={r['pval']:.3f}) | win {r['win']:.1f}% | "
              f"avgNet {r['avg']:+.3f}% | PF {r['pf']:.2f} | Sh~{r['sharpe']:+.2f} | "
              f"strat/stock {r['avg_strat']:+.1f}% vs B&H {r['avg_bh']:+.1f}%")

    if "--n" in argv:
        show(run(opt("--n", 3, int), cost, sl, mh, syms))
    else:
        print("Buy-the-dip: N consecutive down closes -> long, exit on up close")
        for n in range(1, 7):
            show(run(n, cost, sl, mh, syms))


if __name__ == "__main__":
    main()
