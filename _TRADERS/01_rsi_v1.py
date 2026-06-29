#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  01_rsi_v1.py  —  RSI Oversold/Overbought Strategy  (Version 1)        ║
# ║  Pine counterpart : ../_PINE/01_rsi_v1.pine                            ║
# ║  Config file      : ../nifty_config.json  →  key: "rsi_v1"             ║
# ╚══════════════════════════════════════════════════════════════════════════╝
#
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  CONDITIONS SUMMARY                                                     │
# │                                                                         │
# │  LONG ENTRY  : RSI < 30 (oversold) ke baad RSI > 30 cross kare         │
# │                → BUY ATM CE  (price bounce expected)                   │
# │                                                                         │
# │  SHORT ENTRY : RSI > 70 (overbought) ke baad RSI < 70 cross kare       │
# │                → BUY ATM PE  (price reversal expected)                 │
# │                                                                         │
# │  LONG EXIT   : RSI >= 50 (mid zone)  → CE close karo                   │
# │  SHORT EXIT  : RSI <= 50 (mid zone)  → PE close karo                   │
# │                                                                         │
# │  FORCE EXIT  : 3:15 PM → saari open positions band karo (intraday)     │
# └─────────────────────────────────────────────────────────────────────────┘
#
# VERSION HISTORY:
#   v1 (2026-06-19) : Initial build — RSI crossover/crossunder strategy

# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 1 — SETUP
#  Imports, constants, paths, logging
# ═══════════════════════════════════════════════════════════════════════════

import json
import logging
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

# IPv4 force — VPS pe IPv6 hoti hai, Dhan reject karta hai (error DH-905)
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

# Paths — BASE_DIR = project root (parent of _TRADERS/)
BASE_DIR    = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"      # Dhan JWT token
TC_FILE     = BASE_DIR / "nifty_config.json"          # strategy params

# dhan_master root pe hai — import ke liye path add karo
sys.path.insert(0, str(BASE_DIR))
import dhan_master
from brokers import kite_broker

# Market timing (IST)
MARKET_OPEN  = (9,  16)   # 9:16 AM — market chalu
MARKET_CLOSE = (15, 25)   # 3:25 PM — hard stop
FORCE_EXIT   = (15, 15)   # 3:15 PM — sab positions band

# Dhan API endpoints
ORDERS_URL   = "https://api.dhan.co/v2/orders"
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

# Timeframe string → minutes
TF_MAP = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}

# Fallback symbol list (asli list config se aati hai)
DEFAULT_SYMBOLS = [
    "NIFTY", "BANKNIFTY",
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
    "AXISBANK", "BAJFINANCE", "WIPRO", "KOTAKBANK", "LT",
    "MARUTI", "HINDUNILVR", "ITC", "ADANIENT", "SUNPHARMA", "TITAN",
    "ULTRACEMCO", "POWERGRID", "NTPC", "ONGC", "NESTLEIND",
]


def _make_logger(strategy_id):
    """Har strategy ka alag log file. propagate=False → duplicate lines nahi."""
    log_file = BASE_DIR / "logs" / f"{strategy_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger(strategy_id)
    lg.setLevel(logging.INFO)
    lg.propagate = False   # root logger tak mat jane do — warna line 2x print hoti hai
    if not lg.handlers:
        fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(log_file)   # file mein likhega
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()         # terminal mein dikhega
        sh.setFormatter(fmt)
        lg.addHandler(fh)
        lg.addHandler(sh)
    return lg


