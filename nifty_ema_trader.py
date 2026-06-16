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
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

# ── IPv4 Force — Dhan rejects IPv6 (DH-905) ───────────────────────────────────
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
TC_FILE     = BASE_DIR / "nifty_config.json"
LOG_FILE    = BASE_DIR / "nifty_trader.log"

# ── Yahoo Finance symbol map ───────────────────────────────────────────────────
# Add/remove symbols here freely
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
    "TATAMOTORS":   "TATAMOTORS.BO",
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
}

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
MARKET_OPEN  = (9, 16)
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

def load_config():
    default = {
        "active": True,
        "fast_ema": 9,
        "slow_ema": 20,
        "qty": 1,
        "max_trades_per_symbol": 2,
        "symbols": list(SYMBOLS.keys())
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


# ── Candle Fetch ───────────────────────────────────────────────────────────────

def fetch_candles(symbol):
    ticker = SYMBOLS.get(symbol)
    if not ticker:
        return None
    try:
        df = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        df = df.reset_index()
        df.columns = [c[0].lower() if isinstance(c, tuple) else c.lower() for c in df.columns]
        if "datetime" in df.columns:
            df = df.rename(columns={"datetime": "time"})
        df = df[["time", "open", "high", "low", "close"]].dropna()
        return df
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
    log.info(f"  📝 PAPER {signal:4s} {symbol:12s} @ {price:.2f}  [{t}]")
    if len(book) >= 2:
        prev = book[-2]
        if prev["signal"] == "BUY" and signal == "SELL":
            pnl = price - prev["price"]
            log.info(f"  📊 {symbol} PnL: {'+' if pnl>0 else ''}{pnl:.2f} pts  {'✅ WIN' if pnl>0 else '❌ LOSS'}")
        elif prev["signal"] == "SELL" and signal == "BUY":
            pnl = prev["price"] - price
            log.info(f"  📊 {symbol} PnL: {'+' if pnl>0 else ''}{pnl:.2f} pts  {'✅ WIN' if pnl>0 else '❌ LOSS'}")


# ── Real Order ─────────────────────────────────────────────────────────────────

def place_order(symbol, side, qty, token, cid):
    if symbol == "NIFTY":
        log.error("NIFTY index cannot be traded directly — add futures_security_id to config")
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

def run(paper_mode=True):
    mode_str = "📝 PAPER" if paper_mode else "💰 LIVE"
    log.info("=" * 60)
    log.info(f"EMA Trader  |  Mode: {mode_str}")
    log.info("=" * 60)

    # Per-symbol state
    positions    = {}   # symbol → +1 / -1 / 0
    trades_today = {}   # symbol → count
    last_date    = None

    while True:
        try:
            now = ist_now()
            tc  = load_config()

            # Daily reset
            if last_date != now.date():
                last_date    = now.date()
                positions    = {}
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
            sym_list = tc.get("symbols", list(SYMBOLS.keys()))

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

                if t_count >= max_t:
                    log.info(f"  {sym:12s} max trades ({max_t}) hit — skip")
                    continue

                df = fetch_candles(sym)
                if df is None or df.empty:
                    log.warning(f"  {sym:12s} no data")
                    continue

                signal     = compute_signal(df, fast, slow)
                last_close = float(df["close"].iloc[-2])

                log.info(f"  {sym:12s} close={last_close:.2f}  signal={signal or 'NONE':4s}  pos={pos:+d}  trades={t_count}/{max_t}")

                if signal == "BUY" and pos <= 0:
                    if pos < 0:   # close short
                        if paper_mode:
                            paper_trade(sym, "BUY", last_close)
                        else:
                            place_order(sym, "BUY", qty, token, cid)
                        trades_today[sym] = t_count + 1
                    # open long
                    if paper_mode:
                        paper_trade(sym, "BUY", last_close)
                    else:
                        place_order(sym, "BUY", qty, token, cid)
                    positions[sym]    = 1
                    trades_today[sym] = trades_today.get(sym, 0) + 1

                elif signal == "SELL" and pos >= 0:
                    if pos > 0:   # close long
                        if paper_mode:
                            paper_trade(sym, "SELL", last_close)
                        else:
                            place_order(sym, "SELL", qty, token, cid)
                        trades_today[sym] = trades_today.get(sym, 0) + 1
                    # open short
                    if paper_mode:
                        paper_trade(sym, "SELL", last_close)
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
    args = ap.parse_args()

    if args.live:
        print("\n⚠️  LIVE MODE — real orders on Dhan!")
        print("Ctrl+C within 5s to cancel...\n")
        time.sleep(5)
        run(paper_mode=False)
    else:
        print("\n📝 PAPER MODE — signals only\n")
        run(paper_mode=True)
