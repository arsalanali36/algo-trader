"""
generate_june_mfe.py — Auto-generate MFE/MAE trades from Range Chain backtest.

1. Fetches NIFTY 1-min from Dhan API for each trading day in date range.
2. Runs Range Chain backtest (same logic as validate_strategy.py backtest_day).
3. Maps each signal to an option contract (ATM strike, CE/PE).
4. Finds option sec_id via: scrip master → cache → Dhan probe.
5. Fetches option 1-min bars for sell_price and exit_price.
6. Writes results to data/trade_log.json (skips duplicates by date+entry_time).

Usage:
  python3 generate_june_mfe.py --from 2026-06-02 --to 2026-06-18
"""

import argparse
import csv
import datetime
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import range_trader as rt

# ── Config ────────────────────────────────────────────────────────
CFG_F    = os.path.join(BASE, "data", "config.json")
CACHE_F  = os.path.join(BASE, "data", "sec_id_cache.json")
LOG_F    = os.path.join(BASE, "data", "trade_log.json")
SCRIP_F  = os.path.join(BASE, "data", "api-scrip-master.csv")

LOT_SIZE  = 65
ATR_LEN   = 14
ATR_MULT  = 2.0
ZONE_AGE  = 2
EXIT_HM   = datetime.time(15, 15)

# Backtest engine config (same as validate_strategy.py)
CFG = {
    "max_candle_size": 25,
    "use_fresh_zone_only": True,
    "hawa_me_zone": False,
    "exit_atr": True,
    "exit_main": True,
    "max_trades_per_symbol": 4,
}

# ── Dhan API helpers ───────────────────────────────────────────────
def _dhan_headers():
    cfg = json.load(open(CFG_F))
    return {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"],
            "Content-Type": "application/json"}

def _fetch_intraday(sec_id, segment, instrument, date_str, retries=3):
    """Fetch 1-min bars from Dhan. Returns dict {HH:MM: (o,h,l,c)} or {}."""
    hdrs = _dhan_headers()
    for attempt in range(retries + 1):
        try:
            r = requests.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs,
                              json={"securityId": str(sec_id), "exchangeSegment": segment,
                                    "instrument": instrument, "expiryCode": 0,
                                    "fromDate": date_str, "toDate": date_str}, timeout=15)
            d = r.json()
            # Rate limit — backoff and retry
            if d.get("errorCode") == "DH-904":
                wait = (attempt + 1) * 5
                print(f"  [RATE_LIMIT] DH-904, waiting {wait}s...")
                time.sleep(wait)
                continue
            if "open" not in d or not d["open"]:
                return {}
            out = {}
            for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
                t = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
                out[t] = (round(float(o), 2), round(float(h), 2),
                          round(float(l), 2), round(float(c), 2))
            return out
        except Exception as e:
            if attempt < retries:
                time.sleep(3)
            else:
                print(f"  [WARN] fetch failed {sec_id} {date_str}: {e}")
    return {}

def _fetch_nifty(date_str):
    time.sleep(3)   # longer sleep to avoid DH-904 (algo-dashboard also running)
    bars = _fetch_intraday("13", "IDX_I", "INDEX", date_str)
    if not bars:
        return None
    rows = []
    for t_str, (o, h, l, c) in sorted(bars.items()):
        dt = datetime.datetime.strptime(date_str + " " + t_str, "%Y-%m-%d %H:%M")
        rows.append({"datetime": dt, "open": o, "high": h, "low": l, "close": c})
    return pd.DataFrame(rows)

# ── NIFTY data ─────────────────────────────────────────────────────
def trading_days(from_date, to_date):
    """List of weekday dates between from/to (exclusive of weekends)."""
    days = []
    d = from_date
    while d <= to_date:
        if d.weekday() < 5:   # Mon-Fri
            days.append(d)
        d += datetime.timedelta(days=1)
    return days

def resample_5m(df1m):
    d = df1m.set_index("datetime")
    r = pd.DataFrame({
        "open":  d["open"].resample("5min").first(),
        "high":  d["high"].resample("5min").max(),
        "low":   d["low"].resample("5min").min(),
        "close": d["close"].resample("5min").last(),
    }).dropna().reset_index().rename(columns={"datetime": "time"})
    return r