def ist_now():
    """UTC → IST (UTC+5:30). Hamesha IST use karo."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)

def is_market_open():
    t = (ist_now().hour, ist_now().minute)
    return MARKET_OPEN <= t < MARKET_CLOSE

def is_force_exit_time():
    t = (ist_now().hour, ist_now().minute)
    return t >= FORCE_EXIT

def load_creds():
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def dhan_headers(token, cid):
    return {"access-token": token, "client-id": cid, "Content-Type": "application/json"}


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 2 — CONFIG
#  nifty_config.json se strategy params pado (hot-reload — har loop mein)
# ═══════════════════════════════════════════════════════════════════════════

def load_config(strategy_id):
    """
    nifty_config.json se yeh block padho:
    {
      "rsi_v1": {
        "timeframe"            : "5m",   ← candle size
        "rsi_period"           : 14,     ← RSI window
        "oversold"             : 30,     ← entry zone (CE)
        "overbought"           : 70,     ← entry zone (PE)
        "rsi_exit"             : 50,     ← exit level
        "qty"                  : 1,      ← lots (NOT shares — lot size CSV se aata hai)
        "max_trades_per_symbol": 1,      ← aaj kitni baar ek symbol pe trade karna hai
        "strike_offset"        : 0,      ← 0=ATM, 1=1-OTM, -1=1-ITM
        "symbols"              : [...]   ← kaunse symbols scan karne hain
      }
    }
    """
    default = {
        "active": True, "timeframe": "5m",
        "rsi_period": 14, "oversold": 30, "overbought": 70, "rsi_exit": 50,
        "qty": 1, "max_trades_per_symbol": 1, "strike_offset": 0,
        "symbols": DEFAULT_SYMBOLS,
    }
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**default, **cfg.get(strategy_id, {})}
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 3 — DATA FETCH
#  Dhan intraday API se aaj ke candles lo
# ═══════════════════════════════════════════════════════════════════════════

def fetch_candles(symbol, tf, token, cid):
    """
    Symbol name → Dhan intraday OHLC DataFrame (aaj ka din)

    Steps:
      1. dhan_master se symbol ka sec_id + segment nikalo (CSV se)
      2. Dhan /v2/charts/intraday ko POST karo
      3. Response → pandas DataFrame (columns: time, open, high, low, close)

    Returns None agar data nahi mila.
    """
    info = dhan_master.get_equity_info(symbol)
    if not info:
        return None
    sec_id, seg, inst = info
    interval = TF_MAP.get(tf, 5)

    # Cross-process cache first — range_trader.py / rsi_trader.py may have
    # already fetched this exact sec_id+interval within the last few
    # seconds; reuse it instead of an independent Dhan call. This file had
    # NEITHER caching nor rate-limiting before — the actual source of the
    # SBIN DH-904 hits (LESSONS.md TRAP #2 v3). See shared_candle_cache.py.
    try:
        import shared_candle_cache
        cached = shared_candle_cache.get(sec_id, interval, max_age=20.0)
        if cached:
            df = pd.DataFrame(cached)
            df["time"] = pd.to_datetime(df["time"])
            return df.dropna() if not df.empty else None
    except Exception:
        pass

    try:
        today = ist_now().strftime("%Y-%m-%d")
        body  = {
            "securityId":      sec_id,
            "exchangeSegment": seg,       # NSE_EQ ya IDX_I (index)
            "instrument":      inst,      # EQUITY ya INDEX
            "interval":        interval,  # minutes
            "fromDate":        today,
            "toDate":          today,
        }
        try:
            import dhan_rate_limiter as _rl
            _rl.acquire("candle")
        except Exception:
            _rl = None
        r = requests.post(INTRADAY_URL, json=body,
                          headers={"access-token": token, "client-id": cid,
                                   "Content-Type": "application/json"},
                          timeout=10)
        if r.status_code == 429 and _rl:
            _rl.note_429()
        if r.status_code != 200:
            return None

        d    = r.json()
        rows = list(zip(d.get("timestamp", []), d.get("open", []),
                        d.get("high", []), d.get("low", []), d.get("close", [])))
        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])
        df["time"] = (pd.to_datetime(df["time"], unit="s", utc=True)
                        .dt.tz_convert("Asia/Kolkata")
                        .dt.tz_localize(None))
        df = df.dropna()
        if df.empty:
            return None
        try:
            import shared_candle_cache
            cache_df = df.copy()
            cache_df["time"] = cache_df["time"].astype(str)
            shared_candle_cache.put(sec_id, interval, cache_df.to_dict("records"))
        except Exception:
            pass
        return df
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 4 — CORE SIGNAL LOGIC  ★ (yahan strategy ka dimaag hai)
#
#  CONDITIONS:
#    LONG ENTRY  : RSI < 30 ke baad > 30 cross (crossover)  → return "BUY"
#    SHORT ENTRY : RSI > 70 ke baad < 70 cross (crossunder) → return "SELL"
#    LONG EXIT   : RSI >= 50                                 → return "EXIT"
#    SHORT EXIT  : RSI <= 50                                 → return "EXIT"
#    HOLD        : koi condition match nahi                  → return None
# ═══════════════════════════════════════════════════════════════════════════

def _compute_rsi(series, period):
    """
    Wilder RSI — Pine ke ta.rsi() se exactly match karta hai.

    Wilder smoothing formula:
      alpha = 1/period  →  EWM com = period - 1
    (agar span=period use karo toh Pine se values alag aayengi)
    """
    delta = series.diff()
    gain  = delta.clip(lower=0)          # sirf upar wale moves
    loss  = (-delta).clip(lower=0)       # sirf neeche wale moves (positive rakho)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, float("inf"))   # loss=0 → RSI=100
    return 100 - (100 / (1 + rs))


def get_signal(df, period, oversold, overbought, rsi_exit, pos):
    """
    Ek symbol ke candles dekhkar batao: kya karna hai?

    Parameters:
      df         : OHLC DataFrame (Dhan se aaya)
      period     : RSI window (default 14)
      oversold   : CE entry level (default 30)
      overbought : PE entry level (default 70)
      rsi_exit   : exit level (default 50)
      pos        : current position → 0=flat, +1=CE open, -1=PE open

    Returns:
      ("BUY",  rsi_value)  ← CE kharido
      ("SELL", rsi_value)  ← PE kharido
      ("EXIT", rsi_value)  ← position band karo
      (None,   rsi_value)  ← kuch mat karo
    """
    if len(df) < period + 5:
        return None, None

    rsi = _compute_rsi(df["close"], period)
    cur = float(rsi.iloc[-2])   # last CLOSED bar (confirmed)
    prv = float(rsi.iloc[-3])   # usse pehle wala bar

    # ── EXIT (pehle check karo — position hai toh entry nahi) ──────────────
    if pos == +1 and cur >= rsi_exit:   # CE open, RSI 50 ke upar → exit
        return "EXIT", round(cur, 1)
    if pos == -1 and cur <= rsi_exit:   # PE open, RSI 50 ke neeche → exit
        return "EXIT", round(cur, 1)
    if pos != 0:
        return None, round(cur, 1)      # position hai, wait karo

    # ── ENTRY (tabhi jab flat ho) ───────────────────────────────────────────
    #   LONG ENTRY : RSI 30 se neeche tha, ab 30 ke upar aaya
    if prv <= oversold and cur > oversold:
        return "BUY", round(cur, 1)

    #   SHORT ENTRY : RSI 70 se upar tha, ab 70 ke neeche aaya
    if prv >= overbought and cur < overbought:
        return "SELL", round(cur, 1)

    return None, round(cur, 1)


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 5 — ORDER EXECUTION
#  Dhan REST API pe actual order bhejo
# ═══════════════════════════════════════════════════════════════════════════

def _order_broker(cfg):
    """Config se order broker decide karo. Default = dhan."""
    return cfg.get("order_broker", "dhan").lower()   # "dhan" ya "kite"


def place_order(side, trad_sym, sec_id, qty, token, cid, log, cfg=None):
    """
    Order bhejo — broker config ke hisaab se Dhan ya Kite pe.

    order_broker = "dhan" (default) → Dhan REST API
    order_broker = "kite"           → Zerodha Kite Connect

    side     : "BUY" ya "SELL"
    trad_sym : Dhan format "NIFTY-Jun2026-23950-CE"
    sec_id   : Dhan internal ID (sirf Dhan ke liye chahiye)
    qty      : actual shares (lots × lot_size — CSV se, kabhi hardcode nahi)
    """
    broker = _order_broker(cfg or {})

    if broker == "kite":
        # Dhan trad_sym → Kite format convert karo
        kite_sym = kite_broker.dhan_sym_to_kite(trad_sym)
        log.info(f"    [KITE→] {side} {kite_sym}  qty={qty}  (from {trad_sym})")
        order_id = kite_broker.place_order(side, kite_sym, qty, log_ref=log)
        return order_id is not None

    # ── Default: Dhan ────────────────────────────────────────────────────────
    payload = {
        "dhanClientId":      cid,
        "correlationId":     f"RSI_{trad_sym}_{int(time.time())}",
        "transactionType":   side,
        "exchangeSegment":   "NSE_FNO",
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
        "amoTime":           "OPEN",
    }
    try:
        r    = requests.post(ORDERS_URL, json=payload,
                             headers=dhan_headers(token, cid), timeout=20)
        resp = r.json() if r.content else {}
        if r.status_code == 200:
            log.info(f"    [DHAN] {side} {trad_sym}  qty={qty}  orderId={resp.get('orderId','?')}")
            return True
        log.error(f"    [ORDER ERR] {side} {trad_sym}: {resp.get('remarks') or r.text[:120]}")
        return False
    except Exception as e:
        log.error(f"    [ORDER EXC] {e}")
        return False


def close_position(sym, active_opts, token, cid, paper_mode, rsi_val, log, cfg=None):
    """Open option position band karo (EXIT order)."""
    if sym not in active_opts:
        log.info(f"    [PAPER] EXIT {sym}  RSI={rsi_val}")
        return
    o          = active_opts.pop(sym)
    close_side = "SELL" if o["side"] == "BUY" else "BUY"
    if not paper_mode:
        place_order(close_side, o["trad_sym"], o["sec_id"], o["qty"], token, cid, log, cfg)
    else:
        broker = _order_broker(cfg or {})
        log.info(f"    [PAPER/{broker.upper()}] EXIT {o['trad_sym']}  qty={o['qty']}  RSI={rsi_val}")


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 6 — MAIN LOOP
#  Har candle ke baad saare symbols scan karo, signals pe act karo
# ═══════════════════════════════════════════════════════════════════════════

def run(paper_mode=True, strategy_id="rsi_v1"):
    log = _make_logger(strategy_id)
    log.info("=" * 62)
    log.info(f"  01_rsi_v1.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    # In-memory state — naye din pe reset hota hai
    positions    = {}   # sym → +1 (CE open) / -1 (PE open) / 0 (flat)
    active_opts  = {}   # sym → {sec_id, trad_sym, side, qty}
    trades_today = {}   # sym → int (aaj kitni baar trade hua)
    last_date    = None

    while True:
        try:
            now = ist_now()
            tc  = load_config(strategy_id)          # har loop mein fresh config

            # Naya din → state reset
            if last_date != now.date():
                last_date    = now.date()
                positions    = {}
                active_opts  = {}
                trades_today = {}
                log.info(f"── New day: {last_date} ──")

            # Paused check
            if not tc.get("active", True):
                log.info("[RSI] Paused — active=false in config")
                time.sleep(60)
                continue

            # Market hours check
            if not is_market_open():
                log.info(f"[RSI] Market closed  ({now.strftime('%H:%M')} IST)")
                time.sleep(60)
                continue

            # Config values
            tf         = tc.get("timeframe", "5m")
            period     = int(tc.get("rsi_period", 14))
            oversold   = float(tc.get("oversold", 30))
            overbought = float(tc.get("overbought", 70))
            rsi_exit   = float(tc.get("rsi_exit", 50))
            lots       = int(tc.get("qty", 1))
            max_t      = int(tc.get("max_trades_per_symbol", 1))
            offset     = int(tc.get("strike_offset", 0))
            sym_list   = tc.get("symbols", DEFAULT_SYMBOLS)
            if isinstance(sym_list, str):
                sym_list = [s.strip() for s in sym_list.split(",") if s.strip()]

            token, cid = load_creds()
            tf_secs    = TF_MAP.get(tf, 5) * 60     # sleep = 1 candle duration

            # ── 3:15 PM force-exit ───────────────────────────────────────────
            if is_force_exit_time():
                for sym, pos in list(positions.items()):
                    if pos == 0:
                        continue
                    log.info(f"[FORCE EXIT 3:15] {sym}")
                    close_position(sym, active_opts, token, cid, paper_mode, "3:15", log)
                    positions[sym] = 0
                time.sleep(300)
                continue

            # ── Config snapshot log (dashboard mein clearly dikhta hai) ──────
            log.info(
                f"[CONFIG] TF={tf}  RSI({period})  "
                f"Entry: OS<{oversold} / OB>{overbought}  "
                f"Exit@RSI={rsi_exit}  Lots={lots}  MaxTrades={max_t}  Symbols={len(sym_list)}"
            )
            log.info(f"── Scanning {len(sym_list)} symbols ──")

            # watch snapshot — har scan ke baad update hoga (dashboard pe dikhta hai)
            watch_rows = []

            # ── Symbol-by-symbol scan ────────────────────────────────────────
            for sym in sym_list:
                try:
                    import dhan_rate_limiter as _rl_ctx
                    _rl_ctx.set_context(f"{strategy_id}:{sym}")
                except Exception:
                    pass
                t_count = trades_today.get(sym, 0)
                pos     = positions.get(sym, 0)

                df = fetch_candles(sym, tf, token, cid)
                if df is None or df.empty:
                    log.warning(f"  {sym:14s} no data")
                    continue

                last_close          = float(df["close"].iloc[-2])
                signal, rsi_val     = get_signal(df, period, oversold, overbought, rsi_exit, pos)

                log.info(
                    f"  {sym:14s}  close={last_close:>9.1f}"
                    f"  RSI={str(rsi_val or '?'):>5}"
                    f"  → {signal or 'HOLD':4s}"
                    f"  pos={pos:+d}  trades={t_count}"
                )

                # Watch snapshot entry — signal ke baad bhi record karo
                if rsi_val is not None:
                    # Zone classify karo (entry ke kitna paas hai?)
                    if rsi_val <= oversold:
                        zone = "OVERSOLD"      # CE entry zone — crossover ka wait
                    elif rsi_val <= oversold + 5:
                        zone = "NEAR_OS"       # approaching oversold (warning zone)
                    elif rsi_val >= overbought:
                        zone = "OVERBOUGHT"    # PE entry zone — crossover ka wait
                    elif rsi_val >= overbought - 5:
                        zone = "NEAR_OB"       # approaching overbought
                    else:
                        zone = "NEUTRAL"

                    watch_rows.append({
                        "sym":    sym,
                        "close":  round(last_close, 1),
                        "rsi":    rsi_val,
                        "zone":   zone,
                        "pos":    pos,        # 0=flat, +1=CE open, -1=PE open
                        "signal": signal or "",
                    })

                # ── EXIT ────────────────────────────────────────────────────
                if signal == "EXIT":
                    close_position(sym, active_opts, token, cid, paper_mode, rsi_val, log, tc)
                    positions[sym]    = 0
                    trades_today[sym] = t_count + 1
                    continue

                # Already in position / max trades hit / no signal
                if pos != 0 or t_count >= max_t or signal is None:
                    continue

                # ── ENTRY ───────────────────────────────────────────────────
                opt_type = "CE" if signal == "BUY" else "PE"
                log.info(f"  ★ {signal} on {sym} → BUY {opt_type}")

                # Option contract: sec_id + trad_sym + lot_size from Dhan CSV
                result = dhan_master.get_option_contract(sym, last_close, opt_type, offset)
                if not result or not result[0]:
                    log.error(f"  {sym} {opt_type} — contract not found in master CSV")
                    continue

                sec_id, trad_sym, lot_size = result
                actual_qty = lots * (lot_size or 1)   # lot_size CSV se, kabhi hardcode nahi

                if not paper_mode:
                    ok = place_order("BUY", trad_sym, sec_id, actual_qty, token, cid, log, tc)
                    if ok:
                        active_opts[sym]  = {"sec_id": sec_id, "trad_sym": trad_sym,
                                              "side": "BUY", "qty": actual_qty}
                        positions[sym]    = +1 if signal == "BUY" else -1
                        trades_today[sym] = t_count + 1
                else:
                    log.info(
                        f"    [PAPER] BUY {opt_type} {trad_sym}"
                        f"  qty={actual_qty} ({lots}L × {lot_size})"
                        f"  @ ~{last_close:.1f}  RSI={rsi_val}"
                    )
                    active_opts[sym]  = {"sec_id": sec_id, "trad_sym": trad_sym,
                                          "side": "BUY", "qty": actual_qty}
                    positions[sym]    = +1 if signal == "BUY" else -1
                    trades_today[sym] = t_count + 1

            # ── Watch file update ────────────────────────────────────────
            # Zone priority sort: OVERSOLD / OVERBOUGHT pehle, phir NEAR, phir NEUTRAL
            _zone_order = {"OVERSOLD": 0, "OVERBOUGHT": 1, "NEAR_OS": 2, "NEAR_OB": 3, "NEUTRAL": 4}
            watch_rows.sort(key=lambda r: (_zone_order.get(r["zone"], 9), r["rsi"]))
            watch_data = {
                "updated":  now.strftime("%Y-%m-%d %H:%M:%S"),
                "strategy": strategy_id,
                "tf":       tf,
                "levels":   {"oversold": oversold, "overbought": overbought, "exit": rsi_exit},
                "symbols":  watch_rows,
            }
            watch_file = BASE_DIR / "data" / f"{strategy_id}_watch.json"
            watch_file.write_text(json.dumps(watch_data, indent=2))

        except KeyboardInterrupt:
            log.info("[RSI] Stopped by user")
            break
        except Exception as e:
            log.error(f"[RSI] Loop error: {e}", exc_info=True)

        time.sleep(tf_secs)


# ═══════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
#  Dashboard isko subprocess se chalata hai:
#    python _TRADERS/01_rsi_v1.py --paper --id rsi_v1
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="RSI Strategy Trader")
    ap.add_argument("--paper", action="store_true", help="Paper mode (default)")
    ap.add_argument("--live",  action="store_true", help="LIVE mode — real orders!")
    ap.add_argument("--id",    default="rsi_v1",    help="Key in nifty_config.json")
    args = ap.parse_args()

    if args.live:
        print("\n⚠️  LIVE MODE — REAL ORDERS WILL BE PLACED ON DHAN!")
        print("Ctrl+C within 5 seconds to cancel...\n")
        time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n[PAPER MODE]  strategy_id = {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
