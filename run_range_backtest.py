import pandas as pd
import glob
import os
import sys
from datetime import datetime
from range_trader import build_key_levels, compute_atr, is_bullish_pattern, is_bearish_pattern, traditional_pivots

DATA_DIR = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\Index\NIFTY"

def build_daily_df(until_date="2026-05-05"):
    files = glob.glob(os.path.join(DATA_DIR, "*_*.csv"))
    daily_rows = []
    
    def get_date(filepath):
        return os.path.basename(filepath).lower().replace("nifty_", "").replace(".csv", "")
        
    for f in sorted(files, key=get_date):
        basename = os.path.basename(f).lower()
        date_str = basename.replace("nifty_", "").replace(".csv", "")
        if date_str > until_date:
            continue
            
        try:
            df = pd.read_csv(f)
            if df.empty:
                continue
                
            df.columns = [c.lower() for c in df.columns]
            
            d_open = df.iloc[0]["open"]
            d_close = df.iloc[-1]["close"]
            d_high = df["high"].max()
            d_low = df["low"].min()
            
            daily_rows.append({
                "date": date_str,
                "open": float(d_open),
                "high": float(d_high),
                "low": float(d_low),
                "close": float(d_close)
            })
        except Exception as e:
            print(f"Error reading {f}: {e}")
            
    return pd.DataFrame(daily_rows)

