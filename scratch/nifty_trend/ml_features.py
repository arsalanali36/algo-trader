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
VERSION = 3   # v3 (2026-07-14, Task 5a): + expiry_regime (0=Thu-era, 1=Tue-era from
              # 2025-09-01, expiry_calendar.py verified schedule) + the Task-5 user-seeded
              # family from the day's FIRST FIVE 1-min candles (fib retracement of candle-1
              # range, confirm-candle state at candles 2/3/5, ATM CE/PE premium-divergence
              # classes, OI-buildup 4-quadrant at the nearest-fib strike). All candle-1..5
              # features are FINAL at 09:20:00 and broadcast ONLY to 5m bars labeled >=09:20
              # (the 09:15 bar closes at the same instant the info completes — excluded, so
              # earliest entry = 09:20 bar's close = 09:25, strictly after the signal).
              # PRE-OPEN GAP (Task 5a #5) VERIFIED: gap_open_pct = prev-day close vs the
              # 09:15 bar OPEN (first tick). NSE's 09:08-09:12 pre-open indicative price is
              # NOT in any data source here (spot 1m starts exactly 09:15 across 2018-2026;
              # lake is market-hours only) -> no auction-gap column possible, coverage gap
              # documented, gap_open_pct stays the gap feature.
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


FIB_R = (0.236, 0.382, 0.5, 0.618, 0.786)
CONFIRM_K = (2, 3, 5)          # 2nd / 3rd / 5th 1-min candle of the day
STRIKE_STEP = 50


def _sign(x, eps=1e-9):
    return 0 if (x is None or not np.isfinite(x) or abs(x) <= eps) else (1 if x > 0 else -1)


