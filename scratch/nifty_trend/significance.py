"""Step 4 — statistical significance of the ENTRY edge (rotation permutation test).

We build the position series pos_t in {-1,0,+1} from the winning strategy's
trades, and the market's next-bar returns. The real per-bar strategy return is
pos_t * ret_{t+1}. Under the null "entry timing carries no edge", rotating the
position series by a random offset (circular shift) preserves how long/often we
hold but destroys its alignment with future returns. We compute the Sharpe of
1,000 rotated series -> null distribution -> p-value = P(null >= real).

Rotation (not plain shuffle) keeps the autocorrelation of the holding pattern,
so the test isn't fooled by trend-following's naturally long holds.
"""
import numpy as np
import pandas as pd
import engine


def position_series(df, trades):
    pos = np.zeros(len(df))
    for t in trades:
        s = 1 if t["side"] == "long" else -1
        pos[t["entry_i"]:t["exit_i"] + 1] = s
    return pos


def _sharpe_from_bars(bar_ret, dates):
    d = pd.DataFrame({"r": bar_ret, "day": pd.to_datetime(dates).date})
    daily = d.groupby("day").r.sum()
    if daily.std() == 0 or len(daily) < 5:
        return 0.0
    return daily.mean() / daily.std() * np.sqrt(252)


def significance(df, variant, mode, params, n_perm=1000, seed=7):
    res = engine.backtest(df, variant, mode, params)
    d = engine.prep(df, {**engine.DEFAULTS[variant], **params})
    pos = position_series(d, res["trades"])
    ret = d.Close.pct_change().shift(-1).fillna(0).values      # next-bar return
    dates = d.Datetime.values

    real = _sharpe_from_bars(pos * ret, dates)
    rng = np.random.default_rng(seed)
    N = len(pos)
    null = np.empty(n_perm)
    for i in range(n_perm):
        k = rng.integers(engine.BARS_PER_DAY, N - engine.BARS_PER_DAY)
        null[i] = _sharpe_from_bars(np.roll(pos, k) * ret, dates)
    p = float((null >= real).mean())
    return dict(real_sharpe=float(real), p_value=p,
                null_mean=float(null.mean()), null_p95=float(np.percentile(null, 95)),
                n_perm=n_perm, significant=bool(p < 0.05))


if __name__ == "__main__":
    df = engine.load()
    from optimize import optimize
    for variant, mode in (("trail", "positional"), ("atr", "positional")):
        c = optimize(df, variant, mode, trials=200)[0]
        s = significance(df, variant, mode, c["params"])
        print(f"{variant}/{mode}: real_sharpe={s['real_sharpe']:.2f} "
              f"p={s['p_value']:.3f} null95={s['null_p95']:.2f} "
              f"{'SIGNIFICANT' if s['significant'] else 'not sig'}")
