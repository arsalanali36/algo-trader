"""Step 6 — Monte Carlo overfit stress test (trade bootstrap).

Each trade is expressed as a return on the equity it risked. We resample the
trade sequence WITH replacement 1,000x, compound each resampled sequence from
the starting capital, and read off the distribution of the headline metrics.

If the ORIGINAL backtest sits near the MEDIAN of the simulations (not up in the
best-5%), the result is not a lucky ordering / curve-fit — the same message the
video's Monte Carlo panel conveys.
"""
import numpy as np
import pandas as pd
import engine


def _trade_returns(res):
    """per-trade return = pnl / equity just before the trade (reconstructed)."""
    eq = engine.START_CAP
    rets = []
    for t in res["trades"]:
        rets.append(t["pnl"] / eq)
        eq += t["pnl"]
    return np.array(rets)


def _path_metrics(rets):
    eq = engine.START_CAP * np.cumprod(1 + rets)
    eq = np.concatenate([[engine.START_CAP], eq])
    net = (eq[-1] / engine.START_CAP - 1) * 100
    peak = np.maximum.accumulate(eq)
    maxdd = ((eq / peak - 1) * 100).min()
    # per-trade Sharpe proxy annualized to ~trades/yr cadence
    if rets.std() == 0:
        sharpe = 0.0
    else:
        sharpe = rets.mean() / rets.std() * np.sqrt(len(rets) / 4.5)  # ~4.5yr window
    return net, maxdd, sharpe, eq


def montecarlo(res, n_sims=1000, seed=11):
    rets = _trade_returns(res)
    if len(rets) < 10:
        return None
    rng = np.random.default_rng(seed)
    nets, dds, sharpes, paths = [], [], [], []
    for _ in range(n_sims):
        samp = rng.choice(rets, size=len(rets), replace=True)
        net, dd, sh, eq = _path_metrics(samp)
        nets.append(net); dds.append(dd); sharpes.append(sh)
        if len(paths) < 120:                     # keep a subset for the fan chart
            paths.append(eq.tolist())
    orig_net, orig_dd, orig_sh, orig_eq = _path_metrics(rets)

    def pct(a, q):
        return float(np.percentile(a, q))

    win_rate = float((rets > 0).mean() * 100)
    return dict(
        n_sims=n_sims,
        original=dict(net=orig_net, maxdd=orig_dd, sharpe=orig_sh,
                      win_rate=win_rate, trades=len(rets)),
        table=dict(
            net=[orig_net, pct(nets, 5), pct(nets, 50), pct(nets, 95)],
            maxdd=[orig_dd, pct(dds, 5), pct(dds, 50), pct(dds, 95)],
            sharpe=[orig_sh, pct(sharpes, 5), pct(sharpes, 50), pct(sharpes, 95)],
        ),
        sharpe_dist=dict(median=pct(sharpes, 50), best5=pct(sharpes, 95),
                         worst5=pct(sharpes, 5), original=orig_sh),
        paths=paths, orig_path=orig_eq.tolist(),
        # overfit verdict: original should be <= best5 and near median
        not_overfit=bool(orig_sh <= pct(sharpes, 95) + 1e-9 and
                         orig_sh <= pct(sharpes, 50) * 1.6 + 0.3),
    )


if __name__ == "__main__":
    df = engine.load()
    from optimize import optimize
    for variant, mode in (("trail", "positional"), ("atr", "positional")):
        c = optimize(df, variant, mode, trials=200)[0]
        res = engine.backtest(df, variant, mode, c["params"])
        mc = montecarlo(res)
        t = mc["table"]
        print(f"{variant}/{mode}: net orig={t['net'][0]:.0f}% "
              f"[w5 {t['net'][1]:.0f} / med {t['net'][2]:.0f} / b5 {t['net'][3]:.0f}]  "
              f"sharpe orig={t['sharpe'][0]:.2f} med={t['sharpe'][2]:.2f} "
              f"-> {'NOT overfit' if mc['not_overfit'] else 'OVERFIT?'}")
