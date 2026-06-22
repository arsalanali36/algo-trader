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
import os
import socket
import sys
# project root (parent of _TRADERS/) on path BEFORE importing root modules —
# this script is launched as a subprocess (sys.path[0] = _TRADERS/), so dhan_master
# at the project root is otherwise not importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dhan_master
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ── IPv4 Force — Dhan rejects IPv6 (DH-905) ──────────────────────────────────
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

# ── Paths ─────────────────────────────────────────────────────────────────────
# BASE_DIR = project root (parent of _TRADERS/) — creds, nifty_config.json and
# logs/ all live at the root, not inside _TRADERS/ (same as rsi_trader.py).
BASE_DIR    = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
CFG_FILE    = BASE_DIR / "range_config.json"
LOG_FILE    = BASE_DIR / "range_trader.log"

# ── Symbols ───────────────────────────────────────────────────────────────────
SYMBOLS = list({
    "NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "SBIN", "AXISBANK", "BAJFINANCE", "WIPRO", "KOTAKBANK", "LT", "MARUTI",
    "HINDUNILVR", "ITC", "ADANIENT", "SUNPHARMA", "TITAN", "ULTRACEMCO",
    "NESTLEIND", "POWERGRID", "NTPC", "ONGC", "ASIANPAINT", "BHARTIARTL",
    "HCLTECH", "BAJAJFINSV", "TATACONSUM", "COALINDIA", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HEROMOTOCO", "HINDALCO", "JSWSTEEL", "M&M",
    "SBILIFE", "SHRIRAMFIN", "TATASTEEL", "TECHM", "TRENT",
})

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

HIST_URL     = "https://api.dhan.co/v2/charts/historical"
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

def _dhan_info(symbol):
    """Sec_id + segment + instrument for a symbol from Dhan equity cache."""
    info = dhan_master.get_equity_info(symbol)
    if info:
        return info
    # fallback for indices: Dhan well-known sec_ids
    _IDX = {"NIFTY": ("13", "IDX_I", "INDEX"), "BANKNIFTY": ("25", "IDX_I", "INDEX")}
    return _IDX.get(symbol)

def fetch_daily(symbol, days=22):
    """Fetch daily OHLC from Dhan /v2/charts/historical."""
    from datetime import date as _date
    info = _dhan_info(symbol)
    if not info:
        log.error(f"fetch_daily: no Dhan info for {symbol}")
        return None
    sec_id, seg, inst = info
    today = _date.today()
    frm = (_date.fromordinal(today.toordinal() - max(days * 2, 60))).isoformat()
    token, cid = load_creds()
    try:
        r = requests.post(HIST_URL,
            json={"securityId": sec_id, "exchangeSegment": seg, "instrument": inst,
                  "expiryCode": 0, "fromDate": frm, "toDate": today.isoformat()},
            headers=hdrs(token, cid), timeout=10)
        if r.status_code != 200:
            log.error(f"fetch_daily {symbol}: HTTP {r.status_code} — {r.text[:200]}")
            return None
        d = r.json()
        ts  = d.get("start_Time") or d.get("timestamp") or []
        if not ts:
            log.error(f"fetch_daily {symbol}: empty response")
            return None
        df = pd.DataFrame({
            "date":  pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=5, minutes=30),
            "open":  d.get("open", []),
            "high":  d.get("high", []),
            "low":   d.get("low", []),
            "close": d.get("close", []),
        })
        df["date"] = df["date"].dt.date
        return df.tail(days).reset_index(drop=True)
    except Exception as e:
        log.error(f"fetch_daily {symbol} error: {e}")
        return None


# ── Patterns + zones now live in _CHARTING/ (shared, reusable module) ───────
# Re-exported here so existing callers (validate_strategy.py, run_signal_engine
# below) keep working unchanged. See _CHARTING/patterns.py + _CHARTING/zones.py
# for the actual implementations.
sys.path.insert(0, str(BASE_DIR))
from _CHARTING.zones import traditional_pivots, build_key_levels
from _CHARTING.patterns import (
    green_hammer, red_hammer, inv_red_hammer,
    bull_engulfing, bear_engulfing, bull_harami, bear_harami,
    is_bullish_pattern, is_bearish_pattern,
)

