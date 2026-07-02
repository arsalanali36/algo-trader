#!/usr/bin/env python3
"""
nifty_ema_trader.py — 9/20 EMA Crossover | Multi-Symbol | 1-min

Modes:
  --paper  : Signals only, no real orders (default)
  --live   : Real orders on Dhan

Usage:
  python nifty_ema_trader.py --paper
  python nifty_ema_trader.py --live

Config file: nifty_config.json (hot-reload every loop)
"""

import json
import logging
import os
import socket
import sys
import time
# project root (parent of _TRADERS/) on path BEFORE importing root modules —
# launched as a subprocess (sys.path[0] = _TRADERS/), so dhan_master at the
# project root is otherwise not importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dhan_master
import dhan_rate_limiter as _rl
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# ── IPv4 Force — Dhan rejects IPv6 (DH-905) ───────────────────────────────────
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

# ── Paths ──────────────────────────────────────────────────────────────────────
# BASE_DIR = project root (parent of _TRADERS/) — creds, nifty_config.json and
# logs/ live at the root, not inside _TRADERS/ (same as rsi_trader.py).
BASE_DIR    = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
TC_FILE     = BASE_DIR / "nifty_config.json"
LOG_FILE    = BASE_DIR / "nifty_trader.log"

# ── Symbol list (Dhan equity symbols) ─────────────────────────────────────────
SYMBOLS = [
    "NIFTY", "BANKNIFTY", "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "SBIN", "AXISBANK", "BAJFINANCE", "WIPRO", "KOTAKBANK", "LT",
    "MARUTI", "HINDUNILVR", "ITC", "ADANIENT", "SUNPHARMA", "TITAN",
    "ULTRACEMCO", "NESTLEIND", "POWERGRID", "NTPC", "ONGC",
]

# ── Dhan API (orders only) ─────────────────────────────────────────────────────
ORDERS_URL = "https://api.dhan.co/v2/orders"

# Dhan security IDs for live trading (equity only — NIFTY index can't be traded)
DHAN_INFO = {
    "RELIANCE":   ("2885",  "NSE_EQ"),
    "TCS":        ("11536", "NSE_EQ"),
    "INFY":       ("1594",  "NSE_EQ"),
    "HDFCBANK":   ("1333",  "NSE_EQ"),
    "ICICIBANK":  ("4963",  "NSE_EQ"),
    "SBIN":       ("3045",  "NSE_EQ"),
    "AXISBANK":   ("5900",  "NSE_EQ"),
    "BAJFINANCE": ("317",   "NSE_EQ"),
    "WIPRO":      ("3787",  "NSE_EQ"),
}

# ── Market Hours IST ───────────────────────────────────────────────────────────
MARKET_OPEN  = (9, 15)
MARKET_CLOSE = (15, 25)
AUTO_EXIT_AT = (15, 15)

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("trader")


# ── Helpers ────────────────────────────────────────────────────────────────────

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
        "fast_ema": 9,
        "slow_ema": 20,
        "qty": 1,
        "max_trades_per_symbol": 2,
        "symbols": list(SYMBOLS)
    }
    if not TC_FILE.exists():
        TC_FILE.write_text(json.dumps(default, indent=2))
        return default
    try:
        return json.loads(TC_FILE.read_text())
    except Exception:
        return default

def hdrs(token, cid):
    return {"access-token": token, "client-id": cid, "Content-Type": "application/json"}


# ── Candle Fetch (Dhan intraday API) ──────────────────────────────────────────
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
_TF_MAP = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}

