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
import re
import socket
import sys
# project root (parent of _TRADERS/) on path BEFORE importing root modules —
# this script is launched as a subprocess (sys.path[0] = _TRADERS/), so dhan_master
# at the project root is otherwise not importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import _paths  # sys.path bootstrap — adds _core/_data/_ops so flat imports below resolve after refactor
import dhan_master
import dhan_rate_limiter as _rl
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


def _ensure_root_path():
    """Idempotent safety net so root-level modules (shared_ltp_cache, universe,
    order_store) stay importable from inside functions. Root is already inserted
    at import time above; this just re-adds it if something cleared sys.path."""
    _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _root not in sys.path:
        sys.path.insert(0, _root)


_RT_ONCE = {}
def _rt_once(key):
    """True only the first time `key` is seen on the current IST day (resets next
    day). Keeps repeated [WHITELIST]/[MAX-TRADES] log lines from spamming when
    the scan loop re-hits the same symbol every cycle."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    today = (_dt.now(_tz.utc) + _td(hours=5, minutes=30)).strftime("%Y-%m-%d")
    if _RT_ONCE.get(key) == today:
        return False
    _RT_ONCE[key] = today
    return True

# ── Paths ─────────────────────────────────────────────────────────────────────
# BASE_DIR = project root (parent of _TRADERS/) — creds, nifty_config.json and
# logs/ all live at the root, not inside _TRADERS/ (same as rsi_trader.py).
BASE_DIR    = Path(__file__).resolve().parent.parent.parent
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

def _exit_times():
    """(squareoff_hm, no_entry_hm) — RMS single-source (risk_gate.exit_time_config,
    editable in RMS tab). Fallback AUTO_EXIT_AT if RMS unreadable."""
    try:
        import risk_gate as _rg
        return _rg.exit_time_config()
    except Exception:
        return AUTO_EXIT_AT, AUTO_EXIT_AT

def is_exit_time():
    t = (ist_now().hour, ist_now().minute)
    return t >= _exit_times()[0]

def is_no_entry_time():
    """No new entry at/after RMS no_entry_after time — stops the '3:15 ke baad
    entry -> turant squareoff -> ₹50 zabardasti tax' bug (entry hi na ho)."""
    t = (ist_now().hour, ist_now().minute)
    return t >= _exit_times()[1]

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
        _rl.acquire("candle")
        r = requests.post(HIST_URL,
            json={"securityId": sec_id, "exchangeSegment": seg, "instrument": inst,
                  "expiryCode": 0, "fromDate": frm, "toDate": today.isoformat()},
            headers=hdrs(token, cid), timeout=10)
        if r.status_code == 429:
            _rl.note_429()
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
# ATR ka SINGLE source of truth (Rule 6B / ADR-002) — wahi Wilder RMA formula
# (alpha=1/period) jo pehle yahin inline thi; alias se existing callers unchanged.
from _CHARTING.indicators import wilder_atr as compute_atr

MIN_BODY_SIZE = 0.5    # minimum body in points (Pine minBodySize)
WICK_RATIO    = 2.5    # wick >= WICK_RATIO * body (Pine wickRatio=2.5)


# ─────────────────────────────────────────────────────────────────────────────
# ATR — compute_atr = wilder_atr (imported above from _CHARTING/indicators.py,
# Task 2 consolidation 2026-07-07; formula bit-for-bit wahi hai jo yahan inline thi)
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# INTRADAY SIGNAL ENGINE — runs bar by bar on 1-min data
# ─────────────────────────────────────────────────────────────────────────────

