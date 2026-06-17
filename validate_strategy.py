"""
validate_strategy.py — Phase 4 validation harness.

Runs the LIVE engine logic (range_trader.run_signal_engine, copied here to
collect ALL trades instead of just the last) on historical NIFTY 5-min bars,
then compares the engine's entries/exits against a TradingView "List of Trades"
CSV. A trade is a MATCH only when entry AND exit time + side both align.

Output: per-day side-by-side table + overall % match + mismatch list.

Data: ._TRADING DATA / Index / NIFTY  (per-day 1-min CSVs)
Strategy: Range Chain (Ars_Auto_Rev_Chain) == range_trader.py
Timeframe: 5-min (1-min resampled).
"""

import argparse
import csv
import glob
import os
from datetime import time as dtime

import numpy as np
import pandas as pd

import range_trader as rt

DATA_DIR = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Index\NIFTY"
EXIT_HM  = dtime(15, 15)   # 3:15 daily square-off

# Engine config — mirror the TradingView Pine settings
CFG = {
    "max_candle_size": 25,
    "use_fresh_zone_only": True,
    "hawa_me_zone": False,
    "exit_atr": True,
    "exit_main": True,            # Pine MainExit_Toggle (Zone Exit) = true
    "max_trades_per_symbol": 4,   # Pine maxTradesPerDay
}
ATR_LEN, ATR_MULT, ZONE_AGE = 14, 2.0, 2


# ───────────────────────── data loading ─────────────────────────
def load_1m(path):
    df = pd.read_csv(path)
    df.columns = [c.lower() for c in df.columns]
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df


def resample_5m(df1):
    d = df1.set_index("datetime")
    r = pd.DataFrame({
        "open":  d["open"].resample("5min").first(),
        "high":  d["high"].resample("5min").max(),
        "low":   d["low"].resample("5min").min(),
        "close": d["close"].resample("5min").last(),
    }).dropna().reset_index().rename(columns={"datetime": "time"})
    return r


def daily_bars():
    """Aggregate per-day 1-min files into a daily OHLC frame (for key levels)."""
    rows = []
    for p in sorted(glob.glob(os.path.join(DATA_DIR, "NIFTY_2026-*.csv"))):
        df = load_1m(p)
        if df.empty:
            continue
        rows.append({
            "date":  df["datetime"].iloc[0].date(),
            "open":  float(df["open"].iloc[0]),
            "high":  float(df["high"].max()),
            "low":   float(df["low"].min()),
            "close": float(df["close"].iloc[-1]),
        })
    return pd.DataFrame(rows)


# Pine: bullish = bullEngulf or bullHarami or greenHammer
#       bearish = bearEngulf or bearHarami or invRedHam or redHammer
def _ohlc(df, i):
    r = df.iloc[i]
    return float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])


def _bull(df, i):
    if i < 1:
        return False
    o, h, l, c = _ohlc(df, i)
    po, ph, pl, pc = _ohlc(df, i - 1)
    return (rt.green_hammer(o, h, l, c)
            or rt.bull_engulfing(po, ph, pl, pc, o, h, l, c)
            or rt.bull_harami(po, ph, pl, pc, o, h, l, c))


def _bear(df, i):
    if i < 1:
        return False
    o, h, l, c = _ohlc(df, i)
    po, ph, pl, pc = _ohlc(df, i - 1)
    return (rt.red_hammer(o, h, l, c)
            or rt.inv_red_hammer(o, h, l, c)
            or rt.bear_engulfing(po, ph, pl, pc, o, h, l, c)
            or rt.bear_harami(po, ph, pl, pc, o, h, l, c))


