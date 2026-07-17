"""Ars chain — rotation-permutation significance on the LIVE engine's own trades.

Ye wo test hai jo poore din NAHI hua tha, aur jisne aakhir me faisla kiya.

Reuses significance.py ka apna `position_series` + `_sharpe_from_bars` (Rule 6B) —
sirf driver alag hai, kyunki significance.significance() intraday_engine ke
variant/params ke around bana hai, aur yahan trades LIVE engine (range_trader) se
aate hain.

Natija (2026-07-17):
    2018-2026 (poora)         1950 trades | Sharpe 0.50 | null p95 0.55 | p=0.072  FAIL
    2026 sirf (TV wala daur)   104 trades | Sharpe 0.66 | null p95 2.39 | p=0.371  FAIL

`null p95 = 2.39` sabse ahem hai: usi position series ko RANDOM jagah ghuma do, aur wo
6.5-mahine ki window me routinely Sharpe 2.39 tak pahunch jaati hai — asli 0.66 hai.
Yaani TV ka "PF 2.024, Jan-Jul 2026" luck se alag NAHI hai. Chhoti window me sab achha
dikhta hai; ye gate isi liye hai.

Usage: python -X utf8 scratch/nifty_trend/arschain_significance.py
"""
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arschain_backtest as ab, significance as sg

BPD = 75   # 5m bars/day. engine.BARS_PER_DAY=7 hourly ke liye hai — us par mat jao.

def run(trades, d5, label, n_perm=1000, seed=7):
    idx = {pd.Timestamp(t): i for i, t in enumerate(d5["time"])}
    tr = []
    for t in trades:
        i, j = idx.get(pd.Timestamp(t["entry_dt"])), idx.get(pd.Timestamp(t["exit_dt"]))
        if i is None or j is None: continue
        tr.append(dict(side=t["side"], entry_i=i, exit_i=j))
    if not tr: return None
    pos = sg.position_series(d5, tr)
    ret = d5["close"].pct_change().shift(-1).fillna(0).values
    dates = d5["time"].values
    real = sg._sharpe_from_bars(pos * ret, dates)
    rng = np.random.default_rng(seed); N = len(pos)
    null = np.empty(n_perm)
    for k in range(n_perm):
        r = rng.integers(BPD, N - BPD)
        null[k] = sg._sharpe_from_bars(np.roll(pos, r) * ret, dates)
    p = float((null >= real).mean())
    print("  %-28s trades %4d | Sharpe %6.2f | null p95 %5.2f | p=%.3f  %s" % (
        label, len(tr), real, np.percentile(null, 95), p,
        "SIGNIFICANT" if p < 0.05 else "nahi (noise se alag nahi)"))

cont5 = ab.load_5m(None)
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades_cache.pkl")
if os.path.exists(CACHE):
    tr = pickle.load(open(CACHE, "rb"))['jaisa abhi hai (trail ON, zone ON)']
else:   # cache nahi to engine khud chala lo (~4 min)
    tr = ab.run_engine(cont5, ab.daily_from_5m(cont5), ab.engine_cfg())
print("\n  ROTATION-PERMUTATION significance (repo ka apna test, 1000 perms)")
print("  spot/instrument pass — signal ka apna edge, options ke bina\n")
for lab, cut in (("2018-2026 (poora)", None), ("2026 SIRF (TV wala daur)", "2026-01-01")):
    d5 = cont5 if cut is None else cont5[cont5["time"] >= pd.Timestamp(cut)].reset_index(drop=True)
    t2 = tr if cut is None else [t for t in tr if pd.Timestamp(t["entry_dt"]) >= pd.Timestamp(cut)]
    run(t2, d5, lab)
print()