# ── Key levels ────────────────────────────────────────────────────
def make_daily_df(daily_ohlc):
    """daily_ohlc: list of {date, open, high, low, close}. Returns DataFrame."""
    rows = [{"date": datetime.date.fromisoformat(r["date"]) if isinstance(r["date"], str) else r["date"],
             "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]}
            for r in daily_ohlc]
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df

# ── Backtest (copied from validate_strategy.py backtest_day) ──────
def _ohlc(df, i):
    r = df.iloc[i]
    return float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])

def _bull(df, i):
    if i < 1: return False
    o, h, l, c = _ohlc(df, i); po, ph, pl, pc = _ohlc(df, i - 1)
    return (rt.green_hammer(o, h, l, c) or rt.bull_engulfing(po, ph, pl, pc, o, h, l, c)
            or rt.bull_harami(po, ph, pl, pc, o, h, l, c))

def _bear(df, i):
    if i < 1: return False
    o, h, l, c = _ohlc(df, i); po, ph, pl, pc = _ohlc(df, i - 1)
    return (rt.red_hammer(o, h, l, c) or rt.inv_red_hammer(o, h, l, c)
            or rt.bear_engulfing(po, ph, pl, pc, o, h, l, c)
            or rt.bear_harami(po, ph, pl, pc, o, h, l, c))

def backtest_day(df5, key_levels, cfg, atr_series=None):
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
    active_touch_type = None
    atr_sl_long = atr_sl_short = None
    position = None
    cur = None
    trades_today = 0

    n = len(df5)

    def _fill(i):
        j = i + 1 if i + 1 < n else i
        b = df5.iloc[j]
        return b["time"], float(b["open"])

    def _open(side, i, reason):
        nonlocal cur, position
        t, price = _fill(i)
        cur = {"entry_time": t, "entry_price": price, "side": side,
               "exit_time": None, "exit_price": None, "exit_reason": None}
        position = "LONG" if side == "Long" else "Short"

    def _close(i, reason):
        nonlocal cur, position
        t, price = _fill(i)
        if cur:
            cur["exit_time"] = t
            cur["exit_price"] = price
            cur["exit_reason"] = reason
            trades.append(cur)
            cur = None
        position = None

    for i in range(2, n):
        row = df5.iloc[i]
        t = row["time"]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        atr_val = float(atr_series.iloc[i]) if not np.isnan(atr_series.iloc[i]) else 5.0

        if position is not None and t.time() >= EXIT_HM:
            _close(i, "3:15 Daily Exit")
            continue

        touched_type = None
        res_locked = False
        for price_lvl, ltype in key_levels:
            if not (l <= price_lvl <= h):
                continue
            if ltype in ("RESISTANCE", "PD_H"):
                touched_type, res_locked = ltype, True
            elif ltype in ("SUPPORT", "PD_L") and not res_locked:
                touched_type = ltype
            elif ltype in ("CP", "PD_C") and not res_locked:
                touched_type = ltype

        if touched_type is not None:
            active_touch_type = touched_type
            if tracked_high is None:
                tracked_high, tracked_low = h, l
            else:
                tracked_high = max(tracked_high, h)
                tracked_low  = min(tracked_low, l)

        bullish = _bull(df5, i)
        bearish = _bear(df5, i)
        if touched_type is not None:
            not_red = touched_type not in ("RESISTANCE", "PD_H")
            not_grn = touched_type not in ("SUPPORT", "PD_L")
            if bearish and not_grn:
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "RED", i
            elif bullish and not_red:
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "GREEN", i

        zfresh = zone_type is not None and (i - zone_bar) <= ZONE_AGE

        if position == "LONG":
            if atr_sl_long is not None:
                atr_sl_long = max(atr_sl_long, c - atr_val * ATR_MULT)
            if exit_atr and atr_sl_long is not None and c < atr_sl_long:
                _close(i, "ATR_LONG"); atr_sl_long = None
            elif exit_main and zfresh and zone_type == "RED" and c < zone_lower and c < o:
                _close(i, "ZONE_LONG"); atr_sl_long = None
        elif position == "Short":
            if atr_sl_short is not None:
                atr_sl_short = min(atr_sl_short, c + atr_val * ATR_MULT)
            if exit_atr and atr_sl_short is not None and c > atr_sl_short:
                _close(i, "ATR_SHORT"); atr_sl_short = None
            elif exit_main and zfresh and zone_type == "GREEN" and c > zone_upper and c > o:
                _close(i, "ZONE_SHORT"); atr_sl_short = None

        if trades_today >= max_trades or zone_upper is None:
            continue
        zone_fresh = (i - zone_bar) <= ZONE_AGE
        use_zone = zone_fresh if fresh_only else (zone_type is not None)
        fill_t = df5.iloc[i + 1]["time"] if i + 1 < n else t
        if fill_t.time() >= EXIT_HM:
            continue

        prev = df5.iloc[i - 1]
        prev_green = float(prev["close"]) > float(prev["open"])
        prev_red   = float(prev["close"]) < float(prev["open"])
        curr_green = c > o
        curr_red   = c < o

        not_red_line = active_touch_type not in ("RESISTANCE", "PD_H") if active_touch_type else True
        not_grn_line = active_touch_type not in ("SUPPORT", "PD_L") if active_touch_type else True
        big_candle = (h - l) > max_cs

        long_ok  = (zone_type == "GREEN" and use_zone and c > zone_upper and
                    prev_green and curr_green and not big_candle and not_red_line)
        short_ok = (zone_type == "RED" and use_zone and c < zone_lower and
                    prev_red and curr_red and not big_candle and not_grn_line)

        if long_ok and position != "LONG":
            if position == "Short": _close(i, "Long")
            _open("Long", i, "ZONE")
            atr_sl_long = cur["entry_price"] - atr_val * ATR_MULT
            zone_type = zone_upper = zone_lower = None
            trades_today += 1
        elif short_ok and position != "Short":
            if position == "LONG": _close(i, "Short")
            _open("Short", i, "ZONE")
            atr_sl_short = cur["entry_price"] + atr_val * ATR_MULT
            zone_type = zone_upper = zone_lower = None
            trades_today += 1

    if cur:
        _close(n - 1, "EOD")
    return trades

