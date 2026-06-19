#!/usr/bin/env python3
"""
range_trader.py — Ars_Auto_Rev_Chain RANGE Strategy | 1-min

PineScript se convert kiya gaya. Logic:
  1. Daily levels: Prev-day H/L/C + Traditional Pivots + High/Low Chain (20-day)
  2. Zone detection: key level touch + candle pattern → Green/Red zone
  3. Entry: zone fresh (<=2 bars) + close above/below zone + candle confirm
  4. Exit: ATR trailing stop (14x2) + zone exit + 3:15 PM auto

Modes:
  --paper  : Signals only, no real orders (default)
  --live   : Real orders on Dhan
"""

import json
import logging
import socket
import sys
import dhan_master
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# ── IPv4 Force — Dhan rejects IPv6 (DH-905) ──────────────────────────────────
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
CFG_FILE    = BASE_DIR / "range_config.json"
LOG_FILE    = BASE_DIR / "range_trader.log"

# ── Symbols ───────────────────────────────────────────────────────────────────
SYMBOLS = {
    "NIFTY":        "^NSEI",
    "BANKNIFTY":    "^NSEBANK",
    "RELIANCE":     "RELIANCE.NS",
    "TCS":          "TCS.NS",
    "INFY":         "INFY.NS",
    "HDFCBANK":     "HDFCBANK.NS",
    "ICICIBANK":    "ICICIBANK.NS",
    "SBIN":         "SBIN.NS",
    "AXISBANK":     "AXISBANK.NS",
    "BAJFINANCE":   "BAJFINANCE.NS",
    "WIPRO":        "WIPRO.NS",
    "KOTAKBANK":    "KOTAKBANK.NS",
    "LT":           "LT.NS",
    "MARUTI":       "MARUTI.NS",
    "HINDUNILVR":   "HINDUNILVR.NS",
    "ITC":          "ITC.NS",
    "ADANIENT":     "ADANIENT.NS",
    "SUNPHARMA":    "SUNPHARMA.NS",
    "TITAN":        "TITAN.NS",
    "ULTRACEMCO":   "ULTRACEMCO.NS",
    "NESTLEIND":    "NESTLEIND.NS",
    "POWERGRID":    "POWERGRID.NS",
    "NTPC":         "NTPC.NS",
    "ONGC":         "ONGC.NS",
    "ASIANPAINT":   "ASIANPAINT.NS",
    "BHARTIARTL":   "BHARTIARTL.NS",
    "HCLTECH":      "HCLTECH.NS",
    "BAJAJFINSV":   "BAJAJFINSV.NS",
    "TATACONSUM":   "TATACONSUM.NS",
    "COALINDIA":    "COALINDIA.NS",
    "DIVISLAB":     "DIVISLAB.NS",
    "DRREDDY":      "DRREDDY.NS",
    "EICHERMOT":    "EICHERMOT.NS",
    "GRASIM":       "GRASIM.NS",
    "HEROMOTOCO":   "HEROMOTOCO.NS",
    "HINDALCO":     "HINDALCO.NS",
    "JSWSTEEL":     "JSWSTEEL.NS",
    "M&M":          "M&M.NS",
    "SBILIFE":      "SBILIFE.NS",
    "SHRIRAMFIN":   "SHRIRAMFIN.NS",
    "TATASTEEL":    "TATASTEEL.NS",
    "TECHM":        "TECHM.NS",
    "TRENT":        "TRENT.NS",
}

