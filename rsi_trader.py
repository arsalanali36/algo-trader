#!/usr/bin/env python3
"""
rsi_trader.py — RSI Strategy | Multi-Symbol | 5-min TF

RSI logic:
  BUY  → RSI crosses ABOVE oversold level (e.g. 30) — recovery signal
  SELL → RSI crosses BELOW overbought level (e.g. 70) — reversal signal

Modes:
  --paper  : Signals only, no real orders (default)
  --live   : Real orders on Dhan

Config: rsi_config.json (hot-reload every loop)
"""

import json
import logging
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
import requests

# ── IPv4 Force — Dhan rejects IPv6 (DH-905) ──────────────────────────────────
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
TC_FILE     = BASE_DIR / "rsi_config.json"
LOG_FILE    = BASE_DIR / "rsi_trader.log"

# ── Symbol Map (Yahoo Finance tickers) ────────────────────────────────────────
SYMBOLS = {
    "NIFTY":      "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "RELIANCE":   "RELIANCE.NS",
    "TCS":        "TCS.NS",
    "INFY":       "INFY.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "SBIN":       "SBIN.NS",
    "AXISBANK":   "AXISBANK.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "WIPRO":      "WIPRO.NS",
    "KOTAKBANK":  "KOTAKBANK.NS",
    "LT":         "LT.NS",
    "MARUTI":     "MARUTI.NS",
    "HINDUNILVR": "HINDUNILVR.NS",
    "ITC":        "ITC.NS",
    "ADANIENT":   "ADANIENT.NS",
    "SUNPHARMA":  "SUNPHARMA.NS",
    "TITAN":      "TITAN.NS",
    "ULTRACEMCO": "ULTRACEMCO.NS",
    "NESTLEIND":  "NESTLEIND.NS",
    "POWERGRID":  "POWERGRID.NS",
    "NTPC":       "NTPC.NS",
    "ONGC":       "ONGC.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
}

# ── Dhan API ──────────────────────────────────────────────────────────────────
ORDERS_URL = "https://api.dhan.co/v2/orders"

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
    "KOTAKBANK":  ("1922",  "NSE_EQ"),
    "LT":         ("11483", "NSE_EQ"),
    "MARUTI":     ("10999", "NSE_EQ"),
    "ITC":        ("1660",  "NSE_EQ"),
    "ADANIENT":   ("25",    "NSE_EQ"),
    "SUNPHARMA":  ("3351",  "NSE_EQ"),
    "TITAN":      ("3506",  "NSE_EQ"),
    "NTPC":       ("11630", "NSE_EQ"),
    "ONGC":       ("2475",  "NSE_EQ"),
    "TATAMOTORS": ("3456",  "NSE_EQ"),
    "POWERGRID":  ("14977", "NSE_EQ"),
}

MARKET_OPEN  = (9, 16)
MARKET_CLOSE = (15, 25)
AUTO_EXIT_AT = (15, 15)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("rsi_trader")


# ── Helpers ───────────────────────────────────────────────────────────────────

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
        "rsi_period": 14,
        "oversold": 30,
        "overbought": 70,
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


# ── Candle Fetch (5-min) ──────────────────────────────────────────────────────

def fetch_candles(symbol):
    ticker = SYMBOLS.get(symbol)
    if not ticker:
        return None
    try:
        df = yf.download(ticker, period="5d", interval="5m", progress=False, auto_adjust=True)
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


# ── RSI Calculation ───────────────────────────────────────────────────────────

def compute_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, float('inf'))
    return 100 - (100 / (1 + rs))

def compute_signal(df, period=14, oversold=30, overbought=70):
    if len(df) < period + 5:
        return None, None
    rsi = compute_rsi(df["close"], period)
    # Use second-last confirmed candle vs the one before it
    cur = rsi.iloc[-2]
    prv = rsi.iloc[-3]

    if prv <= oversold and cur > oversold:   # crossed UP through oversold → BUY
        return "BUY", round(cur, 1)
    if prv >= overbought and cur < overbought:  # crossed DOWN through overbought → SELL
        return "SELL", round(cur, 1)
    return None, round(cur, 1)


# ── Paper Trade ───────────────────────────────────────────────────────────────

_paper_book = {}

def paper_trade(symbol, signal, price, rsi_val):
    t = ist_now().strftime("%H:%M:%S")
    book = _paper_book.setdefault(symbol, [])
    book.append({"signal": signal, "price": price, "time": t})
    log.info(f"  [RSI] PAPER {signal:4s} {symbol:12s} @ {price:.2f}  RSI={rsi_val}  [{t}]")
    if len(book) >= 2:
        prev = book[-2]
        if prev["signal"] == "BUY" and signal == "SELL":
            pnl = price - prev["price"]
            log.info(f"  [RSI] {symbol} PnL: {'+' if pnl>0 else ''}{pnl:.2f} pts  {'WIN' if pnl>0 else 'LOSS'}")
        elif prev["signal"] == "SELL" and signal == "BUY":
            pnl = prev["price"] - price
            log.info(f"  [RSI] {symbol} PnL: {'+' if pnl>0 else ''}{pnl:.2f} pts  {'WIN' if pnl>0 else 'LOSS'}")