MIN_BODY_SIZE = 0.5    # minimum body in points (Pine minBodySize)
WICK_RATIO    = 2.5    # wick >= WICK_RATIO * body (Pine wickRatio=2.5)


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
    """Fetch intraday candles from Dhan /v2/charts/intraday."""
    from datetime import date as _date
    info = _dhan_info(symbol)
    if not info:
        log.error(f"fetch_1m: no Dhan info for {symbol}")
        return None
    sec_id, seg, inst = info
    today = _date.today()
    days_back = 5 if tf in ("5m", "15m", "30m") else 2
    frm = (_date.fromordinal(today.toordinal() - days_back)).isoformat()
    interval = _TF_MAP.get(tf, "1")
    token, cid = load_creds()
    try:
        r = requests.post(INTRADAY_URL,
            json={"securityId": sec_id, "exchangeSegment": seg, "instrument": inst,
                  "interval": interval, "fromDate": frm, "toDate": today.isoformat()},
            headers=hdrs(token, cid), timeout=10)
        if r.status_code != 200:
            log.error(f"fetch_1m {symbol}: HTTP {r.status_code} — {r.text[:200]}")
            return None
        d = r.json()
        ts = d.get("start_Time") or d.get("timestamp") or []
        if not ts:
            return None
        df = pd.DataFrame({
            "time":  pd.to_datetime(ts, unit="s") + pd.Timedelta(hours=5, minutes=30),
            "open":  d.get("open", []),
            "high":  d.get("high", []),
            "low":   d.get("low", []),
            "close": d.get("close", []),
        })
        # Sirf aaj ke bars rakho
        today_str = today.isoformat()
        df = df[df["time"].dt.strftime("%Y-%m-%d") == today_str].reset_index(drop=True)
        return df if not df.empty else None
    except Exception as e:
        log.error(f"fetch_1m {symbol} error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DHAN ORDER
# ─────────────────────────────────────────────────────────────────────────────

_STRAT_ID = "range"   # set by main() to the running variation's config key (trade DB tag)


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
    # For options: fetch actual option LTP from Dhan — index/spot price is WRONG here
    opt_ltp = None
    if seg == "NSE_FNO" and sec_id:
        for _attempt in range(2):
            try:
                qr = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                                   json={"NSE_FNO": [int(sec_id)]},
                                   headers=hdrs(token, cid), timeout=5)
                if qr.status_code == 429:
                    time.sleep(1.5)
                    continue
                if qr.status_code == 200:
                    qdata = qr.json().get("data", {}).get("NSE_FNO", {})
                    for v in (qdata.values() if isinstance(qdata, dict) else []):
                        ltp_v = float(v.get("last_price") or v.get("ltp") or 0)
                        if ltp_v:
                            opt_ltp = ltp_v
                    break
            except Exception:
                break
        if opt_ltp:
            price = opt_ltp
        else:
            log.warning(f"Option LTP fetch failed for {trad_sym} — logging price as 0 (NOT spot price)")
            price = 0.0  # 0 = unknown; spot price logged karna WRONG hoga

    def _rec(status_, oid=''):
        try:
            import sys as _s, os as _o
            _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
            if _root not in _s.path:
                _s.path.insert(0, _root)
            import order_store
            order_store.record(side, qty, price, source='strategy', strategy=_STRAT_ID,
                mode=mode, broker='dhan', symbol=symbol,
                instrument=('options' if seg == 'NSE_FNO' else 'equity'),
                trad_sym=trad_sym, sec_id=sec_id, segment=seg,
                correlation_id=f'RANGE_{trad_sym}_{ts}', broker_order_id=oid, status=status_)
        except Exception:
            pass

    if mode == "paper":
        log.info(f"[PAPER] {side} {qty} {trad_sym} @ {price:.2f}  correlationId=RANGE_{trad_sym}_{ts}")
        _rec('paper')
        return
    try:
        r = requests.post(ORDERS_URL, json=body, headers=hdrs(token, cid), timeout=10)
        if r.status_code == 200:
            log.info(f"[LIVE] {side} {qty} {trad_sym} @ {price:.2f}  correlationId=RANGE_{trad_sym}_{ts}")
            _rec('filled')
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
    global _STRAT_ID
    _STRAT_ID = strategy_id
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
                time.sleep(0.4)  # Dhan DH-904: 25 symbols @ 0.4s = ~10s startup, well under rate limit
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
            time.sleep(0.25)  # DH-904 guard: 25 symbols * 0.25s = ~6s per loop
            if df_1m is None or len(df_1m) < 20:
                continue

            signal, price, reason, sig_bar, total_bars = run_signal_engine(df_1m, levels, cfg)

            # Sirf last 2 bars ka signal valid hai — purana signal duplicate entry karega
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
                    try:
                        sec_id, t_sym, lot_sz = dhan_master.get_option_contract(symbol, price, opt_type, offset)
                    except (ValueError, TypeError) as _e:
                        log.error(f"get_option_contract failed for {symbol}: {_e} — skipping order, strategy continues")
                        continue
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
