"""
range_check.py - Range Chain (Ars_Auto_Rev_Chain) fresh signals from Dhan data.
Saare entry/exit signals collect karta hai - TradingView se manually compare karo.
Run: python /root/code4/range_check.py
"""
import json, requests, datetime, socket, time, sys
import pandas as pd
import numpy as np
from pathlib import Path

# IPv4 force (DH-905)
_orig = socket.getaddrinfo
socket.getaddrinfo = lambda h,p,f=0,t=0,pr=0,fl=0: _orig(h,p,socket.AF_INET,t,pr,fl)

# Credentials
cfg   = json.loads(Path("/root/code4/data/config.json").read_text())
TOKEN = cfg["jwt_token"]
CID   = cfg["client_id"]
HDRS  = {"access-token": TOKEN, "client-id": CID, "Content-Type": "application/json"}

INTRA_URL = "https://api.dhan.co/v2/charts/intraday"
HIST_URL  = "https://api.dhan.co/v2/charts/historical"

# Symbol map: (sec_id, intraday_seg, hist_seg, instrument)
SYMBOL_MAP = {
    "NIFTY":      ("13",    "IDX_I",  "IDX_I",   "INDEX"),
    "BANKNIFTY":  ("25",    "IDX_I",  "IDX_I",   "INDEX"),
    "RELIANCE":   ("2885",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "TCS":        ("11536", "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "INFY":       ("1594",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "HDFCBANK":   ("1333",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "ICICIBANK":  ("4963",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "SBIN":       ("3045",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "AXISBANK":   ("5900",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "BAJFINANCE": ("317",   "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "WIPRO":      ("3787",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "KOTAKBANK":  ("1922",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "LT":         ("11483", "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "MARUTI":     ("10999", "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "HINDUNILVR": ("1394",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "ITC":        ("1660",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "ADANIENT":   ("25",    "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "SUNPHARMA":  ("3351",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "TITAN":      ("3506",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "ULTRACEMCO": ("11532", "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "NESTLEIND":  ("17963", "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "POWERGRID":  ("14977", "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "NTPC":       ("11630", "NSE_EQ", "NSE_EQ",  "EQUITY"),
    "ONGC":       ("2475",  "NSE_EQ", "NSE_EQ",  "EQUITY"),
}

# Dates
today    = datetime.date.today()
today_s  = today.strftime("%Y-%m-%d")
from2_s  = (today - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
from35_s = (today - datetime.timedelta(days=35)).strftime("%Y-%m-%d")


def _dhan_post(url, payload, retries=2):
    """POST with retry on rate-limit (DH-904)."""
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, headers=HDRS, json=payload, timeout=20)
            d = r.json()
            if isinstance(d, dict) and d.get("errorCode") == "DH-904":
                time.sleep(3)
                continue
            return d
        except Exception:
            if attempt < retries:
                time.sleep(2)
    return {}


def fetch_daily_dhan(symbol):
    row = SYMBOL_MAP.get(symbol)
    if not row: return None
    sec_id, _, hist_seg, inst = row
    d = _dhan_post(HIST_URL, {
        "securityId": sec_id, "exchangeSegment": hist_seg,
        "instrument": inst, "expiryCode": 0,
        "fromDate": from35_s, "toDate": today_s
    })
    if "timestamp" not in d:
        return None
    rows = [
        {"date": datetime.datetime.fromtimestamp(ts).date(),
         "open": o, "high": h, "low": l, "close": c}
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"])
    ]
    df = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    return df.tail(22).reset_index(drop=True)


def fetch_1m_dhan(symbol, all_days=False):
    """Fetch 1-min intraday. all_days=True returns multi-day data for ATR warmup."""
    row = SYMBOL_MAP.get(symbol)
    if not row: return None
    sec_id, intra_seg, _, inst = row
    d = _dhan_post(INTRA_URL, {
        "securityId": sec_id, "exchangeSegment": intra_seg,
        "instrument": inst, "interval": "1",
        "fromDate": from2_s, "toDate": today_s
    })
    if "timestamp" not in d:
        return None
    rows = [
        {"time": datetime.datetime.fromtimestamp(ts),
         "open": o, "high": h, "low": l, "close": c}
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"])
        if all_days or datetime.datetime.fromtimestamp(ts).date() == today
    ]
    if not rows: return None
    return pd.DataFrame(rows).reset_index(drop=True)


def resample_to_5m(df_1m):
    """Resample 1-min OHLC to 5-min bars (to match TradingView 5-min chart)."""
    if df_1m is None or df_1m.empty:
        return None
    df = df_1m.copy()
    df = df.set_index("time")
    # TV labels 5-min bars by their closing minute (right-side label)
    df_5m = df.resample("5min", label="right", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
    ).dropna(subset=["open"]).reset_index()
    df_5m = df_5m.rename(columns={"time": "time"})
    return df_5m


# Import logic from range_trader (already has correct ATR, candle patterns)
sys.path.insert(0, "/root/code4")
from range_trader import build_key_levels, compute_atr, is_bullish_pattern, is_bearish_pattern


def collect_all_signals(df_intra, key_levels, cfg, sim_date=None):
    """
    Bar-by-bar simulation matching Pine's exit logic exactly.
    df_intra may contain multi-day data — ATR is computed on full data but
    simulation only runs on sim_date (today). This gives ATR the warmup it needs.
    Pine fill convention: signal on bar i close → fills at bar i+1 OPEN.
    ATR SL initial: position_avg_price (fill open) ± ATR*2.
    Zone exit: opposite zone breakout exits position (MainExit_Toggle).
    """
    if df_intra is None or len(df_intra) < 20:
        return []

    max_cs     = cfg.get("max_candle_size", 25)
    fresh_only = cfg.get("use_fresh_zone_only", True)
    exit_atr   = cfg.get("exit_atr", True)
    exit_zone  = cfg.get("exit_zone", True)
    atr_mult   = 2.0
    max_trades = cfg.get("max_trades_per_symbol", 4)
    zone_age   = cfg.get("zone_age", 2)

    # Compute ATR on full multi-day data for proper warmup
    atr_series = compute_atr(df_intra, 14)

    # Filter to simulation date only (but keep full atr_series aligned)
    if sim_date is not None:
        mask = df_intra["time"].dt.date == sim_date
        sim_indices = df_intra.index[mask].tolist()
        if not sim_indices:
            return []
        sim_start = sim_indices[0]
    else:
        sim_start = 0

    # Zone state
    zone_upper = zone_lower = zone_type = None
    zone_bar   = -999
    # Persistent last zone prices (survive zone clear after entry, like Pine's zone_Box)
    last_zone_upper = last_zone_lower = None
    last_zone_bar_green = -999  # for greenZoneFresh
    last_zone_bar_red   = -999  # for redZoneFresh

    tracked_high = tracked_low = None
    touch_active = False
    active_touch_type = None

    atr_sl_long = atr_sl_short = None
    position    = None
    entry_bar   = None  # bar index where fill happens (i+1 after signal)
    trades_today = 0
    signals     = []

    n = len(df_intra)
    loop_start = max(2, sim_start)
    for i in range(loop_start, n):
        row = df_intra.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        ts  = row["time"]
        tsf = ts.strftime("%H:%M") if hasattr(ts, "strftime") else str(ts)
        atr_val = float(atr_series.iloc[i]) if not np.isnan(atr_series.iloc[i]) else 5.0

        # ── STEP 1: entry fill bar — set initial ATR SL using open (fill price) ──
        if entry_bar == i and position is not None:
            fill_price = o  # TV fills at open of next bar after signal
            if position == "LONG":
                atr_sl_long  = fill_price - atr_val * atr_mult
            elif position == "SHORT":
                atr_sl_short = fill_price + atr_val * atr_mult
            entry_bar = None

        # ── STEP 2: ATR trailing SL update ──
        if position == "LONG" and atr_sl_long is not None:
            new_sl = c - atr_val * atr_mult
            if new_sl > atr_sl_long:
                atr_sl_long = new_sl

        if position == "SHORT" and atr_sl_short is not None:
            new_sl = c + atr_val * atr_mult
            if new_sl < atr_sl_short:
                atr_sl_short = new_sl

        # ── STEP 3: Exit checks (ATR priority, then Zone) ──
        green_fresh = (i - last_zone_bar_green) <= zone_age
        red_fresh   = (i - last_zone_bar_red)   <= zone_age

        if position == "LONG":
            # ATR exit
            if exit_atr and atr_sl_long is not None and c < atr_sl_long:
                signals.append((tsf, "EXIT_L", c, "ATR_LONG"))
                position = None; atr_sl_long = None
            # Zone exit: close below red zone lower, red candle, fresh red zone
            elif exit_zone and last_zone_lower is not None and red_fresh:
                if c < last_zone_lower and c < o:
                    signals.append((tsf, "EXIT_L", c, "ZONE_LONG"))
                    position = None; atr_sl_long = None

        elif position == "SHORT":
            # ATR exit
            if exit_atr and atr_sl_short is not None and c > atr_sl_short:
                signals.append((tsf, "EXIT_S", c, "ATR_SHORT"))
                position = None; atr_sl_short = None
            # Zone exit: close above green zone upper, green candle, fresh green zone
            elif exit_zone and last_zone_upper is not None and green_fresh:
                if c > last_zone_upper and c > o:
                    signals.append((tsf, "EXIT_S", c, "ZONE_SHORT"))
                    position = None; atr_sl_short = None

        # ── STEP 4: Touch detection ──
        touched_level = touched_type = None
        for price, ltype in key_levels:
            if l <= price <= h:
                touched_level = price
                touched_type  = ltype
                break

        if touched_level is not None:
            if not touch_active:
                touch_active = True
                active_touch_type = touched_type
                tracked_high = h
                tracked_low  = l
            else:
                active_touch_type = touched_type
                if h > (tracked_high or h): tracked_high = h
                if l < (tracked_low  or l): tracked_low  = l

        # ── STEP 5: Zone formation ──
        bullish = is_bullish_pattern(df_intra, i)
        bearish = is_bearish_pattern(df_intra, i)

        if touch_active:
            not_on_red = active_touch_type not in ("RESISTANCE", "PD_H") if active_touch_type else True
            not_on_grn = active_touch_type not in ("SUPPORT",    "PD_L") if active_touch_type else True

            if bearish and not_on_grn and (h - l) <= max_cs:
                zone_upper = h; zone_lower = l; zone_type = "RED"
                zone_bar = i
                last_zone_upper = h; last_zone_lower = l
                last_zone_bar_red = i
                tracked_high = tracked_low = None; touch_active = False

            elif bullish and not_on_red and (h - l) <= max_cs:
                zone_upper = h; zone_lower = l; zone_type = "GREEN"
                zone_bar = i
                last_zone_upper = h; last_zone_lower = l
                last_zone_bar_green = i
                tracked_high = tracked_low = None; touch_active = False

        # ── STEP 6: Entry conditions ──
        if position is not None or trades_today >= max_trades or zone_upper is None:
            continue

        # Skip entries at/after 15:15
        if hasattr(ts, "hour") and (ts.hour > 15 or (ts.hour == 15 and ts.minute >= 15)):
            continue

        zone_fresh = (i - zone_bar) <= zone_age
        use_zone   = zone_fresh if fresh_only else (zone_type is not None)

        prev    = df_intra.iloc[i - 1]
        prev_green = float(prev["close"]) > float(prev["open"])
        prev_red   = float(prev["close"]) < float(prev["open"])
        curr_green = c > o
        curr_red   = c < o

        # trackedHigh/trackedLow guards
        long_ok  = (tracked_high is None or c <= tracked_high)
        short_ok = (tracked_low  is None or c >= tracked_low)

        if (zone_type == "GREEN" and use_zone and c > zone_upper
                and prev_green and curr_green and long_ok and (h - l) <= max_cs):
            signals.append((tsf, "BUY", c, f"GREEN>{zone_upper:.1f}"))
            position  = "LONG"
            entry_bar = i + 1   # fill at next bar open
            trades_today += 1
            # Pine resets zone after entry
            zone_upper = zone_lower = zone_type = None
            zone_bar = -999

        elif (zone_type == "RED" and use_zone and c < zone_lower
                and prev_red and curr_red and short_ok and (h - l) <= max_cs):
            signals.append((tsf, "SELL", c, f"RED<{zone_lower:.1f}"))
            position  = "SHORT"
            entry_bar = i + 1   # fill at next bar open
            trades_today += 1
            # Pine resets zone after entry
            zone_upper = zone_lower = zone_type = None
            zone_bar = -999

    return signals


# Config — matches Pine defaults (5-min chart, Zone Exit ON, ATR Exit ON)
CFG = {
    "max_candle_size": 25,
    "use_fresh_zone_only": True,
    "hawa_me_zone": False,
    "exit_zone": True,
    "exit_atr": True,
    "max_trades_per_symbol": 4,  # Pine maxTradesPerDay=4
    "zone_age": 2,
}

print(f"\n{'='*72}")
print(f"  Range Chain (Ars_Auto_Rev_Chain) - 5-min signals - {today_s}")
print(f"  Config: FreshZone=True  ATRExit=ON  ZoneExit=ON  MaxTrades=4  MaxCS=25")
print(f"{'='*72}")
print(f"  {'Symbol':<14} {'Time':>6}  {'Action':>6}  {'Price':>10}  Reason")
print(f"  {'-'*65}")

all_entries = []
errors = []

for i, sym in enumerate(SYMBOL_MAP):
    if i > 0:
        time.sleep(1.2)  # avoid DH-904 rate limit

    daily_df = fetch_daily_dhan(sym)
    if daily_df is None or len(daily_df) < 2:
        errors.append(f"{sym}:no_daily")
        print(f"  {sym:<14}  -- no daily data")
        continue

    is_idx = sym in ("NIFTY", "BANKNIFTY")
    key_levels = build_key_levels(daily_df, is_index=is_idx)

    df_1m = fetch_1m_dhan(sym, all_days=True)  # multi-day for ATR warmup
    if df_1m is None or df_1m.empty:
        errors.append(f"{sym}:no_intra")
        print(f"  {sym:<14}  -- no intraday data")
        continue

    df_5m = resample_to_5m(df_1m)
    if df_5m is None or df_5m.empty:
        errors.append(f"{sym}:no_5m")
        print(f"  {sym:<14}  -- 5m resample failed")
        continue

    sigs = collect_all_signals(df_5m, key_levels, CFG, sim_date=today)
    if sigs:
        for tstr, action, price, reason in sigs:
            print(f"  {sym:<14} {tstr:>6}  {action:>6}  {price:>10.2f}  {reason}")
            all_entries.append((sym, tstr, action, price))
    else:
        print(f"  {sym:<14}         --    no signal today")

print(f"\n{'='*72}")
print(f"  Total signals : {len(all_entries)}")
print(f"  BUY entries   : {sum(1 for _,_,a,_ in all_entries if a=='BUY')}")
print(f"  SELL entries  : {sum(1 for _,_,a,_ in all_entries if a=='SELL')}")
print(f"  Exits         : {sum(1 for _,_,a,_ in all_entries if a.startswith('EXIT'))}")
if errors:
    print(f"  Errors        : {', '.join(errors)}")
print(f"{'='*72}\n")
