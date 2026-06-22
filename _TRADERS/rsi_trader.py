#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║  rsi_trader.py  —  RSI Oversold/Overbought Strategy             ║
║  Dashboard se start/stop hota hai (trader_dashboard.py)         ║
╚══════════════════════════════════════════════════════════════════╝

STRATEGY LOGIC (simple):
  ┌─────────────────────────────────────────────────────────────┐
  │  RSI neeche se 30 cross kare (oversold se upar aaye)        │
  │    → BUY ATM CE  (expect: price upar jayega)                │
  │                                                             │
  │  RSI upar se 70 cross kare (overbought se neeche aaye)      │
  │    → BUY ATM PE  (expect: price neeche jayega)              │
  │                                                             │
  │  RSI 50 ke paas aaye (middle zone)                          │
  │    → EXIT current position                                  │
  │                                                             │
  │  3:15 PM — har open position force-exit                     │
  └─────────────────────────────────────────────────────────────┘

PINE COUNTERPART:  ../_PINE/rsi_v1.pine  (v2 — 3:15 EOD force-exit + no re-entry
  added 2026-06-20, same rule as AUTO_EXIT_AT below; same logic, TV pe test karo)

BACKTEST PATH:  ../_TOOLS/backtest_engine.py uses its own _rsi_signal_backtest()
  (not this file's compute_signal — that one's bar convention is for the LIVE
  feed's still-forming last candle). The 3:15 cutoff there now exits AT bar i's
  own close, not via next-bar fill — next-bar fill pushed the cutoff exit to
  15:20+, which TV (no such bug) never matched.

CONFIG:  ../nifty_config.json  →  "rsi_v1": { ... }
  Saari values wahan se aati hain — yahan kuch hardcode nahi hai.
  Config change karo, agli cycle mein auto-pickup hoga (hot-reload).

HOW TO RUN (command line se):
  python rsi_trader.py --paper       ← paper mode (default)
  python rsi_trader.py --live --id rsi_v1  ← LIVE (real orders!)
"""

import json, logging, socket, sys, time, dhan_master
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd
import requests

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  IPv4 FORCE  —  VPS pe IPv6 default hoti hai, Dhan reject karta
#  hai (error DH-905). Yeh patch socket ko hamesha IPv4 use karata
#  hai. Har trader file ke top mein hona ZAROORI hai.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PATHS
#  BASE_DIR → parent of _TRADERS/ (project root) so config.json
#  aur nifty_config.json sahi jagah milte hain.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BASE_DIR    = Path(__file__).resolve().parent.parent   # CODE3B root
CONFIG_FILE = BASE_DIR / "data" / "config.json"        # Dhan JWT token
TC_FILE     = BASE_DIR / "nifty_config.json"           # strategy params

# dhan_master (sibling at root) — import path set by dashboard already,
# but set here too so this file works standalone as well.
sys.path.insert(0, str(BASE_DIR))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DEFAULT SYMBOL LIST
#  Yeh sirf fallback hai. Asli list nifty_config.json se aati hai.
#  TATAMOTORS hata diya — Dhan pe data available nahi tha.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEFAULT_SYMBOLS = [
    "NIFTY", "BANKNIFTY",
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK", "SBIN",
    "AXISBANK", "BAJFINANCE", "WIPRO", "KOTAKBANK", "LT",
    "MARUTI", "HINDUNILVR", "ITC", "ADANIENT", "SUNPHARMA", "TITAN",
    "ULTRACEMCO", "POWERGRID", "NTPC", "ONGC", "NESTLEIND",
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MARKET_OPEN  = (9, 16)    # market kab se chalu (HH, MM)
MARKET_CLOSE = (15, 25)   # market kab band (hard stop scanning)
AUTO_EXIT_AT = (15, 15)   # 3:15 PM pe sab positions force-close
ORDERS_URL   = "https://api.dhan.co/v2/orders"
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
_TF_MAP      = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}  # timeframe string → minutes


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LOGGING SETUP
#  Har strategy ka apna log file hota hai: logs/rsi_v1.log
#  propagate=False → duplicate lines nahi aate (root logger bypass)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _make_logger(strategy_id):
    log_file = BASE_DIR / "logs" / f"{strategy_id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger(strategy_id)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S")
        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        lg.addHandler(fh)
        lg.addHandler(sh)
    return lg


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SMALL HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ist_now():
    """UTC → IST (UTC+5:30). Har jagah IST use karo, UTC nahi."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)

def is_market_open():
    t = (ist_now().hour, ist_now().minute)
    return MARKET_OPEN <= t < MARKET_CLOSE

def is_exit_time():
    """3:15 PM ke baad True — force-exit trigger."""
    t = (ist_now().hour, ist_now().minute)
    return t >= AUTO_EXIT_AT

def load_creds():
    """Dhan JWT token aur client_id padho (data/config.json se)."""
    cfg = json.loads(CONFIG_FILE.read_text())
    return cfg["jwt_token"], cfg["client_id"]

def hdrs(token, cid):
    return {"access-token": token, "client-id": cid, "Content-Type": "application/json"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG LOADER  (hot-reload — har loop mein call hota hai)
#
#  nifty_config.json structure example:
#  {
#    "rsi_v1": {
#      "timeframe": "5m",     ← candle timeframe
#      "rsi_period": 14,      ← RSI window
#      "oversold": 30,        ← BUY CE signal level
#      "overbought": 70,      ← BUY PE signal level
#      "rsi_exit": 50,        ← exit at this RSI level
#      "qty": 1,              ← 1 = 1 LOT (lot size Dhan CSV se aata hai)
#      "max_trades_per_symbol": 1,
#      "symbols": ["NIFTY", "BANKNIFTY", ...]
#    }
#  }
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def load_config(strategy_id):
    default = {
        "active": True, "timeframe": "5m",
        "rsi_period": 14, "oversold": 30, "overbought": 70, "rsi_exit": 50,
        "qty": 1, "max_trades_per_symbol": 1,
        "instrument": "options", "strike_offset": 0,
        "symbols": DEFAULT_SYMBOLS,
    }
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
        return {**default, **cfg.get(strategy_id, {})}
    except Exception:
        return default


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CANDLE FETCH  (Dhan intraday API)
#
#  Symbol name → dhan_master se sec_id + segment milta hai
#  → Dhan /v2/charts/intraday pe POST karo
#  → OHLC rows DataFrame bana ke wapas do
#
#  NOTE: yfinance kabhi mat use karo — Dhan API hi single source.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_candles(symbol, tf, token, cid):
    # Step 1: symbol name se Dhan sec_id + segment nikalo (CSV se)
    info = dhan_master.get_equity_info(symbol)
    if not info:
        return None   # symbol CSV mein nahi mila
    sec_id, seg, inst = info
    interval = _TF_MAP.get(tf, 5)

    try:
        today = ist_now().strftime("%Y-%m-%d")
        body  = {
            "securityId":      sec_id,
            "exchangeSegment": seg,       # NSE_EQ ya IDX_I (index ke liye)
            "instrument":      inst,      # EQUITY ya INDEX
            "interval":        interval,  # minutes mein
            "fromDate":        today,
            "toDate":          today,
        }
        r = requests.post(INTRADAY_URL, json=body,
                          headers={"access-token": token, "client-id": cid,
                                   "Content-Type": "application/json"},
                          timeout=10)
        if r.status_code != 200:
            return None

        d    = r.json()
        ts   = d.get("timestamp", [])
        rows = list(zip(ts, d.get("open",[]), d.get("high",[]),
                        d.get("low",[]), d.get("close",[])))
        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["time","open","high","low","close"])
        # UTC epoch → IST datetime
        df["time"] = (pd.to_datetime(df["time"], unit="s", utc=True)
                        .dt.tz_convert("Asia/Kolkata")
                        .dt.tz_localize(None))
        return df.dropna()
    except Exception:
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RSI CALCULATION  (Wilder smoothing — Pine ta.rsi se match karta)
#
#  Pine ka ta.rsi() Wilder method use karta hai:
#    alpha = 1/period  →  EWM com = period - 1
#  Agar simple EWM (span=period) use karo toh values alag aayengi
#  aur Pine vs Python mismatch hoga. Isliye com=period-1.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def compute_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)          # sirf positive moves
    loss  = (-delta).clip(lower=0)       # sirf negative moves (positive banao)
    avg_g = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_l = loss.ewm(com=period - 1, min_periods=period).mean()
    rs    = avg_g / avg_l.replace(0, float("inf"))  # loss=0 → RS=inf → RSI=100
    return 100 - (100 / (1 + rs))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SIGNAL LOGIC
#
#  Hum CONFIRMED bar use karte hain (iloc[-2], na ki current bar):
#    - iloc[-1] = abhi chal raha bar (candle close nahi hua)
#    - iloc[-2] = pichla closed bar  ← yahi use karo
#  Yeh Pine ke bar_index ke saath match karta hai.
#
#  pos values:
#    0  = koi position nahi
#   +1  = CE position open hai (bullish)
#   -1  = PE position open hai (bearish)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def compute_signal(df, period, oversold, overbought, rsi_exit, pos):
    if len(df) < period + 5:
        return None, None   # enough bars nahi hain RSI ke liye

    rsi = compute_rsi(df["close"], period)
    cur = float(rsi.iloc[-2])   # last closed bar ka RSI
    prv = float(rsi.iloc[-3])   # usse pehle wala bar

    # ── EXIT check (position open ho toh pehle check karo) ──────────
    if pos == 1  and cur >= rsi_exit:   # CE position, RSI 50 ke upar → exit
        return "EXIT", round(cur, 1)
    if pos == -1 and cur <= rsi_exit:   # PE position, RSI 50 ke neeche → exit
        return "EXIT", round(cur, 1)
    if pos != 0:
        return None, round(cur, 1)      # position hai, sirf exit condition check ki

    # ── ENTRY check (tabhi jab koi position open nahi) ──────────────
    # RSI 30 ke neeche tha, ab upar aaya → oversold se recovery → BUY CE
    if prv <= oversold and cur > oversold:
        return "BUY", round(cur, 1)

    # RSI 70 ke upar tha, ab neeche aaya → overbought reversal → BUY PE
    if prv >= overbought and cur < overbought:
        return "SELL", round(cur, 1)

    return None, round(cur, 1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ORDER PLACEMENT  (Dhan REST API)
#
#  INTRADAY MARKET order bhejta hai.
#  qty = actual shares (lots × lot_size) — lot_size Dhan CSV se aata hai.
#  correlationId = aapka label jo Dhan orderbook mein dikhta hai.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def place_order(sym, side, qty, token, cid, sec_id, seg, trad_sym, log):
    payload = {
        "dhanClientId":     cid,
        "correlationId":    f"RSI_{trad_sym}_{int(time.time())}",
        "transactionType":  side,            # "BUY" ya "SELL"
        "exchangeSegment":  seg,             # "NSE_FNO"
        "productType":      "INTRADAY",
        "orderType":        "MARKET",
        "validity":         "DAY",
        "tradingSymbol":    trad_sym,        # e.g. "NIFTY-Jun2026-23950-CE"
        "securityId":       sec_id,          # Dhan internal ID
        "quantity":         qty,             # actual shares (not lots)
        "disclosedQuantity": 0,
        "price": 0, "triggerPrice": 0,
        "afterMarketOrder": False, "amoTime": "OPEN",
    }
    try:
        r    = requests.post(ORDERS_URL, json=payload, headers=hdrs(token, cid), timeout=20)
        resp = r.json() if r.content else {}
        if r.status_code == 200:
            log.info(f"  [LIVE] {side} {trad_sym} qty={qty}  orderId={resp.get('orderId','?')}")
            return True
        log.error(f"  [ORDER ERR] {side} {trad_sym}: {resp.get('remarks') or r.text[:120]}")
        return False
    except Exception as e:
        log.error(f"  [ORDER EXC] {e}")
        return False


MARKETFEED_LTP = "https://api.dhan.co/v2/marketfeed/ltp"

def _opt_ltp(sec_id, token, cid):
    """Option ka LIVE premium (Dhan marketfeed). Retry 3x (rate-limit). 0 = nahi mila.
    NOTE: ye option premium hai — underlying (stock) price NAHI."""
    if not sec_id:
        return 0.0
    for _i in range(3):
        try:
            r = requests.post(MARKETFEED_LTP, json={"NSE_FNO": [int(sec_id)]},
                              headers=hdrs(token, cid), timeout=5)
            if r.status_code == 200:
                data = r.json().get("data", {}).get("NSE_FNO", {})
                for v in (data.values() if isinstance(data, dict) else []):
                    ltp = float(v.get("last_price") or v.get("ltp") or 0)
                    if ltp:
                        return ltp
        except Exception:
            pass
        time.sleep(1.1)
    return 0.0


def _record(side, qty, price, mode, trad_sym, sec_id, strategy_id, status, log=None):
    """order_store me ek leg record karo → dashboard ke 'Orders & P&L' tab me RSI
    trades dikhein (range_trader jaisa). Best-effort — kabhi raise nahi karta.
    price=0 (premium na mila) ho to record NAHI karta — warna jhooth P&L banega."""
    if not price:
        if log:
            log.warning(f"  [REC SKIP] {side} {trad_sym} — premium 0, order_store me record nahi")
        return
    try:
        if str(BASE_DIR) not in sys.path:
            sys.path.insert(0, str(BASE_DIR))
        import order_store
        order_store.record(side, qty, price, source='strategy', strategy=strategy_id,
            mode=mode, broker='dhan', symbol=(trad_sym.split('-')[0] if trad_sym else ''),
            instrument='options', trad_sym=trad_sym, sec_id=str(sec_id), segment='NSE_FNO',
            correlation_id=f'RSI_{trad_sym}_{int(time.time())}', status=status)
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN LOOP
#
#  Flow har cycle mein:
#    1. Config hot-reload (nifty_config.json)
#    2. Market hours check
#    3. 3:15 PM → force-exit sab positions
#    4. Har symbol pe: candles fetch → RSI compute → signal check
#    5. Signal → option contract nikalo → order (ya paper log)
#    6. tf_secs ke baad phir se (5m → 300s sleep)
#
#  State in-memory hai — restart pe reset hota hai (positions, trades).
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def run(paper_mode=True, strategy_id="rsi_v1"):
    log      = _make_logger(strategy_id)
    mode_str = "PAPER" if paper_mode else "LIVE"
    log.info("=" * 60)
    log.info(f"RSI Trader  |  ID: {strategy_id}  |  Mode: {mode_str}")
    log.info("=" * 60)

    # in-memory state (resets each new trading day)
    positions    = {}   # sym → +1 (CE open) / -1 (PE open) / 0 (flat)
    active_opts  = {}   # sym → {sec_id, trad_sym, side, qty}
    trades_today = {}   # sym → int (kitni baar trade hua aaj)
    last_date    = None

    while True:
        try:
            now = ist_now()
            tc  = load_config(strategy_id)   # har loop mein fresh config

            # ── Naya din → sab state reset ───────────────────────────
            if last_date != now.date():
                last_date    = now.date()
                positions    = {}
                active_opts  = {}
                trades_today = {}
                log.info(f"── New day: {last_date} ──")

            # ── Paused check ─────────────────────────────────────────
            if not tc.get("active", True):
                log.info("[RSI] Paused (active=false in config)")
                time.sleep(60)
                continue

            # ── Market hours check ───────────────────────────────────
            if not is_market_open():
                log.info(f"[RSI] Market closed ({now.strftime('%H:%M')} IST)")
                time.sleep(60)
                continue

            # ── Config values (dashboard se change kar sakte ho) ─────
            tf         = tc.get("timeframe", "5m")
            period     = int(tc.get("rsi_period", 14))
            oversold   = float(tc.get("oversold", 30))
            overbought = float(tc.get("overbought", 70))
            rsi_exit   = float(tc.get("rsi_exit", 50))
            qty        = int(tc.get("qty", 1))          # lots (not shares)
            max_t      = int(tc.get("max_trades_per_symbol", 1))
            offset     = int(tc.get("strike_offset", 0))  # ATM±N strikes
            sym_list   = tc.get("symbols", DEFAULT_SYMBOLS)
            if isinstance(sym_list, str):
                sym_list = [s.strip() for s in sym_list.split(",") if s.strip()]

            token, cid = load_creds()
            tf_secs    = _TF_MAP.get(tf, 5) * 60   # loop sleep = 1 candle time

            # ── 3:15 PM force-exit ───────────────────────────────────
            if is_exit_time():
                for sym, pos in list(positions.items()):
                    if pos == 0:
                        continue
                    log.info(f"[RSI] 3:15 square-off: {sym}")
                    if sym in active_opts:
                        o         = active_opts[sym]
                        close_side = "SELL" if o["side"] == "BUY" else "BUY"
                        exit_qty  = o.get("qty", qty)
                        exit_prem = _opt_ltp(o["sec_id"], token, cid)
                        if not paper_mode:
                            place_order(sym, close_side, exit_qty, token, cid,
                                        o["sec_id"], "NSE_FNO", o["trad_sym"], log)
                            _record(close_side, exit_qty, exit_prem, "live", o["trad_sym"], o["sec_id"], strategy_id, "filled", log)
                        else:
                            log.info(f"  [PAPER] 3:15 EXIT {o['trad_sym']}  qty={exit_qty}  @ {exit_prem:.2f}  pos={pos}")
                            _record(close_side, exit_qty, exit_prem, "paper", o["trad_sym"], o["sec_id"], strategy_id, "paper", log)
                        del active_opts[sym]
                    else:
                        log.info(f"  [PAPER] 3:15 EXIT {sym}  pos={pos}")
                    positions[sym] = 0
                time.sleep(300)
                continue

            # ── Config log (ek line jo dikhata hai kya chal raha hai) ─
            log.info(
                f"[CONFIG] TF={tf}  RSI={period}  OB={overbought}  OS={oversold}"
                f"  Exit@={rsi_exit}  Qty={qty}L  MaxTrades={max_t}  Symbols={len(sym_list)}"
            )
            log.info(f"── Scanning {len(sym_list)} symbols ──")

            # ── Symbol-by-symbol scan ────────────────────────────────
            for sym in sym_list:
                t_count = trades_today.get(sym, 0)
                pos     = positions.get(sym, 0)

                # Candles fetch karo (aaj ka din)
                df = fetch_candles(sym, tf, token, cid)
                if df is None or df.empty:
                    log.warning(f"  {sym:12s} no data")
                    continue

                last_close          = float(df["close"].iloc[-2])
                signal, rsi_val     = compute_signal(df, period, oversold, overbought, rsi_exit, pos)

                log.info(
                    f"  {sym:14s} close={last_close:.1f}"
                    f"  RSI={str(rsi_val or '?'):>5}"
                    f"  sig={signal or 'NONE':4s}"
                    f"  pos={pos:+d}  trades={t_count}"
                )

                # ── EXIT ─────────────────────────────────────────────
                if signal == "EXIT":
                    if sym in active_opts:
                        o          = active_opts[sym]
                        close_side = "SELL" if o["side"] == "BUY" else "BUY"
                        exit_qty   = o.get("qty", qty)
                        exit_prem  = _opt_ltp(o["sec_id"], token, cid)
                        if not paper_mode:
                            place_order(sym, close_side, exit_qty, token, cid,
                                        o["sec_id"], "NSE_FNO", o["trad_sym"], log)
                            _record(close_side, exit_qty, exit_prem, "live", o["trad_sym"], o["sec_id"], strategy_id, "filled", log)
                        else:
                            log.info(f"  [PAPER] EXIT {o['trad_sym']}  qty={exit_qty}  @ {exit_prem:.2f}  RSI={rsi_val}")
                            _record(close_side, exit_qty, exit_prem, "paper", o["trad_sym"], o["sec_id"], strategy_id, "paper", log)
                        del active_opts[sym]
                    else:
                        log.info(f"  [PAPER] EXIT {sym}  RSI={rsi_val}")
                    positions[sym]    = 0
                    trades_today[sym] = t_count + 1
                    continue

                # Koi action nahi: already in position, ya max trades, ya no signal
                if pos != 0 or t_count >= max_t or signal is None:
                    continue

                # ── ENTRY ────────────────────────────────────────────
                opt_type = "CE" if signal == "BUY" else "PE"
                log.info(f"  ▶ {signal} on {sym} → BUY {opt_type} (close={last_close:.1f})")

                # ATM option contract nikalo (Dhan scrip master CSV se)
                # offset=0 → exact ATM, offset=1 → 1 strike OTM, etc.
                result = dhan_master.get_option_contract(sym, last_close, opt_type, offset)
                if not result or not result[0]:
                    log.error(f"  {sym} {opt_type} contract not found in master CSV")
                    continue

                sec_id, trad_sym, lot_size = result
                # actual_qty = lots × lot_size (lot_size Dhan CSV se, kabhi hardcode nahi)
                actual_qty = qty * (lot_size or 1)
                # entry par ACTUAL option premium fetch karo (underlying close NAHI) —
                # order_store/P&L isi se sahi banega
                entry_prem = _opt_ltp(sec_id, token, cid)

                if not paper_mode:
                    ok = place_order(sym, "BUY", actual_qty, token, cid,
                                     sec_id, "NSE_FNO", trad_sym, log)
                    if ok:
                        active_opts[sym]  = {"sec_id": sec_id, "trad_sym": trad_sym,
                                              "side": "BUY", "qty": actual_qty,
                                              "entry_premium": entry_prem}
                        positions[sym]    = 1 if signal == "BUY" else -1
                        trades_today[sym] = t_count + 1
                        _record("BUY", actual_qty, entry_prem, "live", trad_sym, sec_id, strategy_id, "filled", log)
                else:
                    log.info(
                        f"  [PAPER] BUY {opt_type} {trad_sym}"
                        f"  qty={actual_qty} ({qty}L × {lot_size})"
                        f"  @ {entry_prem:.2f} (premium)  underlying~{last_close:.1f}  RSI={rsi_val}"
                    )
                    active_opts[sym]  = {"sec_id": sec_id, "trad_sym": trad_sym,
                                          "side": "BUY", "qty": actual_qty,
                                          "entry_premium": entry_prem}
                    positions[sym]    = 1 if signal == "BUY" else -1
                    trades_today[sym] = t_count + 1
                    _record("BUY", actual_qty, entry_prem, "paper", trad_sym, sec_id, strategy_id, "paper", log)

        except KeyboardInterrupt:
            log.info("[RSI] Stopped by user (Ctrl+C)")
            break
        except Exception as e:
            log.error(f"[RSI] Loop error: {e}", exc_info=True)

        time.sleep(tf_secs)   # agla scan = 1 candle baad


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ENTRY POINT
#  Dashboard subprocess se chalata hai:
#    python _TRADERS/rsi_trader.py --paper --id rsi_v1
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", action="store_true", help="Paper mode (default)")
    ap.add_argument("--live",  action="store_true", help="LIVE mode — real orders!")
    ap.add_argument("--id",    default="rsi_v1",    help="Strategy ID from nifty_config.json")
    args = ap.parse_args()

    if args.live:
        print("\n⚠️  RSI LIVE MODE — REAL ORDERS PLACEED ON DHAN!")
        print("Ctrl+C within 5 seconds to cancel...\n")
        time.sleep(5)
        run(paper_mode=False, strategy_id=args.id)
    else:
        print(f"\n📝 RSI PAPER MODE — {args.id}\n")
        run(paper_mode=True, strategy_id=args.id)
