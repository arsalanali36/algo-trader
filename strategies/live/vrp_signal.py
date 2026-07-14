"""VRP panic-fade ENTRY SIGNAL — IV-rank of NIFTY (see _ADR/ADR-006).

Edge (validated 2026-07-12): sell weekly ATM straddle ONLY when NIFTY IV is in its
top ~20% of the trailing window (a "fear spike"). This module answers ONE question:
"is today's IV high enough to enter?" — the trader (vrp_straddle_trader.py) does the
orders. Kept separate so the ranking logic is unit-testable offline against the lake.

IV-rank = fraction of the trailing `lookback` days whose IV <= today's IV (0..1).
Matches optlake_load.iv_rank_daily() exactly, so a live signal reproduces the backtest.

Two IV sources (config `iv_source`):
  "atm"  live NIFTY ATM CE/PE implied-vol average — the EXACT quantity the backtest
         used; seedable historically straight from the option-lake.
  "vix"  India VIX (already an annualised IV %); simpler/robuster live read, but its
         history must come from Dhan VIX daily candles (not in the option-lake).

History is persisted (data/vrp_iv_history.json) and appended once per trading day, so
the trailing window survives restarts (TRAP #3 / restart-safe).
"""
import os, json, glob
import numpy as np
import pandas as pd

LOOKBACK = 60          # trailing days for the rank window
MIN_DAYS = 20          # need at least this many before ranking (else no signal)
ENTER_THR = 0.80       # IV-rank >= this → eligible to sell
IV_LO, IV_HI = 3.0, 120.0

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
HIST_PATH = os.path.join(ROOT, "data", "vrp_iv_history.json")
LAKE = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake", "NIFTY")


# ── core ranking (pure, testable) ──────────────────────────────────────────
def iv_rank(today_iv, history_iv, lookback=LOOKBACK, min_days=MIN_DAYS):
    """rank (0..1) of today_iv vs the trailing `lookback` values in history_iv.
    None if not enough history. history_iv = list of prior days' IV (chronological)."""
    if today_iv is None or not np.isfinite(today_iv):
        return None
    w = [v for v in history_iv[-lookback:] if v is not None and np.isfinite(v)]
    if len(w) < min_days:
        return None
    w = np.array(w + [today_iv], dtype=float)
    return float((w[-1] >= w).mean())


def should_enter(today_iv, history_iv, thr=ENTER_THR, **kw):
    r = iv_rank(today_iv, history_iv, **kw)
    return (r is not None and r >= thr), r


# ── history persistence (date -> iv), restart-safe ─────────────────────────
def load_history():
    try:
        with open(HIST_PATH) as f:
            return dict(json.load(f))
    except Exception:
        return {}


def save_history(hist):
    os.makedirs(os.path.dirname(HIST_PATH), exist_ok=True)
    tmp = HIST_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(hist, f)
    os.replace(tmp, HIST_PATH)          # atomic (no half-write on kill)


def record_today(date_str, iv, hist=None):
    """append/update today's IV; returns the updated dict (does NOT auto-save)."""
    hist = dict(hist if hist is not None else load_history())
    if iv is not None and np.isfinite(iv):
        hist[date_str] = float(iv)
    return hist


def _sorted_iv_list(hist, upto_date=None):
    items = sorted(hist.items())
    if upto_date is not None:
        items = [(d, v) for d, v in items if d < upto_date]   # STRICTLY prior days
    return [v for _, v in items]


def rank_for(date_str, today_iv, hist=None, thr=ENTER_THR):
    """convenience: (eligible, rank) for a given day using strictly-prior history."""
    hist = load_history() if hist is None else hist
    prior = _sorted_iv_list(hist, upto_date=date_str)
    return should_enter(today_iv, prior, thr=thr)


# ── seed from the option-lake (ATM source only — matches the backtest) ─────
def _lake_daily_atm_iv(flag="WEEK", lake=None):
    """day(str YYYY-MM-DD) -> day-OPEN ATM IV (mean of CE/PE), from the lake CSVs.
    Self-contained (no optlake_load dependency) so it runs anywhere the lake exists."""
    lake = lake or LAKE
    def _side(side):
        p = os.path.join(lake, flag, f"{side}_ATM.csv")
        if not os.path.exists(p):
            return None
        d = pd.read_csv(p, usecols=["timestamp", "iv"])
        d["dt"] = (pd.to_datetime(d.timestamp, unit="s", utc=True)
                     .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
        d.loc[(d.iv < IV_LO) | (d.iv > IV_HI), "iv"] = np.nan
        d["day"] = d.dt.dt.date
        return d.dropna(subset=["iv"])
    ce, pe = _side("CE"), _side("PE")
    if ce is None or pe is None:
        return {}
    m = ce.merge(pe, on="dt", suffixes=("_ce", "_pe"))
    m["atm_iv"] = m[["iv_ce", "iv_pe"]].mean(axis=1)
    m["day"] = m.dt.dt.date
    op = m.sort_values("dt").groupby("day").atm_iv.first()
    return {str(d): float(v) for d, v in op.items() if np.isfinite(v)}


def seed_from_lake(flag="WEEK", merge=True):
    """(re)build the IV history from the lake's ATM IV. merge=True keeps any newer
    live-appended days already present. Returns the count seeded."""
    seed = _lake_daily_atm_iv(flag)
    hist = load_history() if merge else {}
    for d, v in seed.items():
        hist.setdefault(d, v)       # never overwrite a live-recorded day
    save_history(hist)
    return len(seed)


if __name__ == "__main__":
    import sys
    lake_override = sys.argv[1] if len(sys.argv) > 1 else None
    if lake_override:
        LAKE = lake_override
    n = seed_from_lake("WEEK")
    hist = load_history()
    days = sorted(hist)
    print(f"seeded {n} lake days; history now {len(hist)} days "
          f"({days[0]}..{days[-1]})")
    # replay: which days would have FIRED (rank>=0.80), strictly-prior window
    fired = []
    for d in days:
        elig, r = rank_for(d, hist[d], hist=hist)
        if elig:
            fired.append((d, round(hist[d], 1), round(r, 2)))
    print(f"days IV-rank>=0.80 (would ENTER): {len(fired)}")
    for d, iv, r in fired:
        print(f"  {d}  IV={iv:5.1f}  rank={r:.2f}")