# ───────────────────────── backtest one day ─────────────────────────
def backtest_day(df5, key_levels, cfg, atr_series=None):
    """Mirror of run_signal_engine but COLLECTS every trade. Adds 3:15 exit.
    atr_series (continuous, pre-warmed) aligned to df5 rows; else computed here.
    Returns list of dicts: {entry_time, entry_price, side, exit_time, exit_price, exit_reason}."""
    trades = []
    if df5 is None or len(df5) < 20 or not key_levels:
        return trades

    max_cs     = cfg.get("max_candle_size", 25)
    fresh_only = cfg.get("use_fresh_zone_only", True)
    exit_atr   = cfg.get("exit_atr", True)
    exit_main  = cfg.get("exit_main", True)
    max_trades = cfg.get("max_trades_per_symbol", 4)

    if atr_series is None:
        atr_series = rt.compute_atr(df5, ATR_LEN)
    else:
        atr_series = atr_series.reset_index(drop=True)

    zone_upper = zone_lower = zone_type = None
    zone_bar = -999
    tracked_high = tracked_low = None
    touch_active = False
    active_touch_type = None
    atr_sl_long = atr_sl_short = None
    position = None
    cur = None            # open trade dict
    trades_today = 0

    def _fill(i):
        """TradingView convention: a signal on bar i fills at the NEXT bar's
        open. Return (time, price) of bar i+1 (or bar i if it's the last)."""
        j = i + 1 if i + 1 < n else i
        b = df5.iloc[j]
        return b["time"], float(b["open"])

    def _open(side, i, reason):
        nonlocal cur, position
        t, price = _fill(i)
        cur = {"entry_time": t, "entry_price": price, "side": side,
               "exit_time": None, "exit_price": None, "exit_reason": None,
               "entry_reason": reason}
        position = "LONG" if side == "Long" else "Short"

    def _close(i, reason):
        nonlocal cur, position
        t, price = _fill(i)
        if cur:
            cur["exit_time"], cur["exit_price"], cur["exit_reason"] = t, price, reason
            trades.append(cur)
            cur = None
        position = None

    n = len(df5)
    for i in range(2, n):
        row = df5.iloc[i]
        t = row["time"]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        atr_val = float(atr_series.iloc[i]) if not np.isnan(atr_series.iloc[i]) else 5.0

        # 3:15 daily square-off
        if position is not None and t.time() >= EXIT_HM:
            _close(i, "3:15 Daily Exit")
            # after square-off, no new entries this loop iter
            continue

        # touch detection — Pine selectedLine: RESISTANCE priority (locks),
        # last resistance wins; else last support/CP touched
        touched_type = None
        res_locked = False
        for price, ltype in key_levels:
            if not (l <= price <= h):
                continue
            if ltype in ("RESISTANCE", "PD_H"):
                touched_type, res_locked = ltype, True
            elif ltype in ("SUPPORT", "PD_L") and not res_locked:
                touched_type = ltype
            elif ltype in ("CP", "PD_C") and not res_locked:
                touched_type = ltype
        if touched_type is not None:
            if not touch_active:
                touch_active = True
                active_touch_type = touched_type
                tracked_high, tracked_low = h, l
            else:
                active_touch_type = touched_type
                if h > (tracked_high or h): tracked_high = h
                if l < (tracked_low or l):  tracked_low = l

        # zone formation (Pine: no candle-size check here — that's an entry filter)
        bullish = _bull(df5, i)
        bearish = _bear(df5, i)
        if touch_active:
            not_red = active_touch_type not in ("RESISTANCE", "PD_H") if active_touch_type else True
            not_grn = active_touch_type not in ("SUPPORT", "PD_L") if active_touch_type else True
            if bearish and not_grn:
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "RED", i
                tracked_high = tracked_low = None
                touch_active = False
            elif bullish and not_red:
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "GREEN", i
                tracked_high = tracked_low = None
                touch_active = False

        # zone freshness (Pine maxZoneAge)
        zfresh = zone_type is not None and (i - zone_bar) <= ZONE_AGE

        # EXIT — Pine priority: ATR first, else ZONE (MainExit)
        if position == "LONG":
            if atr_sl_long is not None:
                atr_sl_long = max(atr_sl_long, c - atr_val * ATR_MULT)
            if exit_atr and atr_sl_long is not None and c < atr_sl_long:
                _close(i, "ATR_LONG")
                atr_sl_long = None
            elif exit_main and zfresh and zone_type == "RED" and c < zone_lower and c < o:
                _close(i, "ZONE_LONG")
                atr_sl_long = None
        elif position == "Short":
            if atr_sl_short is not None:
                atr_sl_short = min(atr_sl_short, c + atr_val * ATR_MULT)
            if exit_atr and atr_sl_short is not None and c > atr_sl_short:
                _close(i, "ATR_SHORT")
                atr_sl_short = None
            elif exit_main and zfresh and zone_type == "GREEN" and c > zone_upper and c > o:
                _close(i, "ZONE_SHORT")
                atr_sl_short = None

        # entries
        if trades_today >= max_trades or zone_upper is None:
            continue
        zone_fresh = (i - zone_bar) <= ZONE_AGE
        use_zone = zone_fresh if fresh_only else (zone_type is not None)
        prev = df5.iloc[i - 1]
        prev_green = float(prev["close"]) > float(prev["open"])
        prev_red   = float(prev["close"]) < float(prev["open"])
        curr_green = c > o
        curr_red   = c < o

        big_candle = (h - l) > max_cs
        long_ok = (zone_type == "GREEN" and use_zone and c > zone_upper and
                   prev_green and curr_green and
                   (tracked_high is None or c <= tracked_high) and not big_candle)
        short_ok = (zone_type == "RED" and use_zone and c < zone_lower and
                    prev_red and curr_red and
                    (tracked_low is None or c >= tracked_low) and not big_candle)

        if long_ok and position != "LONG":
            if position == "Short":
                _close(i, "Long")          # reversal exit labeled by new signal
            _open("Long", i, "ZONE")
            atr_sl_long = cur["entry_price"] - atr_val * ATR_MULT
            zone_type = zone_upper = zone_lower = None   # Pine: Green_Zone := false
            trades_today += 1
        elif short_ok and position != "Short":
            if position == "LONG":
                _close(i, "Short")
            _open("Short", i, "ZONE")
            atr_sl_short = cur["entry_price"] + atr_val * ATR_MULT
            zone_type = zone_upper = zone_lower = None   # Pine: Red_Zone := false
            trades_today += 1

    # day ended still open -> force close at last bar (safety; shouldn't happen post 15:15)
    if cur:
        _close(n - 1, "EOD")
    return trades


