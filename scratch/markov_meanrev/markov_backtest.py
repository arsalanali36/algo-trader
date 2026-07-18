"""Backtest of the video's rule: buy-the-dip mean reversion on daily closes.

Rule (faithful to the video):
  ENTRY : N consecutive DOWN closes  -> go LONG
  EXIT  : first day that closes higher than the previous day's close
          (with a max-hold cap and an optional stop as guards)

Positional / holds overnight (NOT the intraday 15:15 engine). Long-only,
one position at a time. Reports vs Buy & Hold, sweeps N, real-ish costs.

Run:  python markov_backtest.py [daily_csv] [--n 3] [--cost-bps 3] [--max-hold 10]
      python markov_backtest.py --sweep
"""
import sys
import numpy as np
import pandas as pd
from _common import load_daily, DEFAULT_DAILY


def backtest(df, n_down=3, cost_bps=3.0, max_hold=10, stop_pct=None,
             entry="close"):
    """entry='close' fills on the signal day's close (idealised, matches video);
    entry='next_open' would need an Open column — kept simple with close here.
    Returns (trades_df, stats)."""
    c = df["Close"].values
    dates = df["Date"].values
    down = np.r_[False, c[1:] < c[:-1]]          # today closed below yesterday
    # consecutive down-run length ending at i
    run = np.zeros(len(c), dtype=int)
    for i in range(1, len(c)):
        run[i] = run[i - 1] + 1 if down[i] else 0

    trades = []
    i = n_down
    pos_entry = None
    while i < len(c):
        if pos_entry is None:
            if run[i] >= n_down:
                pos_entry = (i, c[i])
            i += 1
            continue
        # in a position: exit when close > previous close, or guards hit
        ei, ep = pos_entry
        held = i - ei
        up_close = c[i] > c[i - 1]
        stop_hit = stop_pct is not None and c[i] <= ep * (1 - stop_pct / 100)
        maxed = held >= max_hold
        if up_close or stop_hit or maxed:
            gross = (c[i] - ep) / ep * 100
            net = gross - 2 * cost_bps / 100        # entry+exit cost in %
            reason = "up_close" if up_close else ("stop" if stop_hit else "max_hold")
            trades.append(dict(entry_date=dates[ei], exit_date=dates[i],
                               entry=ep, exit=c[i], bars=held,
                               gross_pct=gross, net_pct=net, reason=reason))
            pos_entry = None
        i += 1

    td = pd.DataFrame(trades)
    stats = _stats(td, df)
    return td, stats


def _stats(td, df):
    bh = (df["Close"].iloc[-1] / df["Close"].iloc[0] - 1) * 100
    if td.empty:
        return dict(trades=0, buyhold_pct=bh)
    wins = (td["net_pct"] > 0).sum()
    # compounded equity of the strategy (sequential, 100% in per trade)
    eq = (1 + td["net_pct"] / 100).cumprod()
    total = (eq.iloc[-1] - 1) * 100
    peak = eq.cummax()
    maxdd = ((eq - peak) / peak).min() * 100
    return dict(
        trades=len(td),
        win_rate=wins / len(td) * 100,
        avg_net_pct=td["net_pct"].mean(),
        total_net_pct=total,
        max_dd_pct=maxdd,
        avg_bars=td["bars"].mean(),
        buyhold_pct=bh,
    )


def _print_stats(tag, s):
    if s.get("trades", 0) == 0:
        print(f"{tag}: 0 trades   (buy&hold {s['buyhold_pct']:+.1f}%)")
        return
    print(f"{tag}: {s['trades']:>4} trades | win {s['win_rate']:>5.1f}% | "
          f"avg {s['avg_net_pct']:+.3f}% | total {s['total_net_pct']:+7.1f}% | "
          f"maxDD {s['max_dd_pct']:>6.1f}% | avgHold {s['avg_bars']:.1f}d | "
          f"B&H {s['buyhold_pct']:+.1f}%")


def main():
    argv = sys.argv[1:]
    path = next((a for a in argv if not a.startswith("--")), DEFAULT_DAILY)

    def opt(name, default, cast=float):
        if name in argv:
            return cast(argv[argv.index(name) + 1])
        return default

    df = load_daily(path, opt("--start", None, str), opt("--end", None, str))
    cost = opt("--cost-bps", 3.0)
    mh = opt("--max-hold", 10, int)
    stop = opt("--stop-pct", None, lambda x: float(x))
    print(f"Loaded {len(df)} bars {df['Date'].min().date()}..{df['Date'].max().date()} "
          f"| cost {cost}bps/side | max-hold {mh}d "
          f"| stop {stop if stop else 'off'}\n")

    if "--sweep" in argv:
        print("Buy-the-dip: N consecutive down closes -> long, exit on up close")
        for n in range(1, 8):
            _, s = backtest(df, n_down=n, cost_bps=cost, max_hold=mh, stop_pct=stop)
            _print_stats(f"  N={n}", s)
    else:
        n = opt("--n", 3, int)
        td, s = backtest(df, n_down=n, cost_bps=cost, max_hold=mh, stop_pct=stop)
        _print_stats(f"N={n}", s)
        if not td.empty:
            out = path.replace(".csv", f"_trades_N{n}.csv")
            td.to_csv(out, index=False)
            print(f"\n{len(td)} trades -> {out}")


if __name__ == "__main__":
    main()
