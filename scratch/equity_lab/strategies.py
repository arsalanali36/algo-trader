"""Equity strategy plugins. Each factory returns:
    weight_fn(close, rebal) -> {rebal_date: {sym: weight}}   (weights sum to 1, equal-weight)
and (for cross-sectional ones) a matching random_weight_fn for the permutation significance null.

Add a new equity idea = add a factory here. The engine handles everything else (portfolio,
cost, tax, metrics, significance, MC). Sector/selloff below are SCAFFOLDED — the framework
runs them, but each needs its own validation run + honest gate before any trust (like the
options mission: significance p<0.05 + train&OOS both positive, survivorship-aware).
"""
import numpy as np
import pandas as pd
from sectors import SECTOR_OF


def _elig(close, i, extra=None):
    """Names tradeable at bar i (price present today). extra = optional bool mask."""
    m = close.iloc[i].notna()
    if extra is not None:
        m = m & extra
    return m


# ============================ 1. CROSS-SECTIONAL MOMENTUM (validated) ============================
def momentum(lookback_days=252, skip_days=5, top_frac=0.10, min_names=30):
    """Jegadeesh-Titman: rank by trailing return (skip last week), long top-decile equal-wt.
    THE validated one (10.01). Survivorship decisive-check passed (long-short t=3.01)."""
    def weight_fn(close, rebal):
        W = {}; dates = close.index
        for rd in rebal:
            i = dates.get_loc(rd)
            if i - lookback_days < 0:
                continue
            mom = (close.iloc[i - skip_days] / close.iloc[i - lookback_days]) - 1.0
            mom = mom[_elig(close, i) & mom.notna()]
            if len(mom) < min_names:
                continue
            k = max(1, int(round(len(mom) * top_frac)))
            winners = mom.sort_values(ascending=False).head(k).index.tolist()
            W[rd] = {s: 1.0 / k for s in winners}
        return W

    def random_weight_fn(close, rebal, rng):
        W = {}; dates = close.index
        for rd in rebal:
            i = dates.get_loc(rd)
            if i - lookback_days < 0:
                continue
            names = close.iloc[i].dropna().index.tolist()
            if len(names) < min_names:
                continue
            k = max(1, int(round(len(names) * top_frac)))
            pick = rng.choice(names, size=k, replace=False)
            W[rd] = {s: 1.0 / k for s in pick}
        return W
    return weight_fn, random_weight_fn


# ============================ 2. SECTOR ROTATION (scaffold — validate before trust) ============================
def sector_rotation(lookback_days=126, skip_days=5, top_sectors=3, names_per_sector=3):
    """Rank SECTORS by their equal-weight momentum, hold the top-N sectors, and within each
    hold its strongest names. Captures 'which sector is leading' rotation. SCAFFOLD."""
    def weight_fn(close, rebal):
        W = {}; dates = close.index
        for rd in rebal:
            i = dates.get_loc(rd)
            if i - lookback_days < 0:
                continue
            mom = (close.iloc[i - skip_days] / close.iloc[i - lookback_days]) - 1.0
            mom = mom[_elig(close, i) & mom.notna()]
            if mom.empty:
                continue
            # sector momentum = mean of member momentum
            by_sec = {}
            for s, v in mom.items():
                by_sec.setdefault(SECTOR_OF.get(s, "Other"), []).append((s, v))
            sec_mom = {sec: np.mean([v for _, v in lst]) for sec, lst in by_sec.items()}
            top = sorted(sec_mom, key=sec_mom.get, reverse=True)[:top_sectors]
            picks = []
            for sec in top:
                members = sorted(by_sec[sec], key=lambda x: x[1], reverse=True)[:names_per_sector]
                picks += [s for s, _ in members]
            if picks:
                W[rd] = {s: 1.0 / len(picks) for s in picks}
        return W

    def random_weight_fn(close, rebal, rng):
        W = {}; dates = close.index
        n_hold = top_sectors * names_per_sector
        for rd in rebal:
            i = dates.get_loc(rd)
            if i - lookback_days < 0:
                continue
            names = close.iloc[i].dropna().index.tolist()
            if len(names) < n_hold:
                continue
            pick = rng.choice(names, size=n_hold, replace=False)
            W[rd] = {s: 1.0 / len(pick) for s in pick}
        return W
    return weight_fn, random_weight_fn


# ============================ 3. SELLOFF-DIP BUY (scaffold — validate before trust) ============================
def selloff_dip(drop_lookback=21, drop_thresh=-0.15, hold_top=10, min_names=20):
    """'Major selloff' identifier: each rebalance, buy the names that fell the MOST over the
    last drop_lookback days beyond drop_thresh (deepest-selloff quintile), betting on rebound.
    Indian large-caps were continuation on daily (falling-knife) in prior tests — so this is a
    HYPOTHESIS to disprove, not a believed edge. SCAFFOLD."""
    def weight_fn(close, rebal):
        W = {}; dates = close.index
        for rd in rebal:
            i = dates.get_loc(rd)
            if i - drop_lookback < 0:
                continue
            ret = (close.iloc[i] / close.iloc[i - drop_lookback]) - 1.0
            ret = ret[_elig(close, i) & ret.notna()]
            crashed = ret[ret <= drop_thresh]
            if len(crashed) < 1:
                continue
            picks = crashed.sort_values().head(hold_top).index.tolist()  # deepest first
            W[rd] = {s: 1.0 / len(picks) for s in picks}
        return W

    def random_weight_fn(close, rebal, rng):
        W = {}; dates = close.index
        for rd in rebal:
            i = dates.get_loc(rd)
            names = close.iloc[i].dropna().index.tolist()
            if len(names) < min_names:
                continue
            k = min(hold_top, len(names))
            pick = rng.choice(names, size=k, replace=False)
            W[rd] = {s: 1.0 / len(pick) for s in pick}
        return W
    return weight_fn, random_weight_fn


REGISTRY = {
    "momentum": momentum,
    "sector_rotation": sector_rotation,
    "selloff_dip": selloff_dip,
}