def _fib_day_features(flag="WEEK"):
    """Task 5a — one row PER DAY from the day's first five 1-min candles.

    Candle-1 (09:15) high/low range -> fib retracement levels (from-high AND from-low —
    note from-high@r == from-low@(1-r), so the 10 levels are 2 mirrored views of the same
    ladder; the CONTINUOUS `fibpos_k` = (close_k - low1)/range encodes every crossing).
    Confirm candles k=2/3/5: fib position + continuation/reversal vs candle-1's own body
    direction.

    ⚠️ DATA-AVAILABILITY ADAPTATION (verified 2026-07-14): the option-chain lake is
    5-MINUTE bars end-to-end (300s spacing 2021-2026; a "1m" resample returns the same
    points) — per-1-min-candle premium windows DO NOT EXIST. Honest mapping:
      * pm_class (ONE per day, not per-k): the 09:15 bar's own OPEN->CLOSE on the raw
        CE_ATM/PE_ATM series = premium move over EXACTLY candles 1-5 (09:15->09:20),
        same contract inside the bar (each row carries its own strike — no drift).
        Spot move for the same window = candle-5 close - candle-1 open (1-min spot).
        FINAL at 09:20:00.
      * oi_bld_k: OI is one snapshot per 5m bar -> earliest intraday OI change =
        oi(09:20-bar) - oi(09:15-bar) at the strike nearest the fib level nearest
        candle-k's close (strike varies per k; window doesn't). Direction = chain spot
        move over the same two bars. FINAL at 09:25:00 -> caller broadcasts oi_bld_*
        one 5m bar later than the rest."""
    # ---- spot: first 5 one-minute candles per day ----
    sp = pd.read_csv(os.path.join(HERE, "nifty_1min.csv"), parse_dates=["Datetime"])
    sp = ml_gate.trim_lockbox(sp)
    sp["day"] = sp.Datetime.dt.date
    sp["t"] = sp.Datetime.dt.time
    first5 = sp[(sp.t >= dt.time(9, 15)) & (sp.t <= dt.time(9, 19))]

    # ---- raw ATM series (per-bar open/close): premium move INSIDE the 09:15 bar ----
    def _first_bar(side):
        p = os.path.join(ol.LAKE, flag, "%s_ATM.csv" % side)
        df = pd.read_csv(p, usecols=["timestamp", "open", "close"])
        ts = (pd.to_datetime(df.timestamp, unit="s", utc=True)
                .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
        df["day"] = ts.dt.date
        df = df[ts.dt.time == dt.time(9, 15)]
        df = df[[d < ml_gate.LOCKBOX_START for d in df.day]]        # belt+braces
        return {r.day: (float(r.open), float(r.close)) for r in df.itertuples()}
    ce_bar0 = _first_bar("CE")
    pe_bar0 = _first_bar("PE")

    # ---- chain 5m (OI ladder ±10): first two bars per day for the OI-buildup window ----
    ch1 = ol.chain_frame(flag, "5m", offs=OI_OFFS)
    ml_gate.assert_no_lockbox(ch1, what="chain_5m(task5)")
    ch1 = ch1.copy()
    ch1["day"] = pd.to_datetime(ch1.Datetime).dt.date
    ch1["t"] = pd.to_datetime(ch1.Datetime).dt.time
    ch1 = ch1[(ch1.t >= dt.time(9, 15)) & (ch1.t <= dt.time(9, 20))]
    offs = list(OI_OFFS)

    def chain_oi(row, K):
        atmk = row.get("CEATM_k", np.nan)
        if row is None or not np.isfinite(atmk):
            return np.nan
        off = int(round((K - atmk) / STRIKE_STEP))
        if off not in offs:
            return np.nan
        tot, any_ = 0.0, False
        for side in ("CE", "PE"):
            col = "%s%s_oi" % (side, ol._off_tag(off))
            v = row.get(col, np.nan)
            if col in row.index and np.isfinite(v):
                tot += v; any_ = True
        return tot if any_ else np.nan

    rows = []
    lake_by_day = {d: g.sort_values("Datetime").reset_index(drop=True)
                   for d, g in ch1.groupby("day")}
    for d, g in first5.groupby("day"):
        g = g.sort_values("Datetime").reset_index(drop=True)
        if len(g) < 5 or g.t.iloc[0] != dt.time(9, 15):
            continue                                   # partial open — skip day (honest)
        o1, h1, l1, c1 = float(g.Open[0]), float(g.High[0]), float(g.Low[0]), float(g.Close[0])
        rng = h1 - l1
        if rng <= 0:
            continue
        rec = {"day": d, "c1_dir": _sign(c1 - o1), "c1_range_pct": rng / c1 * 100.0}
        for r in FIB_R:                                # absolute levels (stored, NOT mined)
            rec["fibp_h_%d" % int(r * 1000)] = h1 - r * rng   # retracement from high
            rec["fibp_l_%d" % int(r * 1000)] = l1 + r * rng   # retracement from low
        # premium-behavior class over candles 1-5 (09:15 bar OPEN->CLOSE, same contract)
        pm = 0
        if d in ce_bar0 and d in pe_bar0:
            d_ce = ce_bar0[d][1] - ce_bar0[d][0]
            d_pe = pe_bar0[d][1] - pe_bar0[d][0]
            d_sp = float(g.Close[4]) - o1              # candle-1 open -> candle-5 close
            sce, spe, ssp = _sign(d_ce), _sign(d_pe), _sign(d_sp)
            if sce == 1 and spe == 1:      pm = 5      # co_expanding (IV-driven)
            elif sce == -1 and spe == -1:  pm = 6      # co_contracting
            elif ssp == 1 and sce == 1:    pm = 1      # index_up_ce_up
            elif ssp == 1 and sce == -1:   pm = 2      # index_up_ce_down (divergence)
            elif ssp == -1 and spe == 1:   pm = 3      # index_down_pe_up
            elif ssp == -1 and spe == -1:  pm = 4      # index_down_pe_down (divergence)
        rec["pm_class"] = pm
        lk = lake_by_day.get(d)
        lk0 = lk.iloc[0] if (lk is not None and len(lk) >= 2) else None
        lk1 = lk.iloc[1] if (lk is not None and len(lk) >= 2) else None
        d_sp2 = ((float(lk1["spot"]) - float(lk0["spot"]))
                 if (lk0 is not None and np.isfinite(lk0.get("spot", np.nan))
                     and np.isfinite(lk1.get("spot", np.nan))) else np.nan)
        for k in CONFIRM_K:
            ck = float(g.Close[k - 1])
            rec["fibpos_%d" % k] = (ck - l1) / rng
            sk = _sign(ck - c1)
            rec["conf_%d" % k] = 0 if (sk == 0 or rec["c1_dir"] == 0) else (1 if sk == rec["c1_dir"] else -1)
            # OI-buildup 4-quadrant at the strike nearest the fib level nearest candle-k close
            ob = 0
            if lk0 is not None and np.isfinite(d_sp2):
                levels = [h1 - r * rng for r in FIB_R] + [l1 + r * rng for r in FIB_R]
                L = min(levels, key=lambda x: abs(x - ck))
                KL = round(L / STRIKE_STEP) * STRIKE_STEP
                oi_a, oi_b = chain_oi(lk0, KL), chain_oi(lk1, KL)
                if np.isfinite(oi_a) and np.isfinite(oi_b):
                    sp_, oi_ = _sign(d_sp2), _sign(oi_b - oi_a)
                    if sp_ == 1 and oi_ == 1:    ob = 1   # long buildup
                    elif sp_ == -1 and oi_ == 1: ob = 2   # short buildup
                    elif sp_ == 1 and oi_ == -1: ob = 3   # short covering
                    elif sp_ == -1 and oi_ == -1: ob = 4  # long unwinding
            rec["oi_bld_%d" % k] = ob
        rows.append(rec)
    out = pd.DataFrame(rows)
    print("task5 day-features: %d days (fib/conf from 1m spot; pm from 09:15 option bar "
          "open->close; OI from 5m chain bars 0->1)" % len(out))
    return out


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

    # ---- Task 5a: fib / confirm-candle / premium-divergence / OI-buildup (per-day) ----
    # Availability masks: fib/conf/pm complete at 09:20:00 -> bars labeled >= 09:20 (the
    # 09:15 bar closes at that same instant = treated as unavailable). oi_bld_* needs the
    # 09:20-bar's OI snapshot (final 09:25:00) -> bars labeled >= 09:25 only.
    fib = _fib_day_features(flag)
    tt_ = pd.to_datetime(f.Datetime).dt.time
    late = tt_ >= dt.time(9, 20)
    later = tt_ >= dt.time(9, 25)
    for col in fib.columns:
        if col == "day":
            continue
        f[col] = f.day.map(fib.set_index("day")[col]).where(
            later if col.startswith("oi_bld_") else late)

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