# ── Real Order ────────────────────────────────────────────────────────────────

def place_order(symbol, side, qty, token, cid):
    info = DHAN_INFO.get(symbol)
    if not info:
        log.error(f"[RSI] No Dhan info for {symbol}")
        return False
    sec_id, seg = info
    payload = {
        "dhanClientId":      cid,
        "correlationId":     f"RSI_{symbol}_{int(time.time())}",  # RSI prefix — separates from EMA orders
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
            log.info(f"  [RSI] LIVE {side} {symbol} qty={qty}  orderId={resp.get('orderId','?')}")
            return True
        log.error(f"  [RSI] {side} {symbol} failed: {resp.get('remarks') or r.text[:150]}")
        return False
    except Exception as e:
        log.error(f"  [RSI] Order exception: {e}")
        return False


# ── Main Loop ─────────────────────────────────────────────────────────────────

def run(paper_mode=True):
    mode_str = "PAPER" if paper_mode else "LIVE"
    log.info("=" * 60)
    log.info(f"RSI Trader  |  Mode: {mode_str}  |  TF: 5-min")
    log.info("=" * 60)

    positions    = {}
    trades_today = {}
    last_date    = None

    while True:
        try:
            now = ist_now()
            tc  = load_config()

            if last_date != now.date():
                last_date    = now.date()
                positions    = {}
                trades_today = {}
                log.info(f"── New day: {last_date} ──")

            if not tc.get("active", True):
                log.info("[RSI] Paused")
                time.sleep(60)
                continue

            if not is_market_open():
                log.info(f"[RSI] Market closed ({now.strftime('%H:%M')} IST)")
                time.sleep(60)
                continue

            period    = int(tc.get("rsi_period", 14))
            oversold  = float(tc.get("oversold", 30))
            overbought= float(tc.get("overbought", 70))
            qty       = int(tc.get("qty", 1))
            max_t     = int(tc.get("max_trades_per_symbol", 2))
            sym_list  = tc.get("symbols", list(SYMBOLS.keys()))

            token, cid = load_creds()

            if is_exit_time():
                for sym, pos in list(positions.items()):
                    if pos != 0:
                        log.info(f"[RSI] 3:15 square-off: {sym}")
                        side = "SELL" if pos > 0 else "BUY"
                        if paper_mode:
                            paper_trade(sym, side, 0, 0)
                        else:
                            place_order(sym, side, qty, token, cid)
                        positions[sym] = 0
                time.sleep(300)
                continue

            log.info(f"── [RSI] Scanning {len(sym_list)} symbols | RSI({period}) OB={overbought} OS={oversold} ──")

            for sym in sym_list:
                if sym not in SYMBOLS:
                    continue

                t_count = trades_today.get(sym, 0)
                pos     = positions.get(sym, 0)

                if t_count >= max_t:
                    continue

                df = fetch_candles(sym)
                if df is None or df.empty:
                    log.warning(f"  [RSI] {sym:12s} no data")
                    continue

                signal, rsi_val = compute_signal(df, period, oversold, overbought)
                last_close      = float(df["close"].iloc[-2])

                log.info(f"  [RSI] {sym:12s} close={last_close:.2f}  RSI={rsi_val or '?':>5}  signal={signal or 'NONE':4s}  pos={pos:+d}")

                if signal == "BUY" and pos <= 0:
                    if pos < 0:
                        if paper_mode: paper_trade(sym, "BUY", last_close, rsi_val)
                        else: place_order(sym, "BUY", qty, token, cid)
                        trades_today[sym] = t_count + 1
                    if paper_mode: paper_trade(sym, "BUY", last_close, rsi_val)
                    else: place_order(sym, "BUY", qty, token, cid)
                    positions[sym]    = 1
                    trades_today[sym] = trades_today.get(sym, 0) + 1

                elif signal == "SELL" and pos >= 0:
                    if pos > 0:
                        if paper_mode: paper_trade(sym, "SELL", last_close, rsi_val)
                        else: place_order(sym, "SELL", qty, token, cid)
                        trades_today[sym] = trades_today.get(sym, 0) + 1
                    if paper_mode: paper_trade(sym, "SELL", last_close, rsi_val)
                    else: place_order(sym, "SELL", qty, token, cid)
                    positions[sym]    = -1
                    trades_today[sym] = trades_today.get(sym, 0) + 1

        except KeyboardInterrupt:
            log.info("[RSI] Stopped")
            break
        except Exception as e:
            log.error(f"[RSI] Loop error: {e}")

        time.sleep(300)   # 5-min TF — scan har 5 minute mein


# ── Entry ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true")
    ap.add_argument("--live",  action="store_true")
    args = ap.parse_args()

    if args.live:
        print("\n⚠️  RSI LIVE MODE — real orders!")
        print("Ctrl+C within 5s to cancel...\n")
        time.sleep(5)
        run(paper_mode=False)
    else:
        print("\n📝 RSI PAPER MODE — signals only\n")
        run(paper_mode=True)
