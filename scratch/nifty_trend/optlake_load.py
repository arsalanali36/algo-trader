"""Loader for the REAL option-chain data-lake (optchain_dl.py output).

Turns the per-series CSVs (_TRADING_DATA/OptChainLake/NIFTY/<flag>/<side>_<off>.csv)
into aligned, resampled, cleaned frames the backtests read — REAL premium + IV + OI,
no Black-Scholes. Timestamps are Dhan epoch (UTC) → converted to IST-naive to match
nifty_1min.csv and the intraday_engine house rules.

Key builders:
  load_series(flag, side, off, tf)  -> DataFrame[Datetime, open..close, volume, iv, oi, strike, spot]
  atm_frame(flag, tf)               -> per-bar ATM view: ce_/pe_ {close,iv,oi}, spot, strike
  chain_frame(flag, tf, offs)       -> wide per-bar chain across offsets (for PCR / max-OI)
"""
import os
import glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
LAKE = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake", "NIFTY")

TF_RULE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min"}
IV_LO, IV_HI = 3.0, 120.0     # sane NIFTY ATM IV band (%) — drop 0s and absurd ticks


def _off_tag(off):
    return "ATM" if off == 0 else (f"ATMp{off}" if off > 0 else f"ATMm{abs(off)}")


def series_path(flag, side, off):
    return os.path.join(LAKE, flag, f"{side}_{_off_tag(off)}.csv")


def available():
    """what's on disk so far (flag -> {side -> [offsets]}) — safe to call mid-backfill."""
    out = {}
    for p in glob.glob(os.path.join(LAKE, "*", "*.csv")):
        flag = os.path.basename(os.path.dirname(p))
        name = os.path.basename(p)[:-4]
        side, tag = name.split("_", 1)
        off = 0 if tag == "ATM" else (int(tag[4:]) if tag.startswith("ATMp") else -int(tag[4:]))
        out.setdefault(flag, {}).setdefault(side, []).append(off)
    for f in out:
        for s in out[f]:
            out[f][s] = sorted(out[f][s])
    return out