def fetch_candles(symbol, tf="1m"):
    info = dhan_master.get_equity_info(symbol)
    if not info:
        log.error(f"No Dhan sec_id for {symbol}")
        return None
    sec_id, seg, inst = info
    interval = _TF_MAP.get(tf, 1)
    try:
        token, cid = load_creds()
        hdrs_c = {"access-token": token, "client-id": cid, "Content-Type": "application/json"}
        today = ist_now().strftime("%Y-%m-%d")
        body  = {"securityId": sec_id, "exchangeSegment": seg,
                 "instrument": inst, "interval": interval,
                 "fromDate": today, "toDate": today}
        # Was calling Dhan directly with zero rate-limiting — every symbol in the
        # watchlist hit /v2/charts/intraday back-to-back every scan cycle, blowing
        # through Dhan's ~1 req/sec account-wide limit (DH-904 storm across
        # LT/MARUTI/HINDUNILVR/etc, found live 2026-07-02). range_trader.py's
        # equivalent fetch already routes through dhan_rate_limiter — this file
        # never got that treatment. Same fix, same pattern: acquire() serializes
        # this call against every other process's Dhan traffic; note_429() opens
        # the shared cooldown if the account limit still gets breached.
        _rl.acquire("candle")
        r = requests.post(INTRADAY_URL, json=body, headers=hdrs_c, timeout=10)
        if r.status_code == 429:
            _rl.note_429()
        if r.status_code != 200:
            log.error(f"{symbol} intraday {r.status_code}: {r.text[:120]}")
            return None
        d = r.json()
        ts   = d.get("timestamp", [])
        rows = list(zip(ts, d.get("open", []), d.get("high", []), d.get("low", []), d.get("close", [])))
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return df.dropna()
    except Exception as e:
        log.error(f"{symbol} candle error: {e}")
        return None


# ── EMA Signal ─────────────────────────────────────────────────────────────────