def run_signal_engine(df_1m, key_levels, cfg, trades_out=None, atr_series=None):
    """Bar-by-bar zone detection + entry/exit. THE engine — live and backtest.

    Returns (signal, price, reason, signal_bar, n_bars, state) where signal is
    the LAST one found, however old; `signal_bar`/`n_bars` are what a caller
    uses to tell fresh from stale (the live loop drops anything >2 bars old).
    Pass a list as `trades_out` to also collect EVERY entry/exit as a dict —
    default None keeps the live path byte-for-byte as it was.

    ── 2026-07-17: this body came from validate_strategy.backtest_day ──
    There were two engines. This one placed the orders; `backtest_day` — whose
    docstring literally read "Mirror of run_signal_engine but COLLECTS every
    trade" — only ran in tests. They were never re-synced, and every fidelity
    fix from the Pine-matching work (the one that reached 90.2%) landed in the
    mirror. Measured against the user's own TV export, Jan-Jun 2026:

        backtest_day  75.3% of TV's trades      <- all the fixes
        this engine   49.5%                     <- none of them, and it traded

    Six ways they had drifted, every one in the mirror's favour:
      1. max_candle_size gated ZONE FORMATION here at a hardcoded 25 (config
         thrown away); the Pine gates the ENTRY's breakout candle. (fixed
         earlier the same day, before the rest of this was known)
      2. zones formed off the PERSISTENT `touch_active` flag, so any candle
         after a touch could make a zone — the Pine only forms one on the bar
         that touches. This alone made 605 zones against TV's 430 on the same
         104 days.
      3. touch detection took the FIRST level in the list (`break`); the Pine
         locks RESISTANCE priority ("selectedLine", a finding of the 90.2%
         work).
      4. tracked_high/low reset on zone formation; the Pine accumulates them
         across the day. And the tracked_high ENTRY filter was applied here —
         the mirror deliberately drops it, with a comment saying removing it
         matched TV ~5% better.
      5. the Not_on_Red_line / Not_on_Gren_line check sat on zone formation;
         the Pine puts it on the ENTRY (line 1223/1234).
      6. `is_bullish_pattern`/`is_bearish_pattern` omit harami — the mirror ORs
         bull_harami/bear_harami in. The Pine has them; the 90.2% writeup names
         them as a fix. Patterns are checked inline here now, so the omission
         cannot come back through the shared wrapper.

    Where the zones DID agree they agreed to the decimal (TV 11:30
    RED[26316.8-26335.3] == this engine's) — the pattern maths, pivots and zone
    prices were never the problem. Only the gates were, and they lived in the
    file that doesn't trade.

    `backtest_day` is now a thin wrapper over this function (it still owns the
    TradingView next-bar fill convention — that is a backtest concern, not a
    live one). One engine. Fixing it once fixes both.

    Unused knobs, deliberately: `hawa_me_zone` is read from config by nobody —
    it was dead here and absent from the mirror.
    """
    n = len(df_1m) if df_1m is not None else 0
    if df_1m is None or n < 20 or not key_levels:
        # 6-tuple, not 3. This guard used to return three values while the
        # normal path returned six — the same function with two arities. Only
        # the live loop's own len<20 check kept it from ever biting.
        return None, None, None, -1, n, {}

    max_cs     = cfg.get("max_candle_size", 25)
    fresh_only = cfg.get("use_fresh_zone_only", True)
    exit_atr   = cfg.get("exit_atr", True)
    exit_zone  = cfg.get("exit_zone", False)
    max_trades = cfg.get("max_trades_per_symbol", 2)

    atr_len  = 14      # fixed — not exposed in config
    atr_mult = 2.0     # fixed
    zone_age = 2       # fixed (Pine maxZoneAge)

    # ATR must be WARM. Live gets that from the multi-day window it is handed;
    # a per-day backtest caller has to pass a continuous pre-warmed series
    # instead, or ATR(14) restarts cold every morning and the first ~14 bars
    # trail off a garbage stop. Same series either way — just two ways in.
    if atr_series is None:
        atr_series = compute_atr(df_1m, atr_len)
    else:
        atr_series = atr_series.reset_index(drop=True)

    zone_upper = zone_lower = zone_type = None
    zone_bar = -999
    zones_history = []           # every zone formed (chart display)
    tracked_high = tracked_low = None
    touch_active = False
    active_touch_type = None
    atr_sl_long = atr_sl_short = None
    position = None
    entry_price = None

    signal = signal_price = signal_reason = None
    signal_bar = -1
    trades_today = 0
    cur_day = None

    def _emit(kind, i, price, reason):
        if trades_out is not None:
            trades_out.append(dict(time=df_1m.iloc[i]["time"], kind=kind,
                                   price=price, reason=reason, bar=i))

    for i in range(2, n):
        row = df_1m.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        atr_val = float(atr_series.iloc[i]) if not np.isnan(atr_series.iloc[i]) else 5.0

        # ── New day: reset day-scoped state, mirroring the Pine 1:1 —
        #       var int tradesToday = 0
        #       if ta.change(time("D")) != 0
        #           tradesToday := 0
        # `trades_today` used to say "today" while counting every entry in
        # whatever window df_1m spanned (5 days live) — so the 2-trade cap was
        # spent days ago and real signals were dropped for a week. The mirror
        # never had this bug because it is called one day at a time.
        # position/atr_sl reset with it (the Pine flattens daily at 3:15).
        # ATR stays continuous across days — that is what the extra days are FOR.
        bar_day = row["time"].date()
        if bar_day != cur_day:
            cur_day = bar_day
            trades_today = 0
            position = entry_price = None
            atr_sl_long = atr_sl_short = None
            if zone_upper is not None:      # shelve before wiping — see below
                zones_history.append([zone_bar, i - 1, zone_upper, zone_lower, zone_type])
            zone_upper = zone_lower = zone_type = None
            zone_bar = -999
            tracked_high = tracked_low = None
            touch_active = False
            active_touch_type = None

        # ── 3:15 daily square-off (Pine). Configurable, never hardcoded:
        # the live squareoff/no-entry time has a single source (risk_gate's RMS
        # exit_time_config) and the loop passes it down; 15:15 is only the
        # fallback for a backtest caller that doesn't care.
        _eh, _em = cfg.get("exit_hm", (15, 15))
        _past_exit = (row["time"].hour, row["time"].minute) >= (_eh, _em)
        if position is not None and _past_exit:
            signal = "EXIT_LONG" if position == "LONG" else "EXIT_SHORT"
            signal_price, signal_reason, signal_bar = c, "3:15 Daily Exit", i
            _emit(signal, i, c, signal_reason)
            position = entry_price = None
            atr_sl_long = atr_sl_short = None
            continue

        # ── Touch detection — Pine selectedLine: RESISTANCE priority (locks),
        # last resistance wins; else last support/CP touched. NOT first-match.
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

        # tracked high/low — the Pine accumulates over ALL touch bars in the day
        # (touchActive never resets) and does NOT reset on zone formation.
        if touched_type is not None:
            touch_active = True
            active_touch_type = touched_type      # Pine lineType — persists till next touch
            if tracked_high is None:
                tracked_high, tracked_low = h, l
            else:
                tracked_high = max(tracked_high, h)
                tracked_low  = min(tracked_low, l)

        # ── Zone formation — Pine uses the CURRENT bar's touch, not the
        # persistent flag. Patterns inline (harami included, see docstring).
        po, ph, pl, pc = (float(df_1m.iloc[i - 1]["open"]), float(df_1m.iloc[i - 1]["high"]),
                          float(df_1m.iloc[i - 1]["low"]), float(df_1m.iloc[i - 1]["close"]))
        bullish = (green_hammer(o, h, l, c)
                   or bull_engulfing(po, ph, pl, pc, o, h, l, c)
                   or bull_harami(po, ph, pl, pc, o, h, l, c))
        bearish = (red_hammer(o, h, l, c)
                   or inv_red_hammer(o, h, l, c)
                   or bear_engulfing(po, ph, pl, pc, o, h, l, c)
                   or bear_harami(po, ph, pl, pc, o, h, l, c))

        if touched_type is not None:
            not_red = touched_type not in ("RESISTANCE", "PD_H")
            not_grn = touched_type not in ("SUPPORT", "PD_L")
            if bearish and not_grn:
                if zone_upper is not None:
                    zones_history.append([zone_bar, i - 1, zone_upper, zone_lower, zone_type])
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "RED", i
            elif bullish and not_red:
                if zone_upper is not None:
                    zones_history.append([zone_bar, i - 1, zone_upper, zone_lower, zone_type])
                zone_upper, zone_lower, zone_type, zone_bar = h, l, "GREEN", i

        zfresh = zone_type is not None and (i - zone_bar) <= zone_age

        # ── Exits — Pine priority: ATR first, else ZONE (MainExit)
        if position == "LONG":
            if atr_sl_long is not None:
                atr_sl_long = max(atr_sl_long, c - atr_val * atr_mult)
            if exit_atr and atr_sl_long is not None and c < atr_sl_long:
                signal, signal_price, signal_reason, signal_bar = "EXIT_LONG", c, "ATR_TRAILING", i
                position, atr_sl_long = None, None
                _emit("EXIT_LONG", i, c, signal_reason)
            elif exit_zone and zfresh and zone_type == "RED" and c < zone_lower and c < o:
                signal, signal_price, signal_reason, signal_bar = "EXIT_LONG", c, "ZONE_LONG", i
                position, atr_sl_long = None, None
                _emit("EXIT_LONG", i, c, signal_reason)
        elif position == "SHORT":
            if atr_sl_short is not None:
                atr_sl_short = min(atr_sl_short, c + atr_val * atr_mult)
            if exit_atr and atr_sl_short is not None and c > atr_sl_short:
                signal, signal_price, signal_reason, signal_bar = "EXIT_SHORT", c, "ATR_TRAILING", i
                position, atr_sl_short = None, None
                _emit("EXIT_SHORT", i, c, signal_reason)
            elif exit_zone and zfresh and zone_type == "GREEN" and c > zone_upper and c > o:
                signal, signal_price, signal_reason, signal_bar = "EXIT_SHORT", c, "ZONE_SHORT", i
                position, atr_sl_short = None, None
                _emit("EXIT_SHORT", i, c, signal_reason)

        # ── Entries
        if trades_today >= max_trades or zone_upper is None:
            continue
        # No new entry at/after the square-off time — it would be closed minutes
        # later, paying entry+exit brokerage for nothing. NOTE this is OURS, not
        # the Pine's: TV genuinely does enter at 15:20 and exit at 15:25 (4 such
        # trades in the Jan-Jun export). Those will always read as TV-only
        # mismatches, and that is the correct trade to make.
        if _past_exit:
            continue
        use_zone = zfresh if fresh_only else (zone_type is not None)

        prev_green = pc > po
        prev_red   = pc < po
        curr_green = c > o
        curr_red   = c < o

        # Not_on_Red_line / Not_on_Gren_line on the CURRENT bar's lineType
        # (persistent selectedLine type) — the Pine checks this at ENTRY.
        not_red_line = active_touch_type not in ("RESISTANCE", "PD_H") if active_touch_type else True
        not_grn_line = active_touch_type not in ("SUPPORT", "PD_L") if active_touch_type else True

        # The Pine gates the entry on the BREAKOUT candle's size:
        #   if close_Above_Green_zone and candleSize > maxCandleSize
        #       skipBigCandleZoneExit_Long := true
        #   Index_Long_Signal = (... and not skipBigCandleZoneExit_Long)
        big_candle = (h - l) > max_cs

        # Pine's longBelowTrackedHigh/shortAboveTrackedLow filter is NOT applied
        # — its trackedHigh semantics (touchActive never resets) over-block in
        # practice, and dropping it measured ~5% closer to TV.
        long_ok = (zone_type == "GREEN" and use_zone and c > zone_upper and
                   prev_green and curr_green and not big_candle and not_red_line)
        short_ok = (zone_type == "RED" and use_zone and c < zone_lower and
                    prev_red and curr_red and not big_candle and not_grn_line)

        if long_ok and position != "LONG":
            signal, signal_price, signal_bar = "BUY", c, i
            signal_reason = f"GREEN_ZONE close>{zone_upper:.1f}"
            position, entry_price = "LONG", c
            atr_sl_long = c - atr_val * atr_mult
            # Shelve before clearing: the zone that CAUSED this entry is the one
            # most worth seeing on a chart, and it used to vanish here without
            # ever reaching zones_history.
            zones_history.append([zone_bar, i, zone_upper, zone_lower, zone_type])
            zone_type = zone_upper = zone_lower = None   # Pine: Green_Zone := false
            trades_today += 1
            _emit("ENTRY_LONG", i, c, signal_reason)
        elif short_ok and position != "SHORT":
            signal, signal_price, signal_bar = "SELL", c, i
            signal_reason = f"RED_ZONE close<{zone_lower:.1f}"
            position, entry_price = "SHORT", c
            atr_sl_short = c + atr_val * atr_mult
            # Shelve before clearing: the zone that CAUSED this entry is the one
            # most worth seeing on a chart, and it used to vanish here without
            # ever reaching zones_history.
            zones_history.append([zone_bar, i, zone_upper, zone_lower, zone_type])
            zone_type = zone_upper = zone_lower = None   # Pine: Red_Zone := false
            trades_today += 1
            _emit("ENTRY_SHORT", i, c, signal_reason)

    if zone_upper is not None:
        zones_history.append([zone_bar, n - 1, zone_upper, zone_lower, zone_type])

    current_state = {
        "touch_active": touch_active,
        "active_touch_type": active_touch_type,
        "zone_type": zone_type,
        "zone_fresh": (n - 1 - zone_bar) <= zone_age if zone_bar != -999 else False,
        "zone_upper": zone_upper,
        "zone_lower": zone_lower,
        "zone_bar": zone_bar if zone_bar != -999 else None,
        "zones_history": zones_history,
        "tracked_high": tracked_high,
        "tracked_low": tracked_low,
        "ltp": float(df_1m.iloc[-1]["close"]) if n > 0 else 0,
    }
    return signal, signal_price, signal_reason, signal_bar, n, current_state