DHAN_INFO = {
    "RELIANCE":    ("2885",  "NSE_EQ"),
    "TCS":         ("11536", "NSE_EQ"),
    "INFY":        ("1594",  "NSE_EQ"),
    "HDFCBANK":    ("1333",  "NSE_EQ"),
    "ICICIBANK":   ("4963",  "NSE_EQ"),
    "SBIN":        ("3045",  "NSE_EQ"),
    "AXISBANK":    ("5900",  "NSE_EQ"),
    "BAJFINANCE":  ("317",   "NSE_EQ"),
    "WIPRO":       ("3787",  "NSE_EQ"),
    "KOTAKBANK":   ("1922",  "NSE_EQ"),
    "LT":          ("11483", "NSE_EQ"),
    "MARUTI":      ("10999", "NSE_EQ"),
    "HINDUNILVR":  ("1394",  "NSE_EQ"),
    "ITC":         ("1660",  "NSE_EQ"),
    "ADANIENT":    ("25",    "NSE_EQ"),
    "SUNPHARMA":   ("3351",  "NSE_EQ"),
    "TITAN":       ("3506",  "NSE_EQ"),
    "ULTRACEMCO":  ("11532", "NSE_EQ"),
    "NESTLEIND":   ("17963", "NSE_EQ"),
    "POWERGRID":   ("14977", "NSE_EQ"),
    "NTPC":        ("11630", "NSE_EQ"),
    "ONGC":        ("2475",  "NSE_EQ"),
}

# Dhan Data API — sec_id + segment + instrument type
# NSE_IDX for indices, NSE_EQ for stocks
_DHAN_DATA = {
    "NIFTY":      ("13",    "NSE_IDX", "INDEX"),
    "BANKNIFTY":  ("25",    "NSE_IDX", "INDEX"),
    "RELIANCE":   ("2885",  "NSE_EQ",  "EQUITY"),
    "TCS":        ("11536", "NSE_EQ",  "EQUITY"),
    "INFY":       ("1594",  "NSE_EQ",  "EQUITY"),
    "HDFCBANK":   ("1333",  "NSE_EQ",  "EQUITY"),
    "ICICIBANK":  ("4963",  "NSE_EQ",  "EQUITY"),
    "SBIN":       ("3045",  "NSE_EQ",  "EQUITY"),
    "AXISBANK":   ("5900",  "NSE_EQ",  "EQUITY"),
    "BAJFINANCE": ("317",   "NSE_EQ",  "EQUITY"),
    "WIPRO":      ("3787",  "NSE_EQ",  "EQUITY"),
    "KOTAKBANK":  ("1922",  "NSE_EQ",  "EQUITY"),
    "LT":         ("11483", "NSE_EQ",  "EQUITY"),
    "MARUTI":     ("10999", "NSE_EQ",  "EQUITY"),
    "HINDUNILVR": ("1394",  "NSE_EQ",  "EQUITY"),
    "ITC":        ("1660",  "NSE_EQ",  "EQUITY"),
    "SUNPHARMA":  ("3351",  "NSE_EQ",  "EQUITY"),
    "TITAN":      ("3506",  "NSE_EQ",  "EQUITY"),
    "ULTRACEMCO": ("11532", "NSE_EQ",  "EQUITY"),
    "NESTLEIND":  ("17963", "NSE_EQ",  "EQUITY"),
    "POWERGRID":  ("14977", "NSE_EQ",  "EQUITY"),
    "NTPC":       ("11630", "NSE_EQ",  "EQUITY"),
    "ONGC":       ("2475",  "NSE_EQ",  "EQUITY"),
}

_TF_MAP = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30"}

ORDERS_URL   = "https://api.dhan.co/v2/orders"
MARKET_OPEN  = (9, 15)
MARKET_CLOSE = (15, 25)
AUTO_EXIT_AT = (15, 15)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("range_trader")


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)

def is_market_open():
    t = (ist_now().hour, ist_now().minute)
    return MARKET_OPEN <= t < MARKET_CLOSE

def is_exit_time():
    t = (ist_now().hour, ist_now().minute)
    return t >= AUTO_EXIT_AT

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def load_config(strategy_id=None):
    default = {
        "active": True,
        "timeframe": "1m",
        "qty": 1,
        "max_trades_per_symbol": 2,
        "max_candle_size": 25,
        "hawa_me_zone": False,
        "use_fresh_zone_only": True,
        "exit_zone": False,
        "exit_atr": True,
        "exit_fib": False,
        "symbols": ["NIFTY"]
    }
    # Multi-variation: read from nifty_config.json[strategy_id]
    if strategy_id:
        tc_file = BASE_DIR / "nifty_config.json"
        try:
            all_cfg = json.loads(tc_file.read_text())
            if strategy_id in all_cfg:
                return {**default, **all_cfg[strategy_id]}
        except Exception:
            pass
    if not CFG_FILE.exists():
        CFG_FILE.write_text(json.dumps(default, indent=2))
        return default
    try:
        return json.loads(CFG_FILE.read_text())
    except Exception:
        return default