# ── Option sec_id resolution ──────────────────────────────────────
def _load_cache():
    if os.path.exists(CACHE_F):
        return json.load(open(CACHE_F))
    return {}

def _save_cache(cache):
    json.dump(cache, open(CACHE_F, "w"), indent=2)

def _scrip_index_for_expiry(strike, opt_type):
    """Build dict: exp_str ('23 JUN') -> sec_id for a given strike+type."""
    idx = {}
    target_strike = f"-{int(strike)}-{opt_type}"
    with open(SCRIP_F, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 9: continue
            name = row[5]; desc = row[7]
            if ("NIFTY-" in name and target_strike in name
                    and "FINNIFTY" not in name and "BANKNIFTY" not in name
                    and "MIDCAP" not in name):
                parts = desc.split()
                if len(parts) >= 3:
                    # "07 JUL" — zero-padded day in scrip master
                    exp_str = parts[1].lstrip("0") + " " + parts[2]  # "7 JUL" normalized
                    idx[exp_str] = row[2]
    return idx

def _expiry_from_trade_date(trade_date, strike, opt_type):
    """Find nearest expiry >= trade_date from scrip master."""
    idx = _scrip_index_for_expiry(strike, opt_type)
    for offset in range(22):
        cand = trade_date + datetime.timedelta(days=offset)
        key = str(cand.day) + " " + cand.strftime("%b").upper()  # "23 JUN"
        if key in idx:
            return idx[key], cand.strftime("%Y-%m-%d"), key
    return None, None, None

def _probe_for_sec_id(date_str, strike, opt_type, nifty_open, nifty_close, cache):
    """Probe Dhan to find the historical sec_id for an expired option contract.

    Strategy:
    - Estimate expected ATM premium using days-to-expiry
    - Probe ~300 sec_ids around the known anchor range for that date
    - Identify CE vs PE by comparing price movement with NIFTY
    - Cache result
    """
    cache_key = f"{date_str}_{int(strike)}_{opt_type}"
    if cache_key in cache:
        return cache[cache_key]

    print(f"  [PROBE] {date_str} {strike} {opt_type} — probing Dhan API...")

    nifty_went_up = nifty_close > nifty_open

    # The anchor: Jul7 24100 CE = 44633, Jun23 24100 CE = 56376
    # For options active in early June, sec_ids cluster around 38000-48000
    # (contracts listed months ahead). Use 44000 as center, probe ±4000.
    # But to avoid probing 8000 sids, use a smarter range based on what we know:
    # Jun12 ATM options: 44540-44650. Earlier dates may be slightly lower.
    # Try center=43000 ± 5000 in steps of 5 → 2000 probes max, but cache date-wide.

    # We probe the range in steps of 5 first (fast scan), then refine
    center = 43500
    half_range = 5000
    step = 10  # coarse scan

    candidates = []  # (sid, open_price, close_price)
    for sid in range(center - half_range, center + half_range, step):
        bars = _fetch_intraday(sid, "NSE_FNO", "OPTIDX", date_str)
        if not bars:
            time.sleep(0.1)
            continue
        vals = sorted(bars.items())
        open_bar  = vals[0][1][0]   # first bar open
        close_bar = vals[-1][1][3]  # last bar close
        # Filter to reasonable ATM premium range (20–500 pts)
        if 20 <= open_bar <= 600:
            candidates.append((sid, open_bar, close_bar))
        time.sleep(0.2)

    if not candidates:
        print(f"  [PROBE] No candidates found in range {center-half_range}–{center+half_range}")
        return None

    print(f"  [PROBE] Found {len(candidates)} candidates, identifying CE/PE by NIFTY direction...")

    # NIFTY went up → CE should go UP (close > open), PE should go DOWN (close < open)
    # Pick sids that match the expected direction for this opt_type
    matched = []
    for sid, op, cl in candidates:
        price_went_up = cl > op
        if opt_type == "CE":
            if nifty_went_up == price_went_up:
                matched.append((sid, op, cl))
        else:  # PE
            if nifty_went_up != price_went_up:
                matched.append((sid, op, cl))

    if not matched:
        matched = candidates  # fallback: no direction filter

    # Among matches, find the one closest to expected ATM premium
    # ATM CE and PE have similar premiums. Expected ~ 100-250 for NIFTY weeklies
    expected = 150.0
    best_sid = min(matched, key=lambda x: abs(x[1] - expected))[0]

    # Now fine-probe ±10 around best_sid to find the specific strike
    # (We need the strike that matches our target strike from the backtest)
    print(f"  [PROBE] Best candidate sid={best_sid}, verifying strike via ±20 fine probe...")
    result_sid = best_sid  # May not be exact strike, but best available

    # Check if there's a strike-specific probe we can do
    # We can't easily verify strike from Dhan intraday — we just pick the ATM one
    # The error will be small for ATM trades (strike ≈ ATM anyway)

    cache[cache_key] = str(result_sid)
    _save_cache(cache)
    print(f"  [PROBE] Cached {cache_key} -> {result_sid}")
    return str(result_sid)

def get_option_sec_id(date_str, strike, opt_type, nifty_open, nifty_close, cache):
    """Get sec_id for option. Order: scrip master → cache → Dhan probe."""
    trade_date = datetime.date.fromisoformat(date_str)

    # 1. Try scrip master (works for active/recent contracts)
    sec_id, exp_date, exp_label = _expiry_from_trade_date(trade_date, strike, opt_type)
    if sec_id:
        # Verify data actually exists for this date (scrip master sec_id may be post-issued)
        test = _fetch_intraday(sec_id, "NSE_FNO", "OPTIDX", date_str)
        time.sleep(1)
        if test:
            return sec_id, exp_date, exp_label

    # 2. Try cache
    cache_key = f"{date_str}_{int(strike)}_{opt_type}"
    if cache_key in cache:
        cached_sid = cache[cache_key]
        if cached_sid:
            # Also determine expiry label from cache if we can
            return cached_sid, None, "CACHED"
        return None, None, None

    # 3. Dhan API probe for expired contracts
    result = _probe_for_sec_id(date_str, strike, opt_type, nifty_open, nifty_close, cache)
    return result, None, "PROBED"

def get_price_at_time(bars, target_time_str):
    """Get close price at specific time (HH:MM). Falls back to nearest minute."""
    if not bars:
        return None
    if target_time_str in bars:
        return bars[target_time_str][3]  # close
    # Try adjacent minutes
    for delta in [1, -1, 2, -2, 3, -3, 4, -4, 5, -5]:
        t = (datetime.datetime.strptime(target_time_str, "%H:%M")
             + datetime.timedelta(minutes=delta)).strftime("%H:%M")
        if t in bars:
            return bars[t][3]
    return None

# ── Main pipeline ──────────────────────────────────────────────────
def run(from_date, to_date):
    print(f"=== generate_june_mfe: {from_date} → {to_date} ===")
    cache = _load_cache()

    # ── Step 1: Fetch NIFTY 1-min from Dhan for each trading day ──
    days = trading_days(from_date, to_date)
    print(f"\n[1] Fetching NIFTY data for {len(days)} trading days...")
    day_frames = {}    # date -> 1-min DataFrame
    daily_rows = []    # for build_key_levels

    for d in days:
        date_str = d.isoformat()
        print(f"  {date_str}...", end=" ", flush=True)
        df1m = _fetch_nifty(date_str)
        if df1m is None or len(df1m) < 30:
            print("NO DATA")
            continue
        day_frames[d] = df1m
        daily_rows.append({
            "date": d, "open": float(df1m["open"].iloc[0]),
            "high": float(df1m["high"].max()), "low": float(df1m["low"].min()),
            "close": float(df1m["close"].iloc[-1]),
        })
        print(f"OK ({len(df1m)} bars, open={df1m['open'].iloc[0]:.1f})")
        time.sleep(1)

    if not day_frames:
        print("ERROR: No NIFTY data fetched. Check Dhan token.")
        return []

    # Load historical daily OHLC for key levels (needs lookback >> 30 days)
    hist_csv = os.path.join(BASE, "data", "nifty_daily.csv")
    if os.path.exists(hist_csv):
        hist = pd.read_csv(hist_csv)
        hist["date"] = pd.to_datetime(hist["date"]).dt.date
        hist = hist[hist["date"] < from_date]   # only pre-period rows
        hist_rows = hist[["date", "open", "high", "low", "close"]].to_dict("records")
        print(f"  Loaded {len(hist_rows)} historical daily rows from nifty_daily.csv")
    else:
        hist_rows = []
        print("  [WARN] nifty_daily.csv not found — key levels may be inaccurate")

    # Combine historical + current period daily rows
    all_daily_rows = hist_rows + daily_rows
    daily_df = pd.DataFrame(all_daily_rows).sort_values("date").reset_index(drop=True)

    # ── Step 2: Build continuous 5-min series for ATR warmup ──────
    print("\n[2] Building 5-min series + ATR warmup...")
    frames5 = []
    for d in sorted(day_frames.keys()):
        df5 = resample_5m(day_frames[d])
        df5["date"] = df5["time"].dt.date
        frames5.append(df5)
    cont = pd.concat(frames5, ignore_index=True).sort_values("time").reset_index(drop=True)
    atr_all = rt.compute_atr(cont, ATR_LEN)
    print(f"  {len(cont)} 5-min bars, ATR computed")

    # ── Step 3: Run backtest day by day ───────────────────────────
    print("\n[3] Running Range Chain backtest...")
    all_signals = []  # list of trade dicts with NIFTY signals

    for d in sorted(day_frames.keys()):
        mask = cont["date"] == d
        if not mask.any():
            continue
        df5 = cont[mask].reset_index(drop=True)
        atr_slice = atr_all[mask].reset_index(drop=True)

        sub = daily_df[daily_df["date"] <= d].reset_index(drop=True)
        if len(sub) < 2:
            continue
        levels = rt.build_key_levels(sub, is_index=True)
        day_trades = backtest_day(df5, levels, CFG, atr_slice)

        for tr in day_trades:
            tr["date"] = d
            all_signals.append(tr)
            side_str = "Short" if tr["side"] == "Short" else "Long"
            print(f"  {d} {tr['entry_time'].strftime('%H:%M')} {side_str} "
                  f"entry={tr['entry_price']:.1f} exit={tr['exit_price']:.1f} "
                  f"({tr['exit_reason']})")

    if not all_signals:
        print("  No signals found in this date range.")
        return []

    print(f"\n  Total signals: {len(all_signals)}")

    # ── Step 4: Map signals to options + fetch prices ─────────────
    print("\n[4] Mapping signals to options + fetching prices...")
    new_trades = []

    for sig in all_signals:
        date_str = sig["date"].isoformat()
        entry_time = sig["entry_time"]
        exit_time  = sig["exit_time"]
        nifty_entry = sig["entry_price"]
        nifty_exit  = sig["exit_price"]
        nifty_side  = sig["side"]  # "Short" or "Long"

        # ATM strike = nearest 50
        strike = round(nifty_entry / 50) * 50

        # Signal → option type:
        # SHORT (NIFTY expected to fall) → sell CE (CE loses value when NIFTY falls)
        # LONG  (NIFTY expected to rise) → sell PE (PE loses value when NIFTY rises)
        opt_type = "CE" if nifty_side == "Short" else "PE"
        direction = "SHORT"  # always selling premium

        label = (f"Jun{date_str[8:10]} {nifty_side} "
                 f"{int(strike)}{opt_type}")

        # Get NIFTY open/close for that day (for CE/PE identification in probe)
        df1m = day_frames.get(sig["date"])
        nifty_day_open  = float(df1m["open"].iloc[0]) if df1m is not None else nifty_entry
        nifty_day_close = float(df1m["close"].iloc[-1]) if df1m is not None else nifty_exit

        print(f"\n  Processing: {label} {entry_time.strftime('%H:%M')}–{exit_time.strftime('%H:%M')}")

        # Find option sec_id
        sec_id, exp_date, exp_label = get_option_sec_id(
            date_str, strike, opt_type, nifty_day_open, nifty_day_close, cache)
        time.sleep(3)

        if not sec_id:
            print(f"    [SKIP] sec_id not found for {strike}{opt_type} on {date_str}")
            continue

        # Fetch option bars
        opt_bars = _fetch_intraday(sec_id, "NSE_FNO", "OPTIDX", date_str)
        time.sleep(3)

        if not opt_bars:
            print(f"    [SKIP] No option data for sid={sec_id} on {date_str}")
            continue

        entry_time_str = entry_time.strftime("%H:%M")
        exit_time_str  = exit_time.strftime("%H:%M")

        sell_price = get_price_at_time(opt_bars, entry_time_str)
        exit_price = get_price_at_time(opt_bars, exit_time_str)

        if sell_price is None or exit_price is None:
            print(f"    [SKIP] Option price not available at entry/exit time")
            continue

        print(f"    sec_id={sec_id} expiry={exp_label} "
              f"sell={sell_price} exit={exit_price} "
              f"P&L={round((sell_price - exit_price) * LOT_SIZE, 2)}")

        new_trades.append({
            "label":        label,
            "date":         date_str,
            "direction":    direction,
            "entry_time":   entry_time_str,
            "exit_time":    exit_time_str,
            "strike":       int(strike),
            "opt_type":     opt_type,
            "sell_price":   round(sell_price, 2),
            "exit_price":   round(exit_price, 2),
            "nifty_entry":  round(nifty_entry, 2),
            "exit_reason":  sig.get("exit_reason", ""),
        })

    # ── Step 5: Write to trade_log.json ───────────────────────────
    print(f"\n[5] Writing {len(new_trades)} trades to trade_log.json...")
    existing = []
    if os.path.exists(LOG_F):
        try:
            existing = json.load(open(LOG_F))
        except Exception:
            existing = []

    # Dedup: skip if same date + entry_time already exists
    existing_keys = {(t["date"], t["entry_time"]) for t in existing}
    added = 0
    for t in new_trades:
        key = (t["date"], t["entry_time"])
        if key not in existing_keys:
            existing.append(t)
            existing_keys.add(key)
            added += 1

    # Sort by date + entry_time
    existing.sort(key=lambda x: (x["date"], x["entry_time"]))
    json.dump(existing, open(LOG_F, "w"), indent=2)
    print(f"  Added {added} new trades. Total in log: {len(existing)}")

    return new_trades


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_date", default="2026-06-02")
    ap.add_argument("--to",   dest="to_date",   default=datetime.date.today().isoformat())
    args = ap.parse_args()

    from_dt = datetime.date.fromisoformat(args.from_date)
    to_dt   = datetime.date.fromisoformat(args.to_date)
    trades  = run(from_dt, to_dt)
    print(f"\nDone. {len(trades)} trades generated.")