# ───────────────────────── TV parsing ─────────────────────────
def parse_tv(csv_path):
    """Group TV List-of-Trades rows into trades keyed by 'Trade number'."""
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    byid = {}
    for r in rows:
        byid.setdefault(r["Trade number"], []).append(r)
    trades = []
    for _tid, rs in byid.items():
        entry = next((x for x in rs if x["Type"].startswith("Entry")), None)
        exit_ = next((x for x in rs if x["Type"].startswith("Exit")), None)
        if not entry or not exit_:
            continue
        side = "Long" if "long" in entry["Type"] else "Short"
        trades.append({
            "entry_time": pd.to_datetime(entry["Date and time"]),
            "entry_price": float(entry["Price"]),
            "side": side,
            "exit_time": pd.to_datetime(exit_["Date and time"]),
            "exit_price": float(exit_["Price"]),
            "exit_reason": exit_["Signal"],
        })
    trades.sort(key=lambda x: x["entry_time"])
    return trades


# ───────────────────────── run + compare ─────────────────────────
def run(tv_csv, date_from=None, date_to=None):
    tv = parse_tv(tv_csv)
    if date_from:
        tv = [t for t in tv if t["entry_time"] >= pd.to_datetime(date_from)]
    if date_to:
        tv = [t for t in tv if t["entry_time"] <= pd.to_datetime(date_to) + pd.Timedelta(days=1)]

    daily = daily_bars()
    days = sorted({t["entry_time"].date() for t in tv})

    # Build ONE continuous 5-min series across ALL available days so ATR(14)
    # is warmed/continuous like TradingView (not reset each day).
    all_paths = sorted(glob.glob(os.path.join(DATA_DIR, "NIFTY_2026-*.csv")))
    frames = []
    for p in all_paths:
        d5 = resample_5m(load_1m(p))
        d5["date"] = d5["time"].dt.date
        frames.append(d5)
    cont = pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)
    atr_all = rt.compute_atr(cont, ATR_LEN)

    eng = []
    for d in days:
        mask = cont["date"] == d
        if not mask.any():
            continue
        df5 = cont[mask].reset_index(drop=True)
        atr_slice = atr_all[mask].reset_index(drop=True)
        # daily frame ending at this day (build_key_levels uses iloc[-2]=prev day)
        sub = daily[daily["date"] <= d].reset_index(drop=True)
        if len(sub) < 2:
            continue
        levels = rt.build_key_levels(sub, is_index=True)
        eng += backtest_day(df5, levels, CFG, atr_slice)

    # match per day, in entry-time order
    def fmt(t): return t.strftime("%Y-%m-%d %H:%M") if t is not None else "-"
    matched = 0
    report = []
    eng_by_day = {}
    for e in eng:
        eng_by_day.setdefault(e["entry_time"].date(), []).append(e)
    tv_by_day = {}
    for t in tv:
        tv_by_day.setdefault(t["entry_time"].date(), []).append(t)

    for d in days:
        tlist = tv_by_day.get(d, [])
        elist = eng_by_day.get(d, [])
        used = set()
        for tv_t in tlist:
            hit = None
            for j, e in enumerate(elist):
                if j in used:
                    continue
                if (e["entry_time"] == tv_t["entry_time"] and e["side"] == tv_t["side"]
                        and e["exit_time"] == tv_t["exit_time"]):
                    hit = j
                    break
            if hit is not None:
                used.add(hit)
                matched += 1
                report.append(("MATCH", tv_t, elist[hit]))
            else:
                # find closest engine entry same side for diagnostics
                cand = [e for k, e in enumerate(elist) if k not in used and e["side"] == tv_t["side"]]
                report.append(("MISS", tv_t, cand[0] if cand else None))

    total = len(tv)
    pct = 100.0 * matched / total if total else 0.0
    print(f"\n{'='*78}")
    print(f"VALIDATION: {fmt(tv[0]['entry_time']) if tv else '-'}  ..  "
          f"{fmt(tv[-1]['entry_time']) if tv else '-'}")
    print(f"TV trades: {total} | Engine trades: {len(eng)} | "
          f"MATCHED (entry+exit+side): {matched}")
    print(f"SCORE: {pct:.1f}%")
    print(f"{'='*78}")
    print(f"{'res':5} {'TV entry':16} {'side':5} {'TV exit':16} {'TVrsn':14} | "
          f"{'ENG entry':16} {'ENG exit':16} {'ENGrsn':10}")
    for res, tvt, e in report:
        eng_entry = fmt(e["entry_time"]) if e else "-"
        eng_exit  = fmt(e["exit_time"]) if e else "-"
        eng_rsn   = e["exit_reason"] if e else "-"
        print(f"{res:5} {fmt(tvt['entry_time']):16} {tvt['side']:5} "
              f"{fmt(tvt['exit_time']):16} {tvt['exit_reason']:14} | "
              f"{eng_entry:16} {eng_exit:16} {eng_rsn:10}")
    return pct


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    args = ap.parse_args()
    run(args.csv, args.dfrom, args.dto)