def hdrs(token, cid):
    return {"access-token": token, "client-id": cid, "Content-Type": "application/json"}


# ─────────────────────────────────────────────────────────────────────────────
# DAILY DATA — key levels
# ─────────────────────────────────────────────────────────────────────────────

def fetch_daily(symbol, days=22):
    """Fetch daily OHLC via yfinance."""
    ticker = SYMBOLS.get(symbol)
    if not ticker:
        return None
    try:
        df = yf.download(ticker, period=f"{days}d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        df = df[["date", "open", "high", "low", "close"]].dropna()
        return df.tail(21).reset_index(drop=True)
    except Exception as e:
        log.error(f"{symbol} daily fetch error: {e}")
        return None


def traditional_pivots(h, l, c):
    """Traditional pivot points from prev day H/L/C."""
    P  = (h + l + c) / 3
    R1 = 2 * P - l
    S1 = 2 * P - h
    R2 = P + (h - l)
    S2 = P - (h - l)
    R3 = h + 2 * (P - l)
    S3 = l - 2 * (h - P)
    R4 = R3 + (h - l)
    S4 = S3 - (h - l)
    R5 = R4 + (h - l)
    S5 = S4 - (h - l)
    return dict(P=P, R1=R1, R2=R2, R3=R3, R4=R4, R5=R5,
                S1=S1, S2=S2, S3=S3, S4=S4, S5=S5)


def build_key_levels(daily_df, is_index=False, max_jump_pct=50.0):
    """
    Build all key levels: pivot + prev-day HLC + high/low chain.
    Returns list of (price, level_type) tuples.
    Sorted: resistances first, then CP, then supports, then chain.
    """
    if daily_df is None or len(daily_df) < 2:
        return []

    mj = 10.0 if is_index else max_jump_pct

    # Prev day (index -2 = yesterday, -1 = today/current)
    prev = daily_df.iloc[-2]
    ph, pl, pc = float(prev["high"]), float(prev["low"]), float(prev["close"])

    levels = []  # (price, type)

    # Pivot points
    piv = traditional_pivots(ph, pl, pc)
    for name in ["R5","R4","R3","R2","R1"]:
        levels.append((piv[name], "RESISTANCE"))
    levels.append((piv["P"], "CP"))
    for name in ["S1","S2","S3","S4","S5"]:
        levels.append((piv[name], "SUPPORT"))

    # Prev day H/L/C
    levels.append((ph, "PD_H"))
    levels.append((pc, "PD_C"))
    levels.append((pl, "PD_L"))

    # High chain: consecutive higher highs going back from prev day
    h_thresh = ph
    for i in range(len(daily_df) - 3, max(len(daily_df) - 23, -1), -1):
        row_h = float(daily_df.iloc[i]["high"])
        if row_h > h_thresh:
            jump = (row_h - h_thresh) / h_thresh * 100
            if jump <= mj:
                levels.append((row_h, "RESISTANCE"))
                h_thresh = row_h

    # Low chain: consecutive lower lows going back from prev day
    l_thresh = pl
    for i in range(len(daily_df) - 3, max(len(daily_df) - 23, -1), -1):
        row_l = float(daily_df.iloc[i]["low"])
        if row_l < l_thresh:
            drop = (l_thresh - row_l) / l_thresh * 100
            if drop <= mj:
                levels.append((row_l, "SUPPORT"))
                l_thresh = row_l

    # Remove NaN / zero
    levels = [(p, t) for p, t in levels if p and not np.isnan(p) and p > 0]
    return levels


# ─────────────────────────────────────────────────────────────────────────────
# CANDLE PATTERNS (AA_CandlePatterns equivalent)
# ─────────────────────────────────────────────────────────────────────────────

MIN_BODY_SIZE = 0.5    # minimum body in points (Pine minBodySize)
WICK_RATIO    = 2.5    # wick >= WICK_RATIO * body (Pine wickRatio=2.5)

def _body(o, c):
    return abs(c - o)

def _lower_wick(o, c, l):
    return min(o, c) - l

def _upper_wick(o, c, h):
    return h - max(o, c)

def green_hammer(o, h, l, c):
    body = _body(o, c)
    if body < MIN_BODY_SIZE or c <= o:   # must be green
        return False
    lw = _lower_wick(o, c, l)
    uw = _upper_wick(o, c, h)
    return lw >= WICK_RATIO * body and uw <= body

def red_hammer(o, h, l, c):
    body = _body(o, c)
    if body < MIN_BODY_SIZE or c >= o:   # must be red
        return False
    lw = _lower_wick(o, c, l)
    uw = _upper_wick(o, c, h)
    return lw >= WICK_RATIO * body and uw <= body   # Pine: upperWick <= bodySize

def inv_red_hammer(o, h, l, c):
    body = _body(o, c)
    if body < MIN_BODY_SIZE or c >= o:   # must be red
        return False
    uw = _upper_wick(o, c, h)
    lw = _lower_wick(o, c, l)
    return uw >= WICK_RATIO * body and lw <= body   # Pine: lowerWick <= bodySize

def bull_engulfing(po, ph, pl, pc, o, h, l, c):
    prev_red   = pc < po
    curr_green = c > o
    prev_body = abs(po - pc)
    curr_body = abs(o - c)
    if not prev_red or not curr_green:
        return False
    return c >= po and o <= pc and curr_body > prev_body and prev_body >= 0.5

def bear_engulfing(po, ph, pl, pc, o, h, l, c):
    prev_green = pc > po
    curr_red   = c < o
    prev_body = abs(po - pc)
    curr_body = abs(o - c)
    if not prev_green or not curr_red:
        return False
    return c <= po and o >= pc and curr_body > prev_body and prev_body >= 0.5

def bull_harami(po, ph, pl, pc, o, h, l, c):
    prev_red   = pc < po
    curr_green = c > o
    prev_body  = abs(pc - po)
    curr_body  = abs(c - o)
    body50pct = curr_body >= prev_body * 0.5
    if not prev_red or not curr_green:
        return False
    return o > pc and c < po and body50pct

def bear_harami(po, ph, pl, pc, o, h, l, c):
    prev_green = pc > po
    curr_red   = c < o
    prev_body  = abs(pc - po)
    curr_body  = abs(c - o)
    body50pct = curr_body >= prev_body * 0.5
    if not prev_green or not curr_red:
        return False
    return o < pc and c > po and body50pct

def is_bullish_pattern(df, idx):
    if idx < 1:
        return False
    r  = df.iloc[idx]
    rp = df.iloc[idx - 1]
    o, h, l, c    = float(r["open"]),  float(r["high"]),  float(r["low"]),  float(r["close"])
    po, ph, pl, pc = float(rp["open"]), float(rp["high"]), float(rp["low"]), float(rp["close"])
    return (green_hammer(o, h, l, c) or
            bull_engulfing(po, ph, pl, pc, o, h, l, c))

def is_bearish_pattern(df, idx):
    if idx < 1:
        return False
    r  = df.iloc[idx]
    rp = df.iloc[idx - 1]
    o, h, l, c    = float(r["open"]),  float(r["high"]),  float(r["low"]),  float(r["close"])
    po, ph, pl, pc = float(rp["open"]), float(rp["high"]), float(rp["low"]), float(rp["close"])
    return (red_hammer(o, h, l, c) or
            inv_red_hammer(o, h, l, c) or
            bear_engulfing(po, ph, pl, pc, o, h, l, c))


# ─────────────────────────────────────────────────────────────────────────────
# ATR
# ─────────────────────────────────────────────────────────────────────────────

def compute_atr(df, period=14):
    h, l, pc = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    # Pine ta.atr uses Wilder's RMA (alpha = 1/period), NOT EMA span
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────────────────
# INTRADAY SIGNAL ENGINE — runs bar by bar on 1-min data
# ─────────────────────────────────────────────────────────────────────────────

def run_signal_engine(df_1m, key_levels, cfg):
    """
    Simulate bar-by-bar zone detection + entry/exit on 1-min candles.
    Returns last signal: ('BUY'/'SELL'/None, price, reason)
    """
    if df_1m is None or len(df_1m) < 20:
        return None, None, None

    atr_len    = 14          # fixed — not exposed in config anymore
    atr_mult   = 2.0         # fixed
    max_cs     = cfg.get("max_candle_size", 25)
    zone_age   = 2           # fixed
    fresh_only = cfg.get("use_fresh_zone_only", True)
    hawa_me    = cfg.get("hawa_me_zone", False)
    exit_atr   = cfg.get("exit_atr", True)
    exit_zone  = cfg.get("exit_zone", False)

    atr_series = compute_atr(df_1m, atr_len)

    # Zone state
    zone_upper = None
    zone_lower = None
    zone_type  = None   # 'GREEN' or 'RED'
    zone_bar   = -999

    # Tracking high/low while touching level
    tracked_high = None
    tracked_low  = None
    touch_active = False
    active_touch_type = None

    # ATR trailing stop
    atr_sl_long  = None
    atr_sl_short = None
    position     = None   # 'LONG', 'SHORT', None
    entry_price  = None

    signal       = None
    signal_price = None
    signal_reason= None
    signal_bar   = -1
    trades_today = 0
    max_trades   = cfg.get("max_trades_per_symbol", 2)

    n = len(df_1m)
    for i in range(2, n):
        row   = df_1m.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        
        # ATR calculation for max_cs
        curr_atr = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else 5.0
        ignore_max_cs = False
        max_cs = 25.0 if not ignore_max_cs else 9999.0
        
        atr_val = float(atr_series.iloc[i]) if not np.isnan(atr_series.iloc[i]) else 5.0

        # ── Touch detection ──────────────────────────────────────────────────
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

        # ── Zone formation ───────────────────────────────────────────────────
        bullish = is_bullish_pattern(df_1m, i)
        bearish = is_bearish_pattern(df_1m, i)

        if touch_active:
            not_on_red_line  = active_touch_type not in ("RESISTANCE", "PD_H") if active_touch_type else True
            not_on_grn_line  = active_touch_type not in ("SUPPORT", "PD_L") if active_touch_type else True

            if bearish and not_on_grn_line:
                # Red zone: short setup
                candle_size = h - l
                if candle_size <= max_cs:
                    zone_upper = h
                    zone_lower = l
                    zone_type  = "RED"
                    zone_bar   = i
                    tracked_high = None
                    tracked_low  = None
                    touch_active = False

            elif bullish and not_on_red_line:
                # Green zone: long setup
                candle_size = h - l
                if candle_size <= max_cs:
                    zone_upper = h
                    zone_lower = l
                    zone_type  = "GREEN"
                    zone_bar   = i
                    tracked_high = None
                    tracked_low  = None
                    touch_active = False

        # ── Exit: ATR trailing stop ──────────────────────────────────────────
        if exit_atr and position == "LONG" and atr_sl_long is not None:
            new_sl = c - atr_val * atr_mult
            if new_sl > atr_sl_long:
                atr_sl_long = new_sl
            if c < atr_sl_long:
                signal       = "EXIT_LONG"
                signal_price = c
                signal_reason= "ATR_TRAILING"
                position     = None
                atr_sl_long  = None

        if exit_atr and position == "SHORT" and atr_sl_short is not None:
            new_sl = c + atr_val * atr_mult
            if new_sl < atr_sl_short:
                atr_sl_short = new_sl
            if c > atr_sl_short:
                signal       = "EXIT_SHORT"
                signal_price = c
                signal_reason= "ATR_TRAILING"
                position     = None
                atr_sl_short = None

        # ── Entry signals ────────────────────────────────────────────────────
        if trades_today >= max_trades:
            continue
        if zone_upper is None or zone_lower is None:
            continue

        zone_fresh = (i - zone_bar) <= zone_age
        use_zone   = zone_fresh if fresh_only else (zone_type is not None)

        prev = df_1m.iloc[i - 1]
        prev_green = float(prev["close"]) > float(prev["open"])
        prev_red   = float(prev["close"]) < float(prev["open"])
        curr_green = c > o
        curr_red   = c < o

        # LONG entry
        if (zone_type == "GREEN" and use_zone and
                c > zone_upper and
                prev_green and curr_green and
                (tracked_high is None or c <= tracked_high) and
                position != "LONG"):
            signal       = "BUY"
            signal_price = c
            signal_reason= f"GREEN_ZONE close>{zone_upper:.1f}"
            signal_bar   = i
            position     = "LONG"
            entry_price  = c
            atr_sl_long  = c - atr_val * atr_mult
            trades_today += 1

        # SHORT entry
        elif (zone_type == "RED" and use_zone and
                c < zone_lower and
                prev_red and curr_red and
                (tracked_low is None or c >= tracked_low) and
                position != "SHORT"):
            signal       = "SELL"
            signal_price = c
            signal_reason= f"RED_ZONE close<{zone_lower:.1f}"
            signal_bar   = i
            position     = "SHORT"
            entry_price  = c
            atr_sl_short = c + atr_val * atr_mult
            trades_today += 1

    return signal, signal_price, signal_reason, signal_bar, n


# ─────────────────────────────────────────────────────────────────────────────
# INTRADAY CANDLE FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_1m(symbol, tf="1m"):
    """Fetch intraday candles via yfinance."""
    ticker = SYMBOLS.get(symbol)
    if not ticker:
        return None
    period = "5d" if tf in ("5m", "15m", "30m") else "2d"
    try:
        df = yf.download(ticker, period=period, interval=tf,
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        if "datetime" in df.columns:
            df = df.rename(columns={"datetime": "time"})
        return df[["time", "open", "high", "low", "close"]].dropna().reset_index(drop=True)
    except Exception as e:
        log.error(f"{symbol} 1m fetch error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DHAN ORDER
# ─────────────────────────────────────────────────────────────────────────────

def place_order(symbol, side, qty, token, cid, mode, sec_id=None, seg=None, trad_sym=None, price=0.0):
    if not sec_id or not seg:
        info = DHAN_INFO.get(symbol)
        if not info:
            log.warning(f"{symbol} not in DHAN_INFO — skipping order")
            return
        sec_id, seg = info
        trad_sym = symbol

    ts = int(time.time())
    body = {
        "dhanClientId":    cid,
        "correlationId":   f"RANGE_{trad_sym}_{ts}",
        "transactionType": side,       # "BUY" or "SELL"
        "exchangeSegment": seg,
        "productType":     "INTRADAY",
        "orderType":       "MARKET",
        "validity":        "DAY",
        "securityId":      sec_id,
        "tradingSymbol":   trad_sym,
        "quantity":        qty,
        "price":           0,
        "triggerPrice":    0,
    }
    # For options: fetch actual option LTP from Dhan (index price is not useful here)
    if seg == "NSE_FNO" and sec_id:
        try:
            qr = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                               json={"NSE_FNO": [int(sec_id)]},
                               headers=hdrs(token, cid), timeout=4)
            if qr.status_code == 200:
                qdata = qr.json().get("data", {}).get("NSE_FNO", {})
                for v in (qdata.values() if isinstance(qdata, dict) else []):
                    ltp_v = float(v.get("last_price") or v.get("ltp") or 0)
                    if ltp_v:
                        price = ltp_v
                        break
        except Exception:
            pass  # fallback: use whatever price was passed

    if mode == "paper":
        log.info(f"[PAPER] {side} {qty} {trad_sym} @ {price:.2f}  correlationId=RANGE_{trad_sym}_{ts}")
        return
    try:
        r = requests.post(ORDERS_URL, json=body, headers=hdrs(token, cid), timeout=10)
        if r.status_code == 200:
            log.info(f"[LIVE] {side} {qty} {trad_sym} @ {price:.2f}  correlationId=RANGE_{trad_sym}_{ts}")
        else:
            log.error(f"ORDER FAIL {side} {trad_sym}  status={r.status_code}  body={r.text[:300]}")
            raise Exception(f"Dhan {r.status_code}: {r.text[:200]}")
    except requests.exceptions.RequestException as e:
        log.error(f"ORDER ERR  {trad_sym}: {e}")
        raise


# ─────────────────────────────────────────────────────────────────────────────
# PER-SYMBOL STATE (in-memory, reset daily)
# ─────────────────────────────────────────────────────────────────────────────

_state = {}   # symbol → {"position": None, "trades_today": 0}

def get_state(symbol):
    if symbol not in _state:
        _state[symbol] = {"position": None, "trades_today": 0, "last_signal": None, "opt_sec_id": None, "opt_trad_sym": None}
    return _state[symbol]

def reset_daily_state():
    for sym in list(_state.keys()):
        _state[sym] = {"position": None, "trades_today": 0, "last_signal": None, "opt_sec_id": None, "opt_trad_sym": None}


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def main(strategy_id="range"):
    mode = "live" if "--live" in sys.argv else "paper"
    log.info(f"Range Trader starting — mode={mode}")

    last_day        = None
    daily_levels    = {}   # symbol → [(price, type)]

    while True:
        now = ist_now()

        # Daily reset
        if now.date() != last_day:
            log.info("New trading day — resetting state & reloading daily levels")
            reset_daily_state()
            daily_levels = {}
            last_day = now.date()

        if not is_market_open():
            log.info("Market closed — sleeping 60s")
            time.sleep(60)
            continue

        # 3:15 PM auto-exit
        if is_exit_time():
            log.info("3:15 PM — signalling exit for all open positions")
            cfg = load_config(strategy_id)
            if cfg.get("active", True) and mode == "live":
                try:
                    token, cid = load_creds()
                    for sym, st in _state.items():
                        if st["position"] == "LONG":
                            if st.get("opt_sec_id"):
                                place_order(sym, "BUY", st.get("opt_qty", cfg.get("qty",1)), token, cid, mode, st["opt_sec_id"], "NSE_FNO", st["opt_trad_sym"])
                            else:
                                place_order(sym, "SELL", cfg.get("qty", 1), token, cid, mode)
                            st["position"] = None
                            st["opt_sec_id"] = None
                        elif st["position"] == "SHORT":
                            if st.get("opt_sec_id"):
                                place_order(sym, "BUY", st.get("opt_qty", cfg.get("qty",1)), token, cid, mode, st["opt_sec_id"], "NSE_FNO", st["opt_trad_sym"])
                            else:
                                place_order(sym, "BUY", cfg.get("qty", 1), token, cid, mode)
                            st["position"] = None
                            st["opt_sec_id"] = None
                        elif st["position"] == "SHORT":
                            place_order(sym, "BUY", cfg.get("qty", 1), token, cid, mode)
                            st["position"] = None
                except Exception as e:
                    log.error(f"Exit error: {e}")
            time.sleep(120)
            continue

        cfg = load_config(strategy_id)
        if not cfg.get("active", True):
            log.info("Strategy inactive (config) — sleeping 60s")
            time.sleep(60)
            continue

        try:
            token, cid = load_creds()
        except Exception as e:
            log.error(f"Creds load failed: {e} — sleeping 60s")
            time.sleep(60)
            continue

        # ── Strategy summary (log once per loop so config always visible) ──────
        tf         = cfg.get("timeframe", "1m")
        instrument = cfg.get("instrument", "equity").upper()
        qty        = cfg.get("qty", 1)
        max_tr     = cfg.get("max_trades_per_symbol", 2)
        fresh_only = cfg.get("use_fresh_zone_only", True)
        exit_atr   = cfg.get("exit_atr", True)
        exit_zone  = cfg.get("exit_zone", False)
        log.info(
            f"[CONFIG] TF={tf} | Instrument={instrument} | Qty={qty} | "
            f"MaxTrades/sym={max_tr} | FreshZoneOnly={fresh_only} | "
            f"Exit: ATR={'ON' if exit_atr else 'OFF'} Zone={'ON' if exit_zone else 'OFF'} | "
            f"Entry: zone touch + bearish/bullish candle + close break + 2-candle confirm"
        )

        symbols = cfg.get("symbols", ["NIFTY"])

        for symbol in symbols:
            st = get_state(symbol)
            if st["trades_today"] >= cfg.get("max_trades_per_symbol", 2):
                continue

            # Build daily levels once per day per symbol
            if symbol not in daily_levels:
                daily_df = fetch_daily(symbol)
                is_idx   = symbol in ("NIFTY", "BANKNIFTY")
                daily_levels[symbol] = build_key_levels(
                    daily_df, is_index=is_idx,
                    max_jump_pct=cfg.get("max_jump_pct", 50.0)
                )
                log.info(f"{symbol} levels: {len(daily_levels[symbol])} key levels loaded")

            levels = daily_levels.get(symbol, [])
            if not levels:
                continue

            tf    = cfg.get("timeframe", "1m")
            df_1m = fetch_1m(symbol, tf)
            if df_1m is None or len(df_1m) < 20:
                continue

            signal, price, reason, sig_bar, total_bars = run_signal_engine(df_1m, levels, cfg)

            # Entry sirf tab valid hai jab signal CURRENT candle (last bar) se aaya ho
            # Purana historical signal ignore karo — stale entry avoid karo
            if signal in ("BUY", "SELL") and (total_bars - sig_bar) > 2:
                log.debug(f"Skipping stale {signal} {symbol} — signal bar {sig_bar}, total {total_bars}")
                signal = None

            if signal in ("BUY", "SELL") and signal != st["last_signal"]:
                qty = cfg.get("qty", 1)
                inst = cfg.get("instrument", "equity")
                offset = int(cfg.get("strike_offset", 0))
                
                log.info(f"SIGNAL {signal} {symbol} @ {price:.2f}  reason={reason}")
                
                if inst == "options":
                    opt_type = "PE" if signal == "BUY" else "CE"
                    sec_id, t_sym, lot_sz = dhan_master.get_option_contract(symbol, price, opt_type, offset)
                    if sec_id:
                        actual_qty = qty * lot_sz
                        place_order(symbol, "SELL", actual_qty, token, cid, mode, sec_id, "NSE_FNO", t_sym, price=price)
                        st["opt_sec_id"] = sec_id
                        st["opt_trad_sym"] = t_sym
                        st["opt_qty"] = actual_qty
                    else:
                        log.error(f"Option contract not found for {symbol} {opt_type}")
                else:
                    place_order(symbol, signal, qty, token, cid, mode, price=price)

                st["trades_today"] += 1
                st["last_signal"]   = signal
                st["entry_price"]   = price
                st["position"]      = "LONG" if signal == "BUY" else "SHORT" 

            elif signal in ("EXIT_LONG", "EXIT_SHORT"):
                if st["position"] is None:
                    continue  # no open position this session — skip stale startup EXIT
                exit_side = "SELL" if signal == "EXIT_LONG" else "BUY"
                log.info(f"EXIT {symbol} via {reason} @ {price:.2f}")
                
                if st.get("opt_sec_id"):
                    place_order(symbol, "BUY", st.get("opt_qty", cfg.get("qty",1)), token, cid, mode, st["opt_sec_id"], "NSE_FNO", st["opt_trad_sym"])
                else:
                    place_order(symbol, exit_side, cfg.get("qty", 1), token, cid, mode)
                    
                st["position"]    = None
                st["last_signal"] = None
                st["opt_sec_id"] = None
                st["opt_trad_sym"] = None

        tf_secs = {"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800}.get(cfg.get("timeframe","1m"), 60)
        log.info(f"Loop done — sleeping {tf_secs}s")
        time.sleep(tf_secs)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--paper', action='store_true')
    parser.add_argument('--id', default='range')
    args = parser.parse_args()
    
    # Update log file to use variation ID, remove all existing handlers
    log_file = BASE_DIR / "logs" / f"{args.id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in log.handlers[:]:
        log.removeHandler(h)
    log.propagate = False
    fh = logging.FileHandler(log_file)
    fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s  %(message)s'))
    log.addHandler(fh)
    
    sys.argv = [sys.argv[0]] + (["--live"] if args.live else []) # for compatibility inside main
    main(strategy_id=args.id)