# ─────────────────────────────────────────────────────────────────────────────
# INTRADAY CANDLE FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_1m(symbol, tf="1m"):
    """Fetch intraday candles from Dhan /v2/charts/intraday.
    DH-904 (rate limit) — retry with backoff instead of giving up immediately;
    a single 429 shouldn't drop the symbol for the whole loop, and NOT backing
    off here is exactly what cascades into the next symbol's 429 too."""
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

    # Cross-process cache first — range_trader + rsi_trader (+ any future
    # strategy) on the same symbol within the TTL reuse one fetch instead of
    # each hitting Dhan independently. This is what actually fixes DH-904 —
    # the account-wide rate cap was never the real bottleneck, duplicate
    # candle fetches across processes were. See shared_candle_cache.py.
    try:
        import shared_candle_cache
        cached = shared_candle_cache.get(sec_id, interval, days_back, max_age=20.0)
        if cached:
            df = pd.DataFrame(cached)
            df["time"] = pd.to_datetime(df["time"])
            return df if not df.empty else None
    except Exception:
        pass

    token, cid = load_creds()
    try:
        r = None
        for _attempt in range(3):
            if not _rl.acquire("candle"):
                # gate saturated / 429 cooldown — posting anyway would just
                # extend the account-wide cooldown for everyone. Skip; caller
                # treats None like any transient fetch failure.
                log.warning(f"fetch_1m {symbol}: rate-gate busy, skipping this cycle")
                return None
            r = requests.post(INTRADAY_URL,
                json={"securityId": sec_id, "exchangeSegment": seg, "instrument": inst,
                      "interval": interval, "fromDate": frm, "toDate": today.isoformat()},
                headers=hdrs(token, cid), timeout=10)
            if r.status_code != 429:
                break
            _rl.note_429()
            wait = 2.0 * (_attempt + 1)  # 2s, 4s, 6s
            log.warning(f"fetch_1m {symbol}: DH-904 rate limit, backing off {wait}s (attempt {_attempt+1}/3)")
            time.sleep(wait)
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
        # Keep the whole `days_back` window — the earlier days are WARM-UP.
        #
        # This used to cut everything but today, which starved the engine of
        # exactly what it needs: ATR(14) and zones both start cold at 09:15,
        # and run_signal_engine returns nothing at all until 20 bars exist —
        # on 5m that's ~10:55, so half the session was blind (TRAP #85). Every
        # mission strategy already fetches days=5..10 for this reason and
        # slices today afterwards.
        #
        # Safe now, and only now: the engine resets its day-scoped state on
        # each date change (see run_signal_engine), so the extra days warm the
        # indicators and cannot leak trades, positions or the daily cap into
        # today. That is also precisely what the backtest does — continuous
        # ATR (atr_all) + per-day state (backtest_day) — so live and backtest
        # finally agree by construction rather than by luck.
        if df.empty:
            return None
        try:
            import shared_candle_cache
            cache_df = df.copy()
            cache_df["time"] = cache_df["time"].astype(str)
            shared_candle_cache.put(sec_id, interval, days_back, cache_df.to_dict("records"))
        except Exception:
            pass
        return df
    except Exception as e:
        log.error(f"fetch_1m {symbol} error: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# DHAN ORDER
# ─────────────────────────────────────────────────────────────────────────────

_STRAT_ID = "range"   # set by main() to the running variation's config key (trade DB tag)


def _fetch_premium(sec_id, token, cid):
    """Real option premium for sec_id — shared cross-process cache -> direct
    Dhan call (DH-904 backoff) -> wider stale cache. Returns None if nothing
    real is available (caller must never fall back to ₹0 — LESSONS.md TRAP #1)."""
    _ensure_root_path()
    opt_ltp = None
    try:
        import shared_ltp_cache
        opt_ltp = shared_ltp_cache.get(sec_id, max_age=15)
    except Exception:
        opt_ltp = None
    if opt_ltp:
        return opt_ltp
    for _attempt in range(3):
        try:
            _rl.acquire("ltp")
            qr = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                               json={"NSE_FNO": [int(sec_id)]},
                               headers=hdrs(token, cid), timeout=5)
            if qr.status_code == 429:
                _rl.note_429()
                time.sleep(1.0 * (_attempt + 1))
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
        try:
            import shared_ltp_cache
            shared_ltp_cache.put(sec_id, opt_ltp)
        except Exception:
            pass
        return opt_ltp
    try:
        import shared_ltp_cache
        return shared_ltp_cache.get_stale(sec_id, max_age=120)
    except Exception:
        return None