def compute_signal(df, fast=9, slow=20):
    if len(df) < slow + 5:
        return None
    close    = df["close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    cf, cs = ema_fast.iloc[-2], ema_slow.iloc[-2]   # last confirmed candle
    pf, ps = ema_fast.iloc[-3], ema_slow.iloc[-3]   # candle before that

    if pf <= ps and cf > cs:
        return "BUY"
    if pf >= ps and cf < cs:
        return "SELL"
    return None


# ── Paper Trade ────────────────────────────────────────────────────────────────

# symbol → list of {"signal", "price", "time"}
_paper_book = {}

def paper_trade(symbol, signal, price):
    t = ist_now().strftime("%H:%M:%S")
    book = _paper_book.setdefault(symbol, [])
    book.append({"signal": signal, "price": price, "time": t})
    log.info(f"  [PAPER] {signal} 1 {symbol} @ {price:.2f}")
    if len(book) >= 2:
        prev = book[-2]
        if prev["signal"] == "BUY" and signal == "SELL":
            pnl = price - prev["price"]
            log.info(f"  📊 {symbol} PnL: {'+' if pnl>0 else ''}{pnl:.2f} pts  {'✅ WIN' if pnl>0 else '❌ LOSS'}")
        elif prev["signal"] == "SELL" and signal == "BUY":
            pnl = prev["price"] - price
            log.info(f"  📊 {symbol} PnL: {'+' if pnl>0 else ''}{pnl:.2f} pts  {'✅ WIN' if pnl>0 else '❌ LOSS'}")


# ── Real Order ─────────────────────────────────────────────────────────────────

def place_order(symbol, side, qty, token, cid, sec_id=None, seg=None, trad_sym=None):
    if not sec_id or not seg:
        if symbol == "NIFTY":
            log.error("NIFTY index cannot be traded directly in equity")
            return False
        info = DHAN_INFO.get(symbol)
        if not info:
            log.error(f"No Dhan info for {symbol}")
            return False
        sec_id, seg = info
        trad_sym = symbol

    payload = {
        "dhanClientId":      cid,
        "correlationId":     f"EMA_{trad_sym}_{int(time.time())}",
        "transactionType":   side,
        "exchangeSegment":   seg,
        "productType":       "INTRADAY",
        "orderType":         "MARKET",
        "validity":          "DAY",
        "tradingSymbol":     trad_sym,
        "securityId":        sec_id,
        "quantity":          qty,
        "disclosedQuantity": 0,
        "price":             0,
        "triggerPrice":      0,
        "afterMarketOrder":  False,
        "amoTime":           "OPEN"
    }
    try:
        r    = requests.post(ORDERS_URL, json=payload, headers=hdrs(token, cid), timeout=20)
        resp = r.json() if r.content else {}
        if r.status_code == 200:
            log.info(f"  ✅ LIVE {side} {trad_sym} qty={qty}  orderId={resp.get('orderId','?')}")
            return True
        log.error(f"  ❌ {side} {trad_sym} failed: {resp.get('remarks') or r.text[:150]}")
        return False
    except Exception as e:
        log.error(f"  Order exception: {e}")
        return False
    info = DHAN_INFO.get(symbol)
    if not info:
        log.error(f"No Dhan info for {symbol}")
        return False
    sec_id, seg = info
    payload = {
        "dhanClientId":      cid,
        "correlationId":     f"EMA_{symbol}_{int(time.time())}",
        "transactionType":   side,
        "exchangeSegment":   seg,
        "productType":       "INTRADAY",
        "orderType":         "MARKET",
        "validity":          "DAY",
        "tradingSymbol":     symbol,
        "securityId":        sec_id,
        "quantity":          qty,
        "disclosedQuantity": 0,
        "price":             0,
        "triggerPrice":      0,
        "afterMarketOrder":  False,
        "amoTime":           "OPEN",
        "boProfitValue":     0,
        "boStopLossValue":   0,
        "drvExpiryDate":     "",
        "drvOptionsType":    "CALL",
        "drvStrikePrice":    0,
    }
    try:
        r    = requests.post(ORDERS_URL, json=payload, headers=hdrs(token, cid), timeout=20)
        resp = r.json() if r.content else {}
        if r.status_code == 200:
            log.info(f"  ✅ LIVE {side} {symbol} qty={qty}  orderId={resp.get('orderId','?')}")
            return True
        log.error(f"  ❌ {side} {symbol} failed: {resp.get('remarks') or r.text[:150]}")
        return False
    except Exception as e:
        log.error(f"  Order exception: {e}")
        return False


# ── Main Loop ──────────────────────────────────────────────────────────────────

def run(paper_mode=True, strategy_id="ema"):
    mode_str = "📝 PAPER" if paper_mode else "💰 LIVE"
    log.info("=" * 60)
    log.info(f"EMA Trader  |  Mode: {mode_str}")
    log.info("=" * 60)

    # Per-symbol state
    positions    = {}   # symbol → +1 / -1 / 0
    active_options = {} # symbol → {'sec_id': id, 'trad_sym': sym}
    trades_today = {}   # symbol → count
    last_date    = None

    while True:
        try:
            now = ist_now()
            tc  = load_config(strategy_id)

            # Daily reset
            if last_date != now.date():
                last_date    = now.date()
                positions    = {}
                active_options = {}
                trades_today = {}
                log.info(f"── New day: {last_date} ──")

            if not tc.get("active", True):
                log.info("⏸  Paused")
                time.sleep(60)
                continue

            if not is_market_open():
                log.info(f"🕐 Market closed ({now.strftime('%H:%M')} IST)")
                time.sleep(60)
                continue

            fast    = int(tc.get("fast_ema", 9))
            slow    = int(tc.get("slow_ema", 20))
            qty     = int(tc.get("qty", 1))
            max_t   = int(tc.get("max_trades_per_symbol", 2))
            tf      = tc.get("timeframe", "1m")
            # SYMBOLS is a LIST, not a dict — `.keys()` here crashed the loop
            # every single cycle with "'list' object has no attribute 'keys'"
            # (Python evaluates a default arg eagerly, so it fired regardless of
            # whether config had a "symbols" key). Also handle the comma-string
            # form of "symbols" in nifty_config.json (TRAP #16) — iterating a
            # raw string would loop character-by-character and silently scan 0
            # real symbols.
            sym_list = tc.get("symbols") or list(SYMBOLS)
            if isinstance(sym_list, str):
                import re as _re
                sym_list = [s.strip().upper() for s in _re.split(r"[,\s]+", sym_list) if s.strip()]

            token, cid = load_creds()

            # Auto exit 3:15
            if is_exit_time():
                for sym, pos in list(positions.items()):
                    if pos != 0:
                        log.info(f"⏰ 3:15 square-off: {sym}")
                        side = "SELL" if pos > 0 else "BUY"
                        if paper_mode:
                            paper_trade(sym, side, 0)
                        else:
                            if sym in active_options:
                                place_order(sym, "BUY", qty, token, cid, active_options[sym]['sec_id'], "NFO_OPT", active_options[sym]['trad_sym'])
                                del active_options[sym]
                            else:
                                place_order(sym, side, qty, token, cid)
                        positions[sym] = 0
                time.sleep(60)
                continue

            log.info(f"── Scanning {len(sym_list)} symbols | EMA {fast}/{slow} ──")

            for sym in sym_list:
                if sym not in SYMBOLS:
                    continue

                t_count = trades_today.get(sym, 0)
                pos     = positions.get(sym, 0)

                # max_trades hit + no open position → skip entirely
                if t_count >= max_t and pos == 0:
                    log.info(f"  {sym:12s} max trades ({max_t}) hit — skip")
                    continue

                df = fetch_candles(sym, tf)
                if df is None or df.empty:
                    log.warning(f"  {sym:12s} no data")
                    continue

                signal     = compute_signal(df, fast, slow)
                last_close = float(df["close"].iloc[-2])

                log.info(f"  {sym:12s} close={last_close:.2f}  signal={signal or 'NONE':4s}  pos={pos:+d}  trades={t_count}/{max_t}")

                inst   = tc.get("instrument", "equity")
                offset = int(tc.get("strike_offset", 0))

                # max_trades hit but position open → only exit allowed, no new entry
                allow_entry = (t_count < max_t)

                if signal == "BUY":
                    if pos < 0:   # EXIT short (always, regardless of max_trades)
                        if paper_mode:
                            paper_trade(sym, "BUY", last_close)
                        else:
                            if inst == "options" and sym in active_options:
                                place_order(sym, "BUY", qty, token, cid, active_options[sym]['sec_id'], "NFO_OPT", active_options[sym]['trad_sym'])
                                del active_options[sym]
                            else:
                                place_order(sym, "BUY", qty, token, cid)
                        positions[sym]    = 0
                        trades_today[sym] = trades_today.get(sym, 0) + 1

                    if pos <= 0 and allow_entry:   # ENTRY long (only if trades left)
                        if paper_mode:
                            paper_trade(sym, "BUY", last_close)
                        else:
                            if inst == "options":
                                sec_id, t_sym = dhan_master.get_option_contract(sym, last_close, "PE", offset)
                                if sec_id:
                                    place_order(sym, "SELL", qty, token, cid, sec_id, "NFO_OPT", t_sym)
                                    active_options[sym] = {'sec_id': sec_id, 'trad_sym': t_sym}
                                else:
                                    log.error(f"  {sym} PE option not found")
                            else:
                                place_order(sym, "BUY", qty, token, cid)
                        positions[sym]    = 1
                        trades_today[sym] = trades_today.get(sym, 0) + 1

                elif signal == "SELL":
                    if pos > 0:   # EXIT long (always, regardless of max_trades)
                        if paper_mode:
                            paper_trade(sym, "SELL", last_close)
                        else:
                            if inst == "options" and sym in active_options:
                                place_order(sym, "BUY", qty, token, cid, active_options[sym]['sec_id'], "NFO_OPT", active_options[sym]['trad_sym'])
                                del active_options[sym]
                            else:
                                place_order(sym, "SELL", qty, token, cid)
                        positions[sym]    = 0
                        trades_today[sym] = trades_today.get(sym, 0) + 1

                    if pos >= 0 and allow_entry:   # ENTRY short (only if trades left)
                        if paper_mode:
                            paper_trade(sym, "SELL", last_close)
                        else:
                            if inst == "options":
                                sec_id, t_sym = dhan_master.get_option_contract(sym, last_close, "CE", offset)
                                if sec_id:
                                    place_order(sym, "SELL", qty, token, cid, sec_id, "NFO_OPT", t_sym)
                                    active_options[sym] = {'sec_id': sec_id, 'trad_sym': t_sym}
                                else:
                                    log.error(f"  {sym} CE option not found")
                            else:
                                place_order(sym, "SELL", qty, token, cid)
                        positions[sym]    = -1
                        trades_today[sym] = trades_today.get(sym, 0) + 1

        except KeyboardInterrupt:
            log.info("Stopped")
            break
        except Exception as e:
            log.error(f"Loop error: {e}")

        time.sleep(60)


# ── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true", help="Paper mode (default)")
    ap.add_argument("--live",  action="store_true", help="Real orders on Dhan")
    ap.add_argument("--id",    default="ema")
    args = ap.parse_args()

    if args.live:
        print("\n⚠️  LIVE MODE — real orders on Dhan!")
        print("Ctrl+C within 5s to cancel...\n")
        time.sleep(5)
        run(paper_mode=False)
    else:
        print("\n📝 PAPER MODE — signals only\n")
        run(paper_mode=True)
