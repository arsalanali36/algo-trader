"""Task 1 (ml-strategy-mining) — per-bar feature table for ML candidate mining.

Turns the LOCAL, lockbox-split option-chain lake (WEEK+MONTH, 5-min) + spot candles
into one clean per-5min-bar feature frame. FEATURES ONLY — targets/labels are built
in Task 2 next to the model, so no forward-looking column can leak in here.

House rules baked in (see ML_MINING_DATA_INVENTORY.md):
  - runs on the MINING machine against the lockbox-split lake; output is asserted
    lockbox-free (ml_gate.assert_no_lockbox) — a violation raises, never warns
  - IV-derived features only within ATM±5 (deep-ITM zero-IV gradient beyond that)
  - rolling series used as STATE features only (TRAP #109) — nothing here prices a
    held position
  - liquidity features are PROXIES (volume/OI); real bid-ask was never recorded —
    column names carry the _proxy suffix so nobody mistakes them for real spread
  - every feature uses information available AT the bar (rolling windows lag; day
    aggregates use today-so-far only)

Output versioning: ml_features_v{VERSION}.csv.gz — an existing version file is
NEVER silently overwritten (bump VERSION or pass --force). A row is appended to
ml_features_manifest.json describing the revision (same show-before/after
discipline as the other worklist's Task 6).

Run:  python -X utf8 ml_features.py [--flag WEEK] [--force]
"""
import os
import json
import argparse
import datetime as dt

import numpy as np
import pandas as pd

import ml_gate
import optlake_load as ol
import bs_option as bs
import expiry_calendar as xcal

HERE = os.path.dirname(os.path.abspath(__file__))
VERSION = 3   # v3 (2026-07-14): + expiry_regime (0=Thursday-expiry era, 1=Tuesday era
              # from 2025-09-01, expiry_calendar.py verified schedule). dte_days was
              # already calendar-aware; this makes the regime EXPLICIT so any mined
              # rule leaning on dow/dte can be checked for pre-vs-post stability —
              # "which weekday IS expiry" changed mid-dataset, a regime-boundary
              # overfitting vector distinct from DSR/lockbox.
              # v2 (2026-07-14): + rsi_14, ema20_dist_pct, ema50_dist_pct (spot 5m) —
              # same vocabulary the hand-designed live strategies use (user request)
OUT = os.path.join(HERE, "ml_features_v%d.csv.gz" % VERSION)
MANIFEST = os.path.join(HERE, "ml_features_manifest.json")

IV_OFFS = range(-5, 6)          # IV features stay inside ATM±5 (inventory gap #2)
OI_OFFS = range(-10, 11)        # OI is clean out to ±10
IVRANK_LOOKBACK = 60            # trading days, same as optlake_load.iv_rank_daily
RV_BARS = 20                    # ~100 minutes of 5-min bars
BARS_PER_DAY = 75


def _spot_5m():
    """5-min spot frame from nifty_1min.csv, lockbox-trimmed (shared file — the
    hand pipeline needs the full window, so we trim in memory, never on disk)."""
    df = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), parse_dates=["Datetime"])
    df = ml_gate.trim_lockbox(df)
    s = df.set_index("Datetime").resample("5min", label="left", closed="left").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna(subset=["Close"]).reset_index()
    tt = s.Datetime.dt.time
    s = s[(tt >= dt.time(9, 15)) & (tt <= dt.time(15, 25))].reset_index(drop=True)
    s["day"] = s.Datetime.dt.date
    return s