def load_series(flag, side, off, tf="5m"):
    p = series_path(flag, side, off)
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p)
    if df.empty:
        return None
    df["Datetime"] = (pd.to_datetime(df["timestamp"], unit="s", utc=True)
                        .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
    df = df.drop(columns=["timestamp"]).sort_values("Datetime").drop_duplicates("Datetime")
    # clean IV outliers (0 / absurd) → NaN then ffill within day
    df.loc[(df.iv < IV_LO) | (df.iv > IV_HI), "iv"] = np.nan
    tt = df.Datetime.dt.time
    df = df[(tt >= pd.Timestamp("09:15").time()) & (tt <= pd.Timestamp("15:29").time())]
    df = df.reset_index(drop=True)
    if tf != "5m":
        s = df.set_index("Datetime")
        agg = {"open": "first", "high": "max", "low": "min", "close": "last",
               "volume": "sum", "iv": "last", "oi": "last", "strike": "last", "spot": "last"}
        df = s.resample(TF_RULE[tf], label="left", closed="left").agg(agg).dropna(subset=["close"]).reset_index()
    df["day"] = df.Datetime.dt.date
    return df


def atm_frame(flag="WEEK", tf="5m"):
    """align ATM CE + PE on Datetime → one row per bar with both sides' premium/iv/oi."""
    ce = load_series(flag, "CE", 0, tf)
    pe = load_series(flag, "PE", 0, tf)
    if ce is None or pe is None:
        return None
    ce = ce.rename(columns={c: f"ce_{c}" for c in ("open", "high", "low", "close", "volume", "iv", "oi")})
    pe = pe.rename(columns={c: f"pe_{c}" for c in ("open", "high", "low", "close", "volume", "iv", "oi")})
    m = ce.merge(pe[["Datetime", "pe_open", "pe_high", "pe_low", "pe_close", "pe_volume",
                     "pe_iv", "pe_oi"]], on="Datetime", how="inner")
    m["atm_iv"] = m[["ce_iv", "pe_iv"]].mean(axis=1)
    m["straddle"] = m["ce_close"] + m["pe_close"]
    m["day"] = m.Datetime.dt.date
    return m


def chain_frame(flag="WEEK", tf="5m", offs=range(-10, 11)):
    """wide per-bar chain: for each offset, ce/pe close+oi+iv. For PCR / max-OI / max-pain.
    Skips offsets not yet downloaded (safe mid-backfill)."""
    base = None
    for off in offs:
        for side in ("CE", "PE"):
            s = load_series(flag, side, off, tf)
            if s is None:
                continue
            tag = _off_tag(off)
            keep = s[["Datetime", "close", "oi", "iv", "strike", "spot"]].rename(
                columns={"close": f"{side}{tag}_c", "oi": f"{side}{tag}_oi",
                         "iv": f"{side}{tag}_iv", "strike": f"{side}{tag}_k"})
            if side == "CE" and off == 0:
                keep = keep.rename(columns={"spot": "spot"})
            else:
                keep = keep.drop(columns=["spot"])
            base = keep if base is None else base.merge(keep, on="Datetime", how="outer")
    if base is not None:
        base = base.sort_values("Datetime").reset_index(drop=True)
        base["day"] = base.Datetime.dt.date
    return base


def ironfly_frame(flag="WEEK", tf="5m", wing=5):
    """ATM frame + the two wings needed for an iron-fly: CE at +wing, PE at −wing.
    Adds columns CEATMp{w}_c (call-wing close) and PEATMm{w}_c (put-wing close)."""
    m = atm_frame(flag, tf)
    if m is None:
        return None
    cew = load_series(flag, "CE", +wing, tf)
    pew = load_series(flag, "PE", -wing, tf)
    if cew is None or pew is None:
        return None
    # coverage guard (TRAP #107): a still-downloading wing has only recent bars →
    # inner-join would silently truncate to that overlap and produce a BOGUS
    # (short-history) backtest. Shared helper so every inner-join site guards the
    # same way (Task 1b).
    import data_integrity
    atm_days = m.day.nunique()
    for w, name in ((cew, "CE"), (pew, "PE")):
        ok, det = data_integrity.assert_coverage(w, atm_days, min_pct=0.9,
                                                 label=f"wing {name}±{wing}")
        if not ok:
            print(f"[optlake] {det['label']}: {det['note']} — refusing partial frame", flush=True)
            return None
    cew = cew[["Datetime", "close"]].rename(columns={"close": f"CEATMp{wing}_c"})
    pew = pew[["Datetime", "close"]].rename(columns={"close": f"PEATMm{wing}_c"})
    m = m.merge(cew, on="Datetime", how="inner").merge(pew, on="Datetime", how="inner")
    m["day"] = m.Datetime.dt.date
    return m


def iv_rank_daily(flag="WEEK", tf="5m", lookback=60):
    """day -> IV-rank (0..1) of that day's OPEN ATM IV vs the trailing `lookback` days.
    High IV-rank = premium historically rich → favours selling."""
    m = atm_frame(flag, tf)
    if m is None:
        return {}
    day_open_iv = m.groupby("day").atm_iv.first().dropna()
    rank = day_open_iv.rolling(lookback, min_periods=20).apply(
        lambda w: (w.iloc[-1] >= w).mean(), raw=False)
    return {d: float(r) for d, r in rank.items() if pd.notna(r)}


if __name__ == "__main__":
    print("available on disk:", available())
    m = atm_frame("WEEK", "5m")
    if m is not None:
        print(f"ATM frame rows={len(m)}  days={m.day.nunique()}  "
              f"span {m.Datetime.min()} -> {m.Datetime.max()}")
        print(m[["Datetime", "spot", "strike", "ce_close", "pe_close", "straddle", "atm_iv",
                 "ce_oi", "pe_oi"]].tail(3).to_string())
        ivr = iv_rank_daily()
        print(f"IV-rank days: {len(ivr)}  sample:", list(ivr.items())[-3:])