def place_order(symbol, side, qty, token, cid, mode, sec_id=None, seg=None, trad_sym=None, price=0.0, extra_tags=None, group_id="", broker_override=None, product=None, is_exit=False):
    """Thin file-local wrapper — asli kaam ab execution_gateway mein (Task 3,
    Rule 6B / ADR-001). Entry-side gate_entry() is file mein place_order se
    PEHLE alag se chalta hai (SELL+hedge pairing ki wajah se), isliye yahan
    gate=False — RMS double-gate nahi hota, aur hedge BUY (protective leg)
    kabhi RMS se block ho kar naked SELL nahi chhodta. Exits ko gateway ka
    strategy-aware fresh flat-check free milta hai (Task 5)."""
    if not sec_id or not seg:
        info = DHAN_INFO.get(symbol)
        if not info:
            log.warning(f"{symbol} not in DHAN_INFO — skipping order")
            return False
        sec_id, seg = info
        trad_sym = symbol

    try:
        import sys as _s, os as _o
        _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
        if _root not in _s.path:
            _s.path.insert(0, _root)
        import execution_gateway as gw

        _tag = "ENTRY" if side == "BUY" else "EXIT"   # exactly purana tag logic
        if is_exit:
            res = gw.execute_exit(
                _STRAT_ID, symbol, sec_id, trad_sym, qty, exit_side=side,
                seg=seg, mode=mode, broker_name=broker_override, tag=_tag,
                source="strategy", extra_tags=extra_tags, group_id=group_id,
                product=product, log=log.info)
        else:
            res = gw.execute_signal(
                _STRAT_ID, symbol, side, qty, 1, sec_id, trad_sym,
                seg=seg, mode=mode, broker_name=broker_override, tag=_tag,
                source="strategy", extra_tags=extra_tags, group_id=group_id,
                product=product, gate=False, log=log.info)
        return res.get("ok", False)
    except Exception as e:
        log.error(f"ORDER ERR  {trad_sym}: {e}")
        return False


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