def _atr(s, n=14):
    hl = s.High - s.Low
    hc = (s.High - s.Close.shift()).abs()
    lc = (s.Low - s.Close.shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def build(flag="WEEK"):
    # ---- lake frames (already lockbox-carved on disk) ----
    atm_w = ol.atm_frame("WEEK", "5m")
    atm_m = ol.atm_frame("MONTH", "5m")
    chain = ol.chain_frame(flag, "5m", offs=OI_OFFS)
    if atm_w is None or atm_m is None or chain is None:
        raise RuntimeError("lake frames missing — is the local lake synced?")
    for name, f in (("atm_week", atm_w), ("atm_month", atm_m), ("chain", chain)):
        ml_gate.assert_no_lockbox(f, what=name)

    spot = _spot_5m()

    f = atm_w[["Datetime", "day", "spot", "strike", "atm_iv", "straddle",
               "ce_close", "pe_close", "ce_iv", "pe_iv", "ce_oi", "pe_oi",
               "ce_volume", "pe_volume"]].copy()

    # ---- IV level / rank / term structure ----
    day_open_iv = f.groupby("day").atm_iv.first()
    ivr = day_open_iv.rolling(IVRANK_LOOKBACK, min_periods=20).apply(
        lambda w: (w.iloc[-1] >= w).mean(), raw=False)
    f["iv_rank_60d"] = f.day.map(ivr)
    f["iv_chg_today"] = f.atm_iv / f.groupby("day").atm_iv.transform("first") - 1.0
    m_iv = atm_m[["Datetime", "atm_iv"]].rename(columns={"atm_iv": "month_iv"})
    f = f.merge(m_iv, on="Datetime", how="left")
    f["term_ratio"] = f.atm_iv / f.month_iv          # >1 = short-end stressed
    f["atm_skew"] = f.ce_iv - f.pe_iv

    # ---- straddle / premium-decay shape ----
    f["straddle_norm"] = f.straddle / f.spot * 100.0
    day_open_st = f.groupby("day").straddle.transform("first")
    f["straddle_decay_today"] = f.straddle / day_open_st - 1.0
    f["bars_into_day"] = f.groupby("day").cumcount()
    # decay vs a flat-theta baseline: residual of today's decay-so-far against a
    # pro-rata slope estimated from the TRAILING 5 days' full-day decay (shift(1)
    # keeps it leak-free — today's own full-day decay is never visible intraday)
    daily_decay = f.groupby("day").straddle_decay_today.last()
    trail_slope = daily_decay.rolling(5, min_periods=3).mean().shift(1)  # yesterday-back only
    f["decay_vs_linear"] = f.straddle_decay_today - \
        f.day.map(trail_slope) * (f.bars_into_day / float(BARS_PER_DAY))

    # ---- OI structure (chain ±10) ----
    ce_oi_cols = {off: "CE%s_oi" % ol._off_tag(off) for off in OI_OFFS}
    pe_oi_cols = {off: "PE%s_oi" % ol._off_tag(off) for off in OI_OFFS}
    ce_present = {o: c for o, c in ce_oi_cols.items() if c in chain.columns}
    pe_present = {o: c for o, c in pe_oi_cols.items() if c in chain.columns}
    ch = chain[["Datetime"] + sorted(set(list(ce_present.values()) + list(pe_present.values())))].copy()
    ce_mat = ch[[ce_present[o] for o in sorted(ce_present)]].to_numpy(dtype=float)
    pe_mat = ch[[pe_present[o] for o in sorted(pe_present)]].to_numpy(dtype=float)
    ce_offs = np.array(sorted(ce_present)); pe_offs = np.array(sorted(pe_present))
    with np.errstate(invalid="ignore"):
        ch["pcr_oi"] = np.nansum(pe_mat, 1) / np.where(np.nansum(ce_mat, 1) > 0,
                                                       np.nansum(ce_mat, 1), np.nan)
        ch["max_oi_ce_off"] = np.where(np.isnan(ce_mat).all(1), np.nan,
                                       ce_offs[np.nanargmax(np.nan_to_num(ce_mat, nan=-1), 1)])
        ch["max_oi_pe_off"] = np.where(np.isnan(pe_mat).all(1), np.nan,
                                       pe_offs[np.nanargmax(np.nan_to_num(pe_mat, nan=-1), 1)])
    f = f.merge(ch[["Datetime", "pcr_oi", "max_oi_ce_off", "max_oi_pe_off"]],
                on="Datetime", how="left")
    tot_oi = f.ce_oi + f.pe_oi
    f["oi_imbalance"] = (f.pe_oi - f.ce_oi) / tot_oi.where(tot_oi > 0)
    f["d_atm_oi_30m"] = tot_oi.pct_change(6)

    # ---- spot behaviour ----
    s = spot[["Datetime", "Open", "High", "Low", "Close"]].copy()
    s["atr14_pct"] = (_atr(s, 14) / s.Close) * 100.0
    s["ret_5m"] = s.Close.pct_change()
    s["ret_30m"] = s.Close.pct_change(6)
    s["ret_2h"] = s.Close.pct_change(24)
    s["day"] = s.Datetime.dt.date
    day_hi = s.groupby("day").High.cummax()
    day_lo = s.groupby("day").Low.cummin()
    rng = (day_hi - day_lo)
    s["day_range_pos"] = ((s.Close - day_lo) / rng.where(rng > 0)).clip(0, 1)
    prev_close = s.groupby("day").Close.last().shift(1)
    day_open = s.groupby("day").Open.first()
    gap = (day_open / prev_close - 1.0) * 100.0
    s["gap_open_pct"] = s.day.map(gap)
    rv = s.ret_5m.rolling(RV_BARS).std() * np.sqrt(252.0 * BARS_PER_DAY) * 100.0
    s["rv_20b_ann"] = rv
    # v2: classic indicator vocabulary (RSI/EMA) — same tools the live strategies use
    delta = s.Close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1.0 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    s["rsi_14"] = 100 - 100 / (1 + rs)
    s["ema20_dist_pct"] = (s.Close / s.Close.ewm(span=20, adjust=False).mean() - 1) * 100
    s["ema50_dist_pct"] = (s.Close / s.Close.ewm(span=50, adjust=False).mean() - 1) * 100
    f = f.merge(s.drop(columns=["day"]), on="Datetime", how="inner")
    f["rv_iv_ratio"] = f.rv_20b_ann / f.atm_iv

    # ---- calendar / clock ----
    edt = pd.to_datetime(f.Datetime)
    f["dow"] = edt.dt.dayofweek
    f["min_since_open"] = (edt.dt.hour * 60 + edt.dt.minute) - (9 * 60 + 15)
    f["dte_days"] = [max((bs._next_weekly_expiry(ts) - ts).total_seconds(), 0.0) / 86400.0
                     for ts in edt]
    f["is_expiry_day"] = (f.dte_days < 0.3).astype(int)
    # expiry_regime — 0 = Thursday-weekly era, 1 = Tuesday era (from 2025-09-01, NSE cir
    # 111/2025; boundary lives ONLY in expiry_calendar.py, never re-hardcoded here).
    # dow/dte seasonality may not transfer across this boundary even though dte_days
    # itself is calendar-correct — mining must check any dow-leaning rule per-regime.
    f["expiry_regime"] = np.array([int(xcal.weekly_expiry_weekday(d) == xcal.TUE)
                                   for d in edt.dt.date], dtype=int)

    # ---- liquidity PROXIES (real bid-ask never recorded — see inventory) ----
    f["opt_volume_proxy"] = f.ce_volume + f.pe_volume
    f = f.drop(columns=["ce_volume", "pe_volume", "Open", "High", "Low", "Close"])

    # final hygiene: drop warmup rows lacking core rolling features
    core = ["iv_rank_60d", "atr14_pct", "rv_iv_ratio", "pcr_oi"]
    n0 = len(f)
    f = f.dropna(subset=core).reset_index(drop=True)
    ml_gate.assert_no_lockbox(f, what="ml_features_v%d" % VERSION)
    print("feature frame: %d rows (%d dropped warmup/NaN), %d cols, %s -> %s"
          % (len(f), n0 - len(f), f.shape[1], f.Datetime.min(), f.Datetime.max()))
    return f


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flag", default="WEEK")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if os.path.exists(OUT) and not args.force:
        raise SystemExit("%s exists — bump VERSION or --force (no silent overwrite)" % OUT)
    f = build(args.flag)
    f.to_csv(OUT, index=False, compression="gzip")
    man = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else []
    man.append({"version": VERSION, "file": os.path.basename(OUT),
                "created": dt.datetime.now().isoformat(timespec="seconds"),
                "flag": args.flag, "rows": len(f), "cols": list(f.columns),
                "window": [str(f.Datetime.min()), str(f.Datetime.max())],
                "lockbox_start": str(ml_gate.LOCKBOX_START)})
    json.dump(man, open(MANIFEST, "w"), indent=1)
    print("wrote", OUT, "+ manifest entry v%d" % VERSION)


if __name__ == "__main__":
    main()