def run_backtest(date_to_test="2026-05-06", timeframe="5min"):
    print(f"Building daily data up to {date_to_test}...")
    daily_df = build_daily_df(date_to_test)
    
    key_levels = build_key_levels(daily_df, is_index=True, max_jump_pct=10.0)
    print(f"\n--- KEY LEVELS for {date_to_test} ---")
    for p, t in sorted(key_levels, key=lambda x: x[0], reverse=True):
        print(f"  {t:12}: {p:.2f}")
        
    test_file = os.path.join(DATA_DIR, f"NIFTY_{date_to_test}.csv")
    if not os.path.exists(test_file):
        print(f"Error: Data file for test date {date_to_test} not found!")
        return
        
    print(f"\n--- Loading and Resampling Intraday Data ({timeframe}) ---")
    df_1m = pd.read_csv(test_file)
    df_1m.columns = [c.lower() for c in df_1m.columns]
    df_1m['datetime'] = pd.to_datetime(df_1m['datetime'])
    df_1m.set_index('datetime', inplace=True)
    
    # Resample to timeframe
    df_tf = df_1m.resample(timeframe.replace("min", "T")).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last'
    }).dropna().reset_index()
    
    print(f"Total candles ({timeframe}): {len(df_tf)}")
    
    # 4. Simulate
    df = df_tf
    atr_len    = 14
    atr_mult   = 2.0
    max_cs     = 25 # default max_candle_size
    zone_age   = 2
    fresh_only = True
    exit_atr   = True
    
    atr_series = compute_atr(df, atr_len)
    
    zone_upper = None
    zone_lower = None
    zone_type  = None
    zone_bar   = -999
    
    tracked_high = None
    tracked_low  = None
    touch_active = False
    active_touch_type = None
    
    atr_sl_long  = None
    atr_sl_short = None
    position     = None
    entry_price  = None
    
    trades = []
    
    print("\n--- RUNNING STRATEGY ---")
    n = len(df)
    for i in range(2, n):
        row = df.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        t = row["datetime"]
        curr_atr = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else 5.0
        ignore_max_cs = False
        max_cs = 25.0 if not ignore_max_cs else 9999.0
        
        # Touch detection
        touched_level = None
        touched_type  = None
        for price, ltype in key_levels:
            if l <= price <= h:
                touched_level = price
                touched_type  = ltype
                break
                
        if touched_level is not None:
            if not touch_active:
                touch_active  = True
                active_touch_type = touched_type
                tracked_high  = h
                tracked_low   = l
            else:
                active_touch_type = touched_type
                if h > (tracked_high or h): tracked_high = h
                if l < (tracked_low  or l): tracked_low  = l
                
        # Zone formation
        bullish = is_bullish_pattern(df, i)
        bearish = is_bearish_pattern(df, i)
        
        if touch_active:
            not_on_red_line  = active_touch_type not in ("RESISTANCE", "PD_H") if active_touch_type else True
            not_on_grn_line  = active_touch_type not in ("SUPPORT", "PD_L") if active_touch_type else True
            
            if bearish and not_on_grn_line:
                candle_size = h - l
                if candle_size <= max_cs:
                    zone_upper = h
                    zone_lower = l
                    zone_type  = "RED"
                    zone_bar   = i
                    tracked_high = None
                    tracked_low  = None
                    touch_active = False
                    print(f"[{t.time()}] RED ZONE Formed at {l:.2f}-{h:.2f} (Touch: {active_touch_type} @ {touched_level if touched_level else 'Previous'})")
                else:
                    pass # print(f"[{t.time()}] Candle size {candle_size:.2f} > max_cs {max_cs}, ignoring RED ZONE")
                    
            elif bullish and not_on_red_line:
                candle_size = h - l
                if candle_size <= max_cs:
                    zone_upper = h
                    zone_lower = l
                    zone_type  = "GREEN"
                    zone_bar   = i
                    tracked_high = None
                    tracked_low  = None
                    touch_active = False
                    print(f"[{t.time()}] GREEN ZONE Formed at {l:.2f}-{h:.2f} (Touch: {active_touch_type} @ {touched_level if touched_level else 'Previous'})")
                else:
                    pass # print(f"[{t.time()}] Candle size {candle_size:.2f} > max_cs {max_cs}, ignoring GREEN ZONE")
                    
        # Exit: ATR trailing stop
        if exit_atr and position == "LONG" and atr_sl_long is not None:
            new_sl = c - curr_atr * atr_mult
            if new_sl > atr_sl_long:
                atr_sl_long = new_sl
            if c < atr_sl_long:
                print(f"[{t.time()}] EXIT LONG @ {c:.2f} (ATR SL hit)")
                trades.append(("EXIT_LONG", t, c, c - entry_price))
                position = None
                atr_sl_long = None
                
        if exit_atr and position == "SHORT" and atr_sl_short is not None:
            new_sl = c + curr_atr * atr_mult
            if new_sl < atr_sl_short:
                atr_sl_short = new_sl
            if c > atr_sl_short:
                print(f"[{t.time()}] EXIT SHORT @ {c:.2f} (ATR SL hit)")
                trades.append(("EXIT_SHORT", t, c, entry_price - c))
                position = None
                atr_sl_short = None
                
        if exit_atr:
            if position == "LONG" and curr_atr is not None:
                new_sl = h - (curr_atr * 2.5)
                if atr_sl_long is None or new_sl > atr_sl_long:
                    atr_sl_long = new_sl
                    
            if position == "SHORT" and curr_atr is not None:
                new_sl = l + (curr_atr * 2.5)
                if atr_sl_short is None or new_sl < atr_sl_short:
                    atr_sl_short = new_sl
                    
        # ── Entry logic ──────────────────────────────────────────────────────
        # Use simple entry condition: if price crosses zone boundaries
        
        # We need previous candles for entry confirmations
        if i < 1: continue
        prev = df.iloc[i-1]
        prev_green = float(prev["close"]) > float(prev["open"])
        prev_red   = float(prev["close"]) < float(prev["open"])
        curr_green = c > o
        curr_red   = c < o
        
        # "Use Fresh Zone Only" -> require entry within zone_age (default 2)
        zone_fresh = (i - zone_bar) <= zone_age if fresh_only else True
        use_zone = (zone_type is not None)
        
        if (zone_type == "GREEN" and use_zone and zone_fresh and
                c > zone_upper and
                prev_green and curr_green and
                (tracked_high is None or c <= tracked_high) and
                position != "LONG"):
            
            position = "LONG"
            entry_price = c
            entry_time = t.time()
            atr_sl_long = c - curr_atr * atr_mult
            trades.append(("LONG", t, c, 0))
            print(f"[{t.time()}] ENTRY LONG @ {c:.2f}")
            
        elif (zone_type == "RED" and use_zone and zone_fresh and
                c < zone_lower and
                prev_red and curr_red and
                (tracked_low is None or c >= tracked_low) and
                position != "SHORT"):
            
            position = "SHORT"
            entry_price = c
            entry_time = t.time()
            atr_sl_short = c + curr_atr * atr_mult
            print(f"[{t.time()}] ENTRY SHORT @ {c:.2f}")
            trades.append(("SHORT", t, c, 0))
            
    # Auto exit at end of day
    if position is not None:
        c = df.iloc[-1]["close"]
        t = df.iloc[-1]["datetime"]
        if position == "LONG":
            print(f"[{t.time()}] AUTO EXIT LONG @ {c:.2f}")
            trades.append(("EXIT_LONG", t, c, c - entry_price))
        elif position == "SHORT":
            print(f"[{t.time()}] AUTO EXIT SHORT @ {c:.2f}")
            trades.append(("EXIT_SHORT", t, c, entry_price - c))

    print("\n--- TRADES SUMMARY ---")
    total_pnl = 0
    trade_count = 0
    if not trades:
        print("No trades generated today.")
    else:
        for tr in trades:
            if "EXIT" in tr[0]:
                total_pnl += tr[3]
            else:
                trade_count += 1
            print(f"{tr[0]:10} | {tr[1].time()} | Price: {tr[2]:.2f} | PnL: {tr[3]:.2f}")
            
    print(f"\nTotal Trades Taken: {trade_count}")
    print(f"TOTAL PNL (points): {total_pnl:.2f}")

if __name__ == "__main__":
    run_backtest("2026-05-06", "5min")