def _recover_state_from_order_store(strategy_id):
    """Rebuild in-memory _state from today's open order_store positions on
    process startup — a restart used to reset every symbol's position to
    None, which the EXIT_LONG/EXIT_SHORT handler reads as "no open position
    this session" and silently skips (the 2026-06-17 startup-EXIT guard,
    needed to stop fake exits from stale historical data on a truly fresh
    start, but it can't tell a fresh start apart from a restart that has
    real open positions). Found live 2026-06-29 — 4 ARS_CHAIN_V1 positions
    orphaned from their own strategy's exit logic after repeated restarts
    (still protected by the separate, order_store-driven pos_monitor_loop
    SL/EOD-squareoff in trader_dashboard.py, but the strategy's own zone/
    ATR exit could never fire for them again). See LESSONS.md TRAP #28.
    Best-effort: any failure here just leaves _state at its fresh-empty
    default — never blocks startup."""
    try:
        import order_store
        from datetime import datetime as _dt, timedelta as _td
        ist = _dt.now(timezone.utc) + _td(hours=5, minutes=30)
        data = order_store.trades_for(ist.strftime("%Y-%m-%d"))
        all_today = (data.get("open") or []) + (data.get("closed") or [])
        recovered = 0
        for p in (data.get("open") or []):
            if "CAPITAL_BLOCKED" in (p.get("tags") or []):
                continue  # never actually placed at the broker — not a real position (found live 2026-07-03)
            if p.get("strategy") != strategy_id or p.get("entry") != "SELL":
                continue  # hedge BUY legs / other strategies — skip
            symbol = p.get("symbol")
            trad_sym = p.get("sym", "")
            if not symbol or not trad_sym:
                continue
            opt_type = "PE" if trad_sym.endswith("-PE") else ("CE" if trad_sym.endswith("-CE") else None)
            if opt_type is None:
                continue
            st = get_state(symbol)
            st["position"]      = "LONG" if opt_type == "PE" else "SHORT"
            st["last_signal"]   = "BUY" if opt_type == "PE" else "SELL"
            st["entry_price"]   = p.get("entry_price")
            st["opt_sec_id"]    = p.get("sec_id")
            st["opt_trad_sym"]  = trad_sym
            st["opt_qty"]       = p.get("qty")
            st["trades_today"]  = sum(1 for q in all_today
                                       if q.get("strategy") == strategy_id and q.get("symbol") == symbol
                                       and q.get("entry") == "SELL")
            recovered += 1
        if recovered:
            log.info(f"[RECOVER] re-attached {recovered} open position(s) from order_store — "
                     f"exit logic will resume for them this session")
    except Exception as e:
        log.warning(f"[RECOVER] state recovery failed (continuing with fresh state): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────────────────────────────

def main(strategy_id="range"):
    global _STRAT_ID
    _STRAT_ID = strategy_id
    mode = "live" if "--live" in sys.argv else "paper"
    log.info(f"Range Trader starting — mode={mode}")
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    _recover_state_from_order_store(strategy_id)

    # TRAP #65: this process only ever called dhan_feed.add() to subscribe
    # contracts, never dhan_feed.start() — add() just queues an instrument
    # if the feed's background thread isn't already running (_running stays
    # False forever), so the WebSocket connection here never actually
    # started. dhan_feed.LIVE stayed permanently empty in this process,
    # which meant strategy_safety.check_contract_liquidity()'s primary data
    # source was always empty, forcing every single check onto the REST
    # fallback (which itself is best-effort/rate-limited) and explaining why
    # the "any 2-of-3" liquidity gate never actually gated anything today —
    # it was fail-open on missing data on every single call. Starting the
    # feed here (once, with an empty instrument list — same pattern as
    # trader_dashboard.py's _ensure_feed_started) fixes it at the source.
    # TRAP #65: Proactive underlying feed subscription at startup (Fixed by Antigravity AI).
    try:
        jwt_token, client_id = load_creds()
        cfg = load_config(strategy_id)
        symbols = cfg.get("symbols", ["NIFTY"])
        if isinstance(symbols, str):
            import re
            symbols = [s.strip().upper() for s in re.split(r"[,\s]+", symbols) if s.strip()]
        
        initial_subs = []
        for sym in symbols:
            info = _dhan_info(sym)
            if info:
                sec_id, seg, _inst = info
                initial_subs.append((seg, sec_id))
        
        import dhan_feed
        dhan_feed.start({"client_id": client_id, "jwt_token": jwt_token}, initial_subs)
        log.info(f"dhan_feed started with {len(initial_subs)} proactive underlying subscriptions (Fixed by Antigravity AI) — live market-depth now available for liquidity checks")
    except Exception as e:
        log.warning(f"dhan_feed start failed (liquidity checks will keep using REST fallback only): {e}")

    # Seed last_day to TODAY (not None) — _recover_state_from_order_store()
    # above must never be immediately undone by the loop's own "new day" check
    # below. With last_day=None, the very first iteration always sees
    # now.date() != last_day and calls reset_daily_state(), which wipes every
    # _state entry (including what recovery JUST populated) right back to
    # flat, one line later. Found live 2026-07-02 via VPS log: 2026-07-01
    # 11:24:29 "[RECOVER] re-attached 1 open position" was immediately
    # followed by "New trading day — resetting state & reloading daily
    # levels" — TRAP #28's fix had been silently undoing itself since it
    # shipped 2026-06-29. Zero open positions at the time this was found+
    # fixed (confirmed via order_store before restarting).
    last_day        = ist_now().date()
    daily_levels    = {}   # symbol → [(price, type)]
    # Symbols Dhan can't resolve AT ALL (not in the scrip master — e.g. a
    # delisted/demerged name still sitting in someone's config). Retrying those
    # is pointless: the level-build below retries a failed fetch every loop, and
    # without this set a permanently-dead symbol would retry forever AND ring the
    # bell forever. Transient failures (a 401 burst) are NOT in here — they must
    # keep retrying. Cleared on the day rollover with daily_levels.
    dead_syms       = set()
    pivot_labels    = {}   # symbol → {rounded_price: specific label, e.g. "R3"/"S1"/"P"/"PDH"} — display-only, never used in entry logic

    while True:
        now = ist_now()

        # Daily reset
        if now.date() != last_day:
            dead_syms = set()
            log.info("New trading day — resetting state & reloading daily levels")
            reset_daily_state()
            daily_levels = {}
            pivot_labels = {}
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
                        if st["position"] not in ("LONG", "SHORT"):
                            continue
                        # P6 audit fix (2026-07-02): this loop placed exit orders
                        # unconditionally — no fresh broker flat-check, unlike the
                        # strategy's own signal-driven exit path (TRAP #73, above).
                        # 3:15 EOD force-exit races pos_monitor_loop's own EOD_315
                        # squareoff (a SEPARATE process) — without this check, a
                        # position pos_monitor already closed a moment earlier would
                        # get a duplicate exit order here, opening a phantom
                        # opposite position instead of doing nothing.
                        if mode == "live":
                            try:
                                import broker_sync as _bs
                                _bn      = (cfg.get("broker") or "dhan")
                                _chk_sid = str(st.get("opt_sec_id") or "")
                                _chk_sym = st.get("opt_trad_sym") or sym
                                if _bs.is_flat_fresh(_bn.lower(), _chk_sym, _chk_sid):
                                    log.info(f"[FLAT-CHECK] {sym} already flat at broker "
                                             f"(EOD squareoff won the race elsewhere) — "
                                             f"skipping 3:15 exit order; clearing state.")
                                    st["position"]     = None
                                    st["opt_sec_id"]   = None
                                    st["opt_trad_sym"] = None
                                    continue
                            except Exception as _fe:
                                log.warning(f"[FLAT-CHECK] 3:15 pre-exit check failed "
                                            f"({_fe}) — proceeding with exit (fail-open)")
                        if st["position"] == "LONG":
                            if st.get("opt_sec_id"):
                                place_order(sym, "BUY", st.get("opt_qty", cfg.get("qty",1)), token, cid, mode, st["opt_sec_id"], "NSE_FNO", st["opt_trad_sym"], is_exit=True)
                            else:
                                place_order(sym, "SELL", cfg.get("qty", 1), token, cid, mode, is_exit=True)
                        else:   # SHORT
                            if st.get("opt_sec_id"):
                                place_order(sym, "BUY", st.get("opt_qty", cfg.get("qty",1)), token, cid, mode, st["opt_sec_id"], "NSE_FNO", st["opt_trad_sym"], is_exit=True)
                            else:
                                place_order(sym, "BUY", cfg.get("qty", 1), token, cid, mode, is_exit=True)
                        st["position"] = None
                        st["opt_sec_id"] = None
                except Exception as e:
                    log.error(f"Exit error: {e}")
            time.sleep(120)
            continue

        _watchlist_state = {}

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

        # Fetch today's open positions from order_store to re-validate in-memory state (TRAP #62)
        open_legs_in_store = None
        try:
            import order_store
            open_legs_in_store = order_store.trades_for(now.strftime("%Y-%m-%d")).get("open")
        except Exception as _se:
            log.warning(f"[SYNC] order_store query failed: {_se}")

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

        # cfg["symbols"] is sometimes saved as a comma-string, not a JSON list
        # (e.g. "NIFTY,BANKNIFTY,...") — iterating a string directly walks it
        # CHARACTER BY CHARACTER ("N","I","F",...), which then all fail the
        # whitelist filter below and silently leaves ZERO symbols to trade.
        # health_check.py already had this exact defensive parse; range_trader
        # never got the same fix — found 2026-06-28 while it was live-running.
        symbols = cfg.get("symbols", ["NIFTY"])
        if isinstance(symbols, str):
            symbols = [s for s in re.split(r"[,\s]+", symbols) if s]
        # Liquid stock whitelist (item B) — only trade the user's hand-picked
        # liquid F&O stocks (avoids slippage / wide bid-ask on thin chains like
        # TCS/HINDUNILVR). Indices always pass. cfg["stock_whitelist"]: custom
        # list, or "all" to disable. Also shrinks the per-loop scan → fewer Dhan
        # candle/LTP calls (DH-904 relief).
        # Skipped entirely when the live any-2-of-3 liquidity filter (2026-06-28,
        # strategy_safety.check_contract_liquidity, runs per-contract at entry)
        # is ON for this strategy — that filter was BUILT to replace this rigid
        # static 21-name list with a wider real-time check; leaving this static
        # filter active too just re-imposed the old narrow universe underneath
        # it, silently dropping symbols (e.g. 18/25) before they ever reached
        # the new check. Found 2026-06-29 while live.
        try:
            import risk_gate as _rg_wl
            _live_filter_on = _rg_wl.liquidity_filter_enabled(strategy_id)
        except Exception:
            _live_filter_on = False
        _wl = cfg.get("stock_whitelist")
        if _wl != "all" and not _live_filter_on:
            try:
                _ensure_root_path()
                import universe as _u
                _allowed = set(_wl) if (isinstance(_wl, list) and _wl) else set(_u.LIQUID_PREMIUM)
            except Exception:
                _allowed = None
            if _allowed is not None:
                _IDX = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
                _kept = [s for s in symbols if s in _IDX or s in _allowed]
                if len(_kept) != len(symbols):
                    if _rt_once(f"wl:{strategy_id}"):
                        _dropped = [s for s in symbols if s not in _kept]
                        log.info(f"[WHITELIST] trading {len(_kept)} liquid names; dropped "
                                 f"{len(_dropped)} illiquid: {','.join(_dropped)}")
                    symbols = _kept

        for symbol in symbols:
            try:
                _rl.set_context(f"{strategy_id}:{symbol}")
            except Exception:
                pass
            st = get_state(symbol)
            if st["position"] is not None and open_legs_in_store is not None:
                # Re-validate with order_store open legs
                matching_open = False
                for p in open_legs_in_store:
                    if p.get("strategy") == strategy_id and p.get("symbol") == symbol:
                        if st.get("opt_sec_id"):
                            if str(p.get("sec_id")) == str(st["opt_sec_id"]):
                                matching_open = True
                                break
                        else:
                            matching_open = True
                            break
                if not matching_open:
                    log.info(f"[SYNC] Position for {symbol} closed externally in order_store (broker flat/manual exit/trailing lock). Clearing state.")
                    st["position"] = None
                    st["last_signal"] = None
                    st["opt_sec_id"] = None
                    st["opt_trad_sym"] = None

            if st["trades_today"] >= cfg.get("max_trades_per_symbol", 2):
                if _rt_once(f"maxtrades:{strategy_id}:{symbol}"):
                    log.info(f"[MAX-TRADES] {symbol} hit max_trades_per_symbol "
                             f"({cfg.get('max_trades_per_symbol', 2)}) — no further entries today for it")
                continue

            # Build daily levels once per day per symbol — but only CACHE a
            # successful build. `symbol not in daily_levels` tested KEY presence,
            # so a failed fetch (build_key_levels -> []) still created the key and
            # the retry could never fire again that day.
            #
            # 2026-07-17 cost a whole trading day: at 09:15:36 every strategy woke
            # at once, Dhan answered the daily-historical burst with a transient
            # 401/DH-902, and range_v1 logged "NIFTY levels: 0 key levels loaded".
            # The endpoint was fine seconds later — but 0 was cached until
            # midnight. No zones, no signals, and it looked perfectly healthy the
            # whole time: loop ticking, log fresh, heartbeat green. That was
            # 04.03 Pine2Python's first NIFTY-only day.
            #
            # `.get(symbol)` is falsy for BOTH missing and empty -> retry next
            # loop (~5m apart, and fetch_daily is rate-limiter gated, so this
            # can't turn into a hammer).
            if symbol in dead_syms:
                continue   # Dhan ise jaanta hi nahi — bola ja chuka, ab chup
            if not daily_levels.get(symbol):
                _retry = symbol in daily_levels   # empty from an earlier failure
                if _retry:
                    log.warning(f"{symbol}: levels khaali the (pichla fetch fail) — dobara try")
                daily_df = fetch_daily(symbol)
                time.sleep(0.4)  # Dhan DH-904: 25 symbols @ 0.4s = ~10s startup, well under rate limit
                # build_key_levels()/pivot_labels below both assume iloc[-2] is
                # "yesterday" and iloc[-1] is "today" — true for backtest tools
                # (which slice daily_df to end at their simulated day on
                # purpose) but NOT guaranteed live: Dhan's daily-historical
                # endpoint never returns a partial bar for today, and was found
                # 2026-06-29 to sometimes lag by 2+ trading days (e.g. last row
                # = Thursday when "today" was Monday) — silently shifting every
                # symbol's pivot/PD_H/PD_C/PD_L levels a full day stale vs
                # TradingView. Pad with a dummy "today" row whenever the last
                # real row isn't actually today, so iloc[-2] always resolves to
                # the true most-recent CLOSED trading day.
                if daily_df is not None and len(daily_df) and daily_df.iloc[-1]["date"] != ist_now().date():
                    import pandas as _pd
                    daily_df = _pd.concat([daily_df, _pd.DataFrame([{
                        "date": ist_now().date(), "open": float("nan"),
                        "high": float("nan"), "low": float("nan"), "close": float("nan"),
                    }])], ignore_index=True)
                is_idx   = symbol in ("NIFTY", "BANKNIFTY")
                daily_levels[symbol] = build_key_levels(
                    daily_df, is_index=is_idx,
                    max_jump_pct=cfg.get("max_jump_pct", 50.0)
                )
                if daily_levels[symbol]:
                    log.info(f"{symbol} levels: {len(daily_levels[symbol])} key levels loaded")
                else:
                    # Zero levels = this symbol CANNOT signal (no zones without key
                    # levels). But WHY decides what to do, and the two cases are
                    # opposites:
                    #
                    #   permanent — Dhan has no scrip-master entry for it at all
                    #               (a delisted/demerged name still sitting in a
                    #               config). Retrying can never succeed; say it
                    #               once, then stay quiet. Without this the retry
                    #               above loops forever and rings the bell forever
                    #               — see LESSONS.md TRAP #133 for the real case.
                    #   transient — the fetch failed (401 burst / network). Retry.
                    #
                    # Either way say it ONCE per day (_rt_once): the old INFO
                    # "0 key levels loaded" was shaped exactly like the success line
                    # and scrolled straight past.
                    _permanent = _dhan_info(symbol) is None
                    if _permanent:
                        dead_syms.add(symbol)
                        daily_levels.pop(symbol, None)
                        if _rt_once(f"deadsym:{strategy_id}:{symbol}"):
                            log.error(f"{symbol}: Dhan scrip master me hai hi nahi — ye symbol "
                                      f"kabhi trade nahi karega. Config ki symbols list se hatao.")
                            try:
                                import notify
                                notify.error(
                                    f"{symbol} Dhan me exist hi nahi karta — "
                                    f"config ki symbols list se hatao (retry bekaar hai).",
                                    key=f"deadsym:{strategy_id}:{symbol}", source=strategy_id)
                            except Exception:
                                pass
                        continue
                    daily_levels.pop(symbol, None)   # transient → retry next loop
                    if _rt_once(f"levels0:{strategy_id}:{symbol}"):
                        log.error(f"{symbol}: 0 key levels — daily fetch fail. "
                                  f"Agle loop me retry hoga.")
                        try:
                            import notify
                            notify.error(
                                f"{symbol} ke 0 key levels — daily data nahi mila, "
                                f"tab tak ye symbol trade nahi karega. Retry chalu hai.",
                                key=f"levels0:{strategy_id}:{symbol}", source=strategy_id)
                        except Exception:
                            pass

                # Display-only specific labels (R1-R5/S1-S5/P/PDH/PDC/PDL) for the
                # Watch chart — build_key_levels() collapses these to generic
                # RESISTANCE/SUPPORT/CP/PD_H/PD_C/PD_L on purpose (entry logic only
                # cares about the bucket, e.g. not_on_red_line checks "RESISTANCE"),
                # so recompute the specific pivot here without touching that.
                try:
                    if daily_df is not None and len(daily_df) >= 2:
                        _prev = daily_df.iloc[-2]
                        _ph, _pl, _pc = float(_prev["high"]), float(_prev["low"]), float(_prev["close"])
                        _piv = traditional_pivots(_ph, _pl, _pc)
                        _lbl = {round(_piv[name], 2): name for name in
                                ["R5", "R4", "R3", "R2", "R1", "P", "S1", "S2", "S3", "S4", "S5"]}
                        _lbl[round(_ph, 2)] = "PDH"
                        _lbl[round(_pc, 2)] = "PDC"
                        _lbl[round(_pl, 2)] = "PDL"
                        pivot_labels[symbol] = _lbl
                except Exception:
                    pivot_labels[symbol] = {}

            levels = daily_levels.get(symbol, [])
            if not levels:
                continue

            tf    = cfg.get("timeframe", "1m")
            df_1m = fetch_1m(symbol, tf)
            time.sleep(0.25)  # DH-904 guard: 25 symbols * 0.25s = ~6s per loop
            if df_1m is None or len(df_1m) < 20:
                continue

            # Proactive Option feed subscription (TRAP #65 - Fixed by Antigravity AI)
            # If the strategy is trading options, resolve likely ATM contracts at the current spot price
            # and subscribe them to dhan_feed early.
            if cfg.get("instrument", "equity") == "options" and df_1m is not None and not df_1m.empty:
                try:
                    spot_price = float(df_1m.iloc[-1]["close"])
                    offset = int(cfg.get("strike_offset", 0))
                    import dhan_master
                    import dhan_feed
                    # Resolve and subscribe likely ATM CALL
                    ce_sec_id, _, _ = dhan_master.get_option_contract(symbol, spot_price, "CE", offset)
                    if ce_sec_id:
                        dhan_feed.add(("NSE_FNO", ce_sec_id))
                    # Resolve and subscribe likely ATM PUT
                    pe_sec_id, _, _ = dhan_master.get_option_contract(symbol, spot_price, "PE", offset)
                    if pe_sec_id:
                        dhan_feed.add(("NSE_FNO", pe_sec_id))
                except Exception:
                    pass

            signal, price, reason, sig_bar, total_bars, cur_state = run_signal_engine(df_1m, levels, cfg)
            
            cur_state["symbol"] = symbol
            cur_state["position"] = st["position"]
            cur_state["trades_today"] = st.get("trades_today", 0)
            _lbl_map = pivot_labels.get(symbol, {})
            cur_state["key_levels"] = [[round(float(p), 2), _lbl_map.get(round(float(p), 2), t)] for p, t in levels]
            # df_1m["time"] is already IST wall-clock (fetch_1m shifts it +5:30
            # on the way in) — do NOT add 19800 again here (double-shift bug,
            # found 2026-06-29: candles displayed ~5.5h ahead on the watch chart).
            zb = cur_state.get("zone_bar")
            if zb is not None and 0 <= zb < len(df_1m):
                cur_state["zone_start_ts"] = int(pd.Timestamp(df_1m.iloc[zb]["time"]).timestamp())
            else:
                cur_state["zone_start_ts"] = None

            def _bar_ts(idx):
                idx = max(0, min(int(idx), len(df_1m) - 1))
                return int(pd.Timestamp(df_1m.iloc[idx]["time"]).timestamp())
            cur_state["zones_history"] = [
                {"start_ts": _bar_ts(zs), "end_ts": _bar_ts(ze), "upper": zu, "lower": zl, "type": zt}
                for zs, ze, zu, zl, zt in cur_state.get("zones_history", [])
            ]
            _watchlist_state[symbol] = cur_state

            # Sirf last 2 bars ka signal valid hai — purana signal duplicate entry karega
            if signal in ("BUY", "SELL") and (total_bars - sig_bar) > 2:
                log.debug(f"Skipping stale {signal} {symbol} — signal bar {sig_bar}, total {total_bars}")
                signal = None

            if signal in ("BUY", "SELL") and signal != st["last_signal"]:
                # ── No-entry-after gate (RMS single-source, risk_gate.exit_time_config)
                # No NEW entry at/after the configured time — otherwise the 3:15
                # squareoff instantly closes it = wasted entry+exit brokerage
                # ("zabardasti tax" bug). Exits are never gated.
                if is_no_entry_time():
                    _ne = _exit_times()[1]
                    log.info(f"ENTRY BLOCKED {symbol} — no entry after {_ne[0]:02d}:{_ne[1]:02d} (RMS)")
                    continue
                # ── Expiry-day entry block: no new positions after 2:00 PM
                # on expiry day — last-hour chaos + ITM risk not worth it.
                # (pos_monitor_loop fires EXPIRY_EOD at 2:55 to close existing
                # positions — a new entry just before that is pointless.)
                _now = ist_now()
                _ensure_root_path()
                import risk_gate as _rg
                _no_entry_after = (_now.hour, _now.minute) >= _rg.EXPIRY_NO_ENTRY_AFTER_HM
                if _no_entry_after:
                    _probe_sec = st.get("opt_sec_id")
                    if _probe_sec and _rg.is_expiry_day(sec_id=str(_probe_sec)):
                        log.info(f"ENTRY BLOCKED {symbol} — expiry day, no new entries after "
                                 f"{_rg.EXPIRY_NO_ENTRY_AFTER_HM[0]:02d}:{_rg.EXPIRY_NO_ENTRY_AFTER_HM[1]:02d}")
                        continue

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
                        # Subscribe this contract on the live feed as early as
                        # possible — gate_entry's liquidity check + place_order's
                        # premium fetch both read dhan_feed first; a freshly
                        # resolved ATM strike has no tick yet (subscribe causes
                        # a reconnect), so every millisecond of head start here
                        # reduces how often we fall through to REST/DH-904.
                        try:
                            import dhan_feed
                            dhan_feed.add(("NSE_FNO", sec_id))
                        except Exception:
                            pass
                        # ── risk gate (RMS Stage 1+2+3) — single shared gate,
                        # see strategy_safety.gate_entry() (LESSONS.md TRAP #15:
                        # this used to be hand-rolled separately per strategy
                        # file and silently drifted). Exits (3:15 squareoff /
                        # EXIT signal below) are never gated.
                        #
                        # broker= is now passed (2026-07-03, TRAP #90) so
                        # gate_entry()'s step 6 (check_broker_funds — LIVE mode
                        # only, real broker balance vs needed ₹) actually runs.
                        # It was silently skipped before: gate_entry() only
                        # checks broker funds `if broker is not None`, and this
                        # call never passed one — the stale comment here used
                        # to say "this legacy trader doesn't have a brokers.*
                        # broker object," which hasn't been true since
                        # place_order() below started resolving one via
                        # get_broker() for every order anyway. So every entry
                        # was checked against the manually-configured ₹ capital
                        # cap (an estimate via Dhan's margin calculator) but
                        # NEVER against what the live broker (Kite/Zerodha)
                        # actually has available — found live 2026-07-03 while
                        # explaining a capital-cap block to the user. ──
                        _ensure_root_path()
                        import risk_gate
                        import strategy_safety
                        from brokers import get_broker
                        opt_prem_ltp = risk_gate._quick_option_ltp(sec_id, token, cid)  # real premium, or None/0 if fetch failed
                        opt_prem = opt_prem_ltp or price  # gate check only: conservative fallback to spot
                        _entry_broker_name = risk_gate.default_broker()
                        try:
                            _entry_broker = get_broker(_entry_broker_name)
                        except Exception as _be:
                            log.warning(f"broker resolve failed for funds-check ({_be}) — gate proceeds without it")
                            _entry_broker = None
                        gate_ok, gated_qty, gate_reason = strategy_safety.gate_entry(
                            strategy_id, symbol, qty, lot_sz, opt_prem, side="SELL",
                            sec_id=sec_id, mode=mode, broker=_entry_broker, log=log.info)
                        if not gate_ok:
                            log.info(f"ENTRY blocked {symbol} — {gate_reason}")
                            try:
                                import order_store
                                # Record the OPTION PREMIUM (opt_prem_ltp, ~65.60), NOT the underlying
                                # spot `price` (~3974.8). Using spot here recorded a blocked PE/CE
                                # leg at the index level, so the dashboard's blocked-entries panel
                                # showed a nonsensical entry price → wrong points/P&L. (Task 14)
                                # NOTE: opt_prem falls back to spot when the premium LTP fetch fails
                                # (DH-904), so use opt_prem_ltp directly — real premium or 0 (blocked
                                # rows legitimately carry no price; order_store's ₹0 tripwire skips
                                # 'blocked'). 0 shows honestly as "premium N/A", never a bogus ₹3974.
                                # webhook_executor + universe_trader already record the premium.
                                order_store.record("SELL", actual_qty, opt_prem_ltp or 0, source='strategy',
                                    strategy=strategy_id, mode=mode, broker=risk_gate.default_broker(), symbol=symbol,
                                    instrument='options', trad_sym=t_sym, sec_id=sec_id, segment='NSE_FNO',
                                    status='blocked', tags=["CAPITAL_BLOCKED", gate_reason])
                            except Exception:
                                pass
                            continue
                        if gated_qty != actual_qty:
                            log.info(f"ENTRY sized down {symbol} — qty {actual_qty} -> {gated_qty} ({gate_reason})")
                            actual_qty = gated_qty
                        try:
                            default_sl_tags = risk_gate.default_instrument_sl_tags(strategy_id, symbol)
                        except Exception:
                            default_sl_tags = []
                        hedge_min_strikes, hedge_max_premium = risk_gate.hedge_config(strategy_id)
                        hedge_on = bool(hedge_min_strikes or hedge_max_premium)
                        group_id = f"RANGE_{symbol}_{int(time.time())}" if hedge_on else ""
                        if not place_order(symbol, "SELL", actual_qty, token, cid, mode, sec_id, "NSE_FNO", t_sym, price=price, extra_tags=default_sl_tags, group_id=group_id):
                            # premium unavailable → entry skipped, leave flat so a
                            # later signal can retry (no phantom 0-price position)
                            continue
                        st["opt_sec_id"] = sec_id
                        st["opt_trad_sym"] = t_sym
                        st["opt_qty"] = actual_qty

                        # ── auto-hedge BUY (opt-in, RMS tab "Hedge" fields) —
                        # resolution lives in ONE place: strategy_safety.
                        # compute_hedge_target() (LESSONS.md TRAP #15). Best-
                        # effort: contract-resolve/premium-fetch failure logs+
                        # skips, never unwinds the SELL that already filled. ──
                        if hedge_on:
                            try:
                                h_sec_id, h_t_sym, _h_lot = strategy_safety.compute_hedge_target(
                                    strategy_id, symbol, price, opt_type, offset,
                                    quote_fn=lambda sid: _fetch_premium(sid, token, cid), log=log.warning)
                                if h_sec_id:
                                    place_order(symbol, "BUY", actual_qty, token, cid, mode,
                                                h_sec_id, "NSE_FNO", h_t_sym, group_id=group_id,
                                                product="NRML")
                            except Exception as _e:
                                log.warning(f"[HEDGE] failed (sell leg unaffected): {_e}")
                    else:
                        log.error(f"Option contract not found for {symbol} {opt_type}")
                else:
                    # Task 3b (Rule 6B / ADR-001): equity-route ab inline
                    # check_drawdown/concentration/capital ki jagah shared
                    # strategy_safety.gate_entry() se jaata hai — wahi single gate
                    # jo option branch + execution_gateway use karte hain
                    # (gating_status + drawdown + concentration + capital size-down
                    # + broker-funds; equity seg pe FNO-liquidity check skip). Isse
                    # 3 extra guards free milte hain aur inline "risk-check fail →
                    # allow entry" (fail-OPEN) gap band hota hai. place_order khud
                    # execution_gateway ko delegate karta hai; CAPITAL_BLOCKED row
                    # caller yahin record karta hai (gateway nahi karta).
                    import risk_gate
                    from brokers import get_broker  # local: option-branch's import may not have run on this path
                    _entry_broker_name = risk_gate.default_broker()
                    try:
                        _entry_broker = get_broker(_entry_broker_name)
                    except Exception as _be:
                        log.warning(f"broker resolve failed for funds-check ({_be}) — gate proceeds without it")
                        _entry_broker = None
                    gate_ok, gated_qty, gate_reason = strategy_safety.gate_entry(
                        strategy_id, symbol, qty, 1, price, side=signal,
                        sec_id=None, seg="NSE_EQ", mode=mode, broker=_entry_broker, log=log.info)
                    if not gate_ok:
                        log.info(f"ENTRY blocked {symbol} — {gate_reason}")
                        try:
                            import order_store
                            order_store.record(signal, qty, price, source='strategy',
                                strategy=strategy_id, mode=mode, broker=risk_gate.default_broker(), symbol=symbol,
                                instrument='equity', trad_sym=symbol, status='blocked',
                                tags=["CAPITAL_BLOCKED", gate_reason])
                        except Exception:
                            pass
                        continue
                    if gated_qty != qty:
                        log.info(f"ENTRY sized down {symbol} — {qty} -> {gated_qty} ({gate_reason})")
                        qty = gated_qty
                    try:
                        default_sl_tags = risk_gate.default_instrument_sl_tags(strategy_id, symbol)
                    except Exception:
                        default_sl_tags = []
                    place_order(symbol, signal, qty, token, cid, mode, price=price, extra_tags=default_sl_tags)

                st["trades_today"] += 1
                st["last_signal"]   = signal
                st["entry_price"]   = price
                st["position"]      = "LONG" if signal == "BUY" else "SHORT" 

            elif signal in ("EXIT_LONG", "EXIT_SHORT"):
                if st["position"] is None:
                    continue  # no open position this session — skip stale startup EXIT
                exit_side = "SELL" if signal == "EXIT_LONG" else "BUY"

                # ── Pre-exit broker-flat guard (manual-close protection) ──────────
                # If this position was already closed at the broker (user did it
                # manually on Zerodha, an SL fired there, etc.), firing our own
                # exit here would OPEN a brand-new OPPOSITE position — 1 trade
                # becomes 3, plus extra tax/loss. The TRAP #62 order_store
                # re-validation above only self-heals AFTER broker_sync (a
                # separate process, 30s cadence) has recorded the close; a
                # freshly-fired exit in that 30s window would still slip through.
                # So do a FRESH live broker positions() check right now — not the
                # per-process broker_sync cache (empty in THIS strategy's process),
                # not order_store (lags broker_sync). Live mode only: in paper
                # there is no real broker position and no phantom-money risk.
                if mode == "live":
                    try:
                        import broker_sync as _bs
                        from brokers import get_broker as _gb
                        _bn      = (cfg.get("broker") or "dhan")
                        _chk_sid = str(st.get("opt_sec_id") or "")
                        _chk_sym = st.get("opt_trad_sym") or symbol
                        if not _chk_sid:
                            _info = _dhan_info(symbol)
                            _chk_sid = str(_info[0]) if _info else ""
                        _bpos = _gb(_bn).positions()
                        if _bpos is not None and _bs._check_flat(_bn.lower(), _bpos, _chk_sym, _chk_sid):
                            log.info(f"[FLAT-CHECK] {symbol} already flat at broker "
                                     f"(manually closed / SL hit elsewhere) — skipping exit "
                                     f"order to avoid a phantom opposite position; clearing state.")
                            st["position"]     = None
                            st["last_signal"]  = None
                            st["opt_sec_id"]   = None
                            st["opt_trad_sym"] = None
                            continue
                    except Exception as _fe:
                        log.warning(f"[FLAT-CHECK] pre-exit broker check failed ({_fe}) "
                                    f"— proceeding with exit (fail-open)")
                # ──────────────────────────────────────────────────────────────────

                log.info(f"EXIT {symbol} via {reason} @ {price:.2f}")

                # Tag the exit leg with its own reason (e.g. "ATR_TRAILING")
                # so the dashboard's Completed Trades "Exit Reason" column
                # isn't blank for a strategy-driven exit — this used to only
                # ever reach the log file, never order_store. Found live
                # 2026-07-03 (TCS-Jul2026-2100-CE exit showed no reason).
                _exit_tags = [str(reason)] if reason else []
                if st.get("opt_sec_id"):
                    place_order(symbol, "BUY", st.get("opt_qty", cfg.get("qty",1)), token, cid, mode, st["opt_sec_id"], "NSE_FNO", st["opt_trad_sym"], is_exit=True, extra_tags=_exit_tags)
                else:
                    place_order(symbol, exit_side, cfg.get("qty", 1), token, cid, mode, is_exit=True, extra_tags=_exit_tags)
                    
                st["position"]    = None
                st["last_signal"] = None
                st["opt_sec_id"] = None
                st["opt_trad_sym"] = None

        try:
            watch_file = BASE_DIR / "data" / f"watch_{strategy_id}.json"
            watch_file.parent.mkdir(exist_ok=True)
            with open(watch_file, "w") as f:
                json.dump({"timestamp": time.time(), "symbols": list(_watchlist_state.values())}, f)
        except Exception as e:
            log.error(f"Watchlist dump error: {e}")

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
