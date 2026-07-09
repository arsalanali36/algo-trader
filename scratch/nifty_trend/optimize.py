"""Step 5 — parameter optimization with a train / out-of-sample (OOS) split.

Random search over the DNA grid. For each candidate we backtest on the TRAIN
window and the OOS window separately. Candidates are kept only if they clear a
robustness gate (enough trades + positive train Sharpe), then ranked by OOS
Sharpe — the same "best OOS candidate" logic the video used. This resists
curve-fitting: a param set that only shines in-sample is filtered out.
"""
import random
import numpy as np
import pandas as pd
import engine

SPLIT = 0.68            # train fraction (chronological)
MIN_TRADES = 30         # per window

GRID = {
    "trail": dict(bb_period=[14, 18, 20, 24, 28], bb_dev=[1.8, 2.0, 2.3, 2.6],
                  ema_period=[80, 120, 160, 200, 240], stop_mult=[1.5, 2.0, 2.5, 3.0],
                  trail_atr=[1.5, 2.0, 2.5, 3.0, 3.5]),
    "atr":   dict(bb_period=[14, 18, 20, 24, 28], bb_dev=[1.8, 2.0, 2.3, 2.6],
                  ema_period=[80, 120, 160, 200, 240], atr_sl=[1.5, 2.0, 2.5, 3.0],
                  atr_tp=[3.0, 4.0, 5.0, 6.0]),
}


def split(df):
    k = int(len(df) * SPLIT)
    return df.iloc[:k].reset_index(drop=True), df.iloc[k:].reset_index(drop=True)


def sample(grid):
    return {k: random.choice(v) for k, v in grid.items()}


def score(df_tr, df_oos, variant, mode, p):
    r1 = engine.backtest(df_tr, variant, mode, p)
    m1, _ = engine.metrics(r1)
    r2 = engine.backtest(df_oos, variant, mode, p)
    m2, _ = engine.metrics(r2)
    return m1, m2


def optimize(df, variant, mode, trials=250, seed=42, verbose=False):
    random.seed(seed + hash((variant, mode)) % 1000)
    df_tr, df_oos = split(df)
    grid = GRID[variant]
    seen, cands = set(), []
    for _ in range(trials):
        p = sample(grid)
        key = tuple(sorted(p.items()))
        if key in seen:
            continue
        seen.add(key)
        m1, m2 = score(df_tr, df_oos, variant, mode, p)
        if m1["trades"] < MIN_TRADES or m2["trades"] < MIN_TRADES:
            continue
        if m1.get("sharpe", 0) < 0.3:               # must work in-sample first
            continue
        cands.append(dict(params=p, train_sharpe=m1["sharpe"], oos_sharpe=m2["sharpe"],
                          train_net=m1["net_pct"], oos_net=m2["net_pct"],
                          oos_maxdd=m2["maxdd"], oos_trades=m2["trades"]))
    cands.sort(key=lambda c: c["oos_sharpe"], reverse=True)
    return cands


if __name__ == "__main__":
    import sys
    df = engine.load()
    for variant in ("trail", "atr"):
        for mode in ("positional", "intraday"):
            cands = optimize(df, variant, mode, trials=250)
            print(f"\n=== {variant} / {mode} ===  ({len(cands)} candidates passed gate)")
            for c in cands[:5]:
                print(f"  OOS_sh={c['oos_sharpe']:5.2f}  train_sh={c['train_sharpe']:5.2f}  "
                      f"OOS_net={c['oos_net']:6.1f}%  DD={c['oos_maxdd']:6.1f}%  "
                      f"tr={c['oos_trades']:3d}  {c['params']}")
