"""Optimize intraday designs with train/OOS split + significance gate.
Searches design params AND exit_style. Ranks by OOS Sharpe (robustness), then
runs the rotation significance test on the best — only p<0.05 counts as an edge.
"""
import random, sys
import numpy as np
import pandas as pd
import intraday_engine as ie
import engine
from significance import _sharpe_from_bars

SPLIT = 0.68
MIN_TRADES = 60
EXIT_STYLES = ["atr_rr", "trail", "stop_only"]


def split(d):
    k = int(len(d) * SPLIT)
    return d.iloc[:k].reset_index(drop=True), d.iloc[k:].reset_index(drop=True)


def sample(name):
    g = ie.DESIGN_GRID[name]
    p = {k: random.choice(v) for k, v in g.items()}
    return p


def optimize(d, name, trials=180, seed=1):
    random.seed(seed + hash(name) % 1000)
    dtr, doos = split(d)
    seen, cands = set(), []
    for _ in range(trials):
        p = sample(name); es = random.choice(EXIT_STYLES)
        key = (tuple(sorted(p.items())), es)
        if key in seen:
            continue
        seen.add(key)
        m1, _ = engine.metrics(ie.backtest(dtr, name, p, es))
        if m1["trades"] < MIN_TRADES or m1.get("sharpe", -9) < 0.3:
            continue
        m2, _ = engine.metrics(ie.backtest(doos, name, p, es))
        if m2["trades"] < MIN_TRADES:
            continue
        cands.append(dict(params=p, exit=es, train_sharpe=m1["sharpe"], oos_sharpe=m2["sharpe"],
                          robust=min(m1["sharpe"], m2["sharpe"]),
                          train_net=m1["net_pct"], oos_net=m2["net_pct"], oos_dd=m2["maxdd"],
                          oos_trades=m2["trades"]))
    # Rank by min(train, OOS) — NOT OOS alone. Ranking by OOS fits the OOS window
    # (picks the candidate that got luckiest out-of-sample). LESSONS TRAP #103.
    cands.sort(key=lambda c: c["robust"], reverse=True)
    return cands


def sig_test(d, name, params, exit_style, n_perm=800, seed=7):
    """rotation permutation on the position series (same as significance.py)."""
    res = ie.backtest(d, name, params, exit_style)
    pos = np.zeros(len(d))
    for t in res["trades"]:
        pos[t["entry_i"]:t["exit_i"] + 1] = 1 if t["side"] == "long" else -1
    ret = d.Close.pct_change().shift(-1).fillna(0).values
    dates = d.Datetime.values
    real = _sharpe_from_bars(pos * ret, dates)
    rng = np.random.default_rng(seed)
    N = len(pos); null = np.empty(n_perm)
    for i in range(n_perm):
        k = rng.integers(20, N - 20)
        null[i] = _sharpe_from_bars(np.roll(pos, k) * ret, dates)
    p = float((null >= real).mean())
    return real, p, float(np.percentile(null, 95))


if __name__ == "__main__":
    tf = sys.argv[1] if len(sys.argv) > 1 else "15m"
    df1m = ie.load_1m(); d = ie.resample(df1m, tf)
    print(f"OPTIMIZE intraday @ {tf} (1x, no leverage)  bars={len(d)}\n")
    best_overall = []
    for name in ie.DESIGN_GRID:
        cands = optimize(d, name, trials=180)
        if not cands:
            print(f"  {name:11s} — no candidate passed gate"); continue
        c = cands[0]
        print(f"  {name:11s} OOS_sh={c['oos_sharpe']:5.2f} train={c['train_sharpe']:5.2f} "
              f"net={c['oos_net']:6.1f}% DD={c['oos_dd']:5.1f}% tr={c['oos_trades']:4d} "
              f"exit={c['exit']:6s} {c['params']}")
        best_overall.append((name, c))
    best_overall.sort(key=lambda x: x[1]["robust"], reverse=True)
    print("\n--- significance test on top 3 (full window) ---")
    for name, c in best_overall[:3]:
        real, p, n95 = sig_test(d, name, c["params"], c["exit"])
        print(f"  {name:11s} entry_sharpe={real:5.2f} p={p:.3f} null95={n95:.2f} "
              f"{'*** SIGNIFICANT ***' if p < 0.05 else 'not sig'}")
