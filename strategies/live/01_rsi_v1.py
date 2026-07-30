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
BASE_DIR    = Path(__file__).resolve().parent.parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"      # Dhan JWT token
TC_FILE     = BASE_DIR / "nifty_config.json"          # strategy params

# dhan_master root pe hai — import ke liye path add karo
sys.path.insert(0, str(BASE_DIR))
import _paths  # sys.path bootstrap — adds _core/_data/_ops so flat imports below resolve after refactor
import dhan_master
# (kite_broker import legacy place_order() ke saath gaya — orders ab
#  execution_gateway → smart_order → brokers/ abstraction se jaate hain)
# RSI ka SINGLE source of truth — _CHARTING/indicators.py (Rule 6B / ADR-002).
# Wahi Wilder formula jo pehle yahin inline thi (com=period-1, Pine ta.rsi match) —
# ab signal AUR dashboard chart dono isi se aate hain, mismatch possible hi nahi.
from _CHARTING.indicators import wilder_rsi as _compute_rsi

# Market timing (IST)
MARKET_OPEN  = (9,  16)   # 9:16 AM — market chalu
MARKET_CLOSE = (15, 25)   # 3:25 PM — hard stop
FORCE_EXIT   = (15, 10)   # 3:10 PM — sab positions band (CAS; RMS import-fail fallback)

# Dhan API endpoints
ORDERS_URL   = "https://api.dhan.co/v2/orders"
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"

# Timeframe string → minutes
TF_MAP = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30}

# Index underlyings — CASH-settled, no physical delivery. Stock F&O (everything
# NOT in this set) is PHYSICALLY settled, so Zerodha blocks fresh stock-option
# MIS BUYs in the near-month's expiry week. The next-month-expiry switch below
# applies to STOCKS ONLY; index options must never be rolled.
INDEX_UNDERLYINGS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
    "SENSEX", "BANKEX", "SENSEX50",
}

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

def _exit_times():
    """(squareoff_hm, no_entry_hm) — RMS single-source (risk_gate.exit_time_config,
    RMS tab se editable). Fallback FORCE_EXIT."""
    try:
        import risk_gate as _rg
        return _rg.exit_time_config()
    except Exception:
        return FORCE_EXIT, FORCE_EXIT

def is_force_exit_time():
    t = (ist_now().hour, ist_now().minute)
    return t >= _exit_times()[0]

def is_no_entry_time():
    """No new entry at/after RMS no_entry_after — stops '3:15 ke baad entry ->
    turant squareoff -> ₹50 zabardasti tax' bug."""
    t = (ist_now().hour, ist_now().minute)
    return t >= _exit_times()[1]

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
        cached = shared_candle_cache.get(sec_id, interval, 0, max_age=20.0)
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
            if not _rl.acquire("candle"):
                # gate saturated / 429 cooldown — posting anyway just extends
                # the account-wide cooldown. Caller treats None as fetch-fail.
                return None
        except ImportError:
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
            shared_candle_cache.put(sec_id, interval, 0, cache_df.to_dict("records"))
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

# _compute_rsi = wilder_rsi (imported at top from _CHARTING/indicators.py —
# Task 2 consolidation 2026-07-07; formula bit-for-bit wahi hai jo yahan inline thi)

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


# (Legacy raw place_order() — Dhan REST / kite_broker.place_order seedha —
#  2026-07-07 ko DELETE hua: koi caller nahi bacha tha (entry/exit dono
#  execution_gateway → smart_order.execute se jaate hain). Rule 6B: raw broker
#  order-calls sirf smart_order.py/brokers/ mein. Task 3, ADR-001.)


def close_position(sym, active_opts, token, cid, paper_mode, rsi_val, log, cfg=None, strategy_id="rsi_v1"):
    """Open option position band karo (EXIT order).

    Ab execution_gateway.execute_exit() se — EK call (Task 3, Rule 6B/ADR-001).
    Andar: fresh pre-exit flat-check (manual-close ke baad phantom OPPOSITE
    position guard — ab strategy-aware bhi, Task 5: doosri strategy ka same-
    contract lot MERI flat-check ko confuse nahi karta) + smart_order.execute
    is_exit=True (order_store recording, extra chase rounds, ₹0-guard TRAP #1).
    Exit-reason tag: rsi_val "3:15" (force-exit call site) → EOD_315_SQUAREOFF,
    warna RSI_MIDLINE_EXIT — Completed Trades ka Exit Reason column isi se."""
    if sym not in active_opts:
        log.info(f"    [PAPER] EXIT {sym}  RSI={rsi_val}")
        return
    o    = active_opts.pop(sym)
    mode = "paper" if paper_mode else "live"
    try:
        import execution_gateway as _gw
        _reason = "EOD_315_SQUAREOFF" if rsi_val == "3:15" else "RSI_MIDLINE_EXIT"
        _gw.execute_exit(strategy_id, sym, o["sec_id"], o["trad_sym"], o["qty"],
                         entry_side=o["side"], seg="NSE_FNO", mode=mode,
                         broker_name=_order_broker(cfg or {}), tag="RSI",
                         instrument="options", reason=_reason, log=log.info)
    except Exception as _ee:
        log.error(f"    [EXIT] {sym} exit order failed: {_ee} "
                  f"(pos_monitor SL/EOD will retry via order_store)")


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 5b — RESTART RECOVERY (2026-07-03)
#  Rebuild positions/active_opts/trades_today from today's open order_store
#  rows on process startup. Without this, ANY restart mid-day (dashboard
#  restart, VPS reboot, crash-relaunch) wipes this file's OWN in-memory
#  belief about what's open, even though a position may still be genuinely
#  open at the broker/order_store. The next matching RSI signal then fires
#  a brand-new duplicate BUY on a symbol that's already open — order_store's
#  netting then treats the orphaned original leg as a "pyramid/dup" and
#  FIFO-pairs it with some unrelated leg, producing the exact same-price
#  ₹0 phantom round-trip rows seen live (NTPC/HDFCBANK, 2026-07-02).
#  Same pattern as TRAP #28/#76 (range_trader.py/universe_trader.py) — this
#  file never had it because it's a separate, older script that turned out
#  to be the one the dashboard actually runs for both "rsi" and "rsi_v1".
# ═══════════════════════════════════════════════════════════════════════════

def _recover_rsi_state(strategy_id, positions, active_opts, trades_today, log):
    """Mutates the 3 dicts in place (they're locals in run(), passed by
    reference). Best-effort — any failure just leaves fresh-empty state."""
    try:
        import order_store
        ist = ist_now()
        data = order_store.trades_for(ist.strftime("%Y-%m-%d"))
        all_today = (data.get("open") or []) + (data.get("details") or [])
        recovered = 0
        for p in (data.get("open") or []):
            if "CAPITAL_BLOCKED" in (p.get("tags") or []):
                continue   # never actually placed at the broker — not a real position (found live 2026-07-03)
            if p.get("strategy") != strategy_id or p.get("entry") != "BUY":
                continue   # RSI always enters BUY (buys the premium, CE or PE)
            sym = p.get("symbol")
            trad_sym = p.get("sym", "")
            if not sym or not trad_sym:
                continue
            if trad_sym.endswith("-CE"):
                pos_val = 1
            elif trad_sym.endswith("-PE"):
                pos_val = -1
            else:
                continue
            positions[sym]   = pos_val
            active_opts[sym] = {"sec_id": p.get("sec_id"), "trad_sym": trad_sym,
                                 "side": "BUY", "qty": p.get("qty"),
                                 "entry_premium": p.get("entry_price")}
            trades_today[sym] = sum(1 for q in all_today
                                     if q.get("strategy") == strategy_id and q.get("symbol") == sym
                                     and q.get("entry") == "BUY")
            recovered += 1
        if recovered:
            log.info(f"[RECOVER] re-attached {recovered} open position(s) from order_store — "
                     f"RSI exit logic + trade counters will resume for them this session")
    except Exception as e:
        log.warning(f"[RECOVER] state recovery failed (continuing with fresh state): {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  SECTION 6 — MAIN LOOP
#  Har candle ke baad saare symbols scan karo, signals pe act karo
# ═══════════════════════════════════════════════════════════════════════════

def run(paper_mode=True, strategy_id="rsi_v1"):
    log = _make_logger(strategy_id)
    from singleton_guard import acquire_singleton
    if not acquire_singleton(strategy_id):
        log.warning(f"[SINGLETON] another {strategy_id} process already live — exiting (duplicate-order guard)")
        return
    log.info("=" * 62)
    log.info(f"  01_rsi_v1.py  |  {strategy_id}  |  {'PAPER' if paper_mode else '⚡ LIVE'}")
    log.info("=" * 62)

    # In-memory state — naye din pe reset hota hai
    positions    = {}   # sym → +1 (CE open) / -1 (PE open) / 0 (flat)
    active_opts  = {}   # sym → {sec_id, trad_sym, side, qty}
    trades_today = {}   # sym → int (aaj kitni baar trade hua)
    phys_skip    = set()  # sym → physical-settlement rejection aaya, aaj skip (anti-spam)

    _recover_rsi_state(strategy_id, positions, active_opts, trades_today, log)
    # Seed last_date to TODAY (not None) — recovery must never be immediately
    # undone by the loop's own "new day" check below (TRAP #76's exact
    # failure shape: last_date=None makes iteration 1 always look like a new
    # day, wiping what recovery just populated one line later).
    last_date = ist_now().date()

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
                phys_skip    = set()
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

            # Physical-settlement handling (STOCK options only — see
            # INDEX_UNDERLYINGS): inside the near-month's expiry window Zerodha
            # rejects fresh stock-option MIS buys, so roll to next-month expiry.
            roll_enabled = bool(tc.get("next_month_on_physical_settlement", True))
            switch_days  = int(tc.get("next_month_switch_days", 4))

            token, cid = load_creds()
            tf_secs    = TF_MAP.get(tf, 5) * 60     # sleep = 1 candle duration

            # Re-validate in-memory state against order_store every cycle (not
            # just at startup) — catches a position closed externally during
            # the day (manual close, trailing-lock squareoff, pos_monitor
            # EOD) that this file's own memory wouldn't otherwise learn about
            # until its own RSI-exit signal fires, which can be a long time.
            open_legs_in_store = None
            try:
                import order_store
                open_legs_in_store = order_store.trades_for(now.strftime("%Y-%m-%d")).get("open")
            except Exception as _se:
                log.warning(f"[SYNC] order_store query failed: {_se}")

            # ── 3:15 PM force-exit ───────────────────────────────────────────
            if is_force_exit_time():
                for sym, pos in list(positions.items()):
                    if pos == 0:
                        continue
                    log.info(f"[FORCE EXIT 3:15] {sym}")
                    close_position(sym, active_opts, token, cid, paper_mode, "3:15", log, tc, strategy_id)
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

                if pos != 0 and open_legs_in_store is not None:
                    matching_open = False
                    opt_info = active_opts.get(sym)
                    for p in open_legs_in_store:
                        if p.get("strategy") == strategy_id and p.get("symbol") == sym:
                            if opt_info and opt_info.get("sec_id"):
                                if str(p.get("sec_id")) == str(opt_info["sec_id"]):
                                    matching_open = True
                                    break
                            else:
                                matching_open = True
                                break
                    if not matching_open:
                        log.info(f"[SYNC] Position for {sym} closed externally in order_store "
                                 f"(broker flat/manual exit/trailing lock). Clearing state.")
                        positions[sym] = 0
                        active_opts.pop(sym, None)
                        pos = 0

                df = fetch_candles(sym, tf, token, cid)
                if df is None or df.empty or len(df) < 2:
                    # len<2 hits right at market open (first scan of the day) —
                    # df["close"].iloc[-2] below needs 2+ rows. Without this
                    # guard one thin symbol's IndexError used to abort the
                    # WHOLE cycle (no per-symbol try/except in this loop),
                    # skipping every other symbol's scan that cycle too.
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
                    close_position(sym, active_opts, token, cid, paper_mode, rsi_val, log, tc, strategy_id)
                    positions[sym]    = 0
                    trades_today[sym] = t_count + 1
                    continue

                # Already in position / max trades hit / no signal
                if pos != 0 or t_count >= max_t or signal is None:
                    continue

                # No-entry-after gate (RMS single-source) — no NEW entry at/after
                # the configured time; else 3:15 squareoff instantly closes it =
                # wasted entry+exit brokerage ("zabardasti tax"). Exits above are
                # never gated.
                if is_no_entry_time():
                    _ne = _exit_times()[1]
                    log.info(f"  ENTRY BLOCKED {sym} — no entry after {_ne[0]:02d}:{_ne[1]:02d} (RMS)")
                    continue

                # ── ENTRY ───────────────────────────────────────────────────
                # Anti-spam: agar aaj is symbol pe physical-settlement rejection
                # aa chuka hai, dobara mat aazmao (100-reject spam se bachao).
                if sym in phys_skip:
                    continue

                opt_type = "CE" if signal == "BUY" else "PE"
                log.info(f"  ★ {signal} on {sym} → BUY {opt_type}")

                # Option contract: sec_id + trad_sym + lot_size from Dhan CSV.
                # Physical-settlement guard: STOCK options (index nahi) ke liye,
                # jab near-month expiry <= switch_days trading-din door ho, to
                # NEXT-month contract lo — Zerodha stock-option MIS BUY reject
                # karta hai us window me ("Try next month's expiry"). Index
                # options cash-settled = kabhi switch nahi.
                use_next_month = False
                _dte = None
                if roll_enabled and sym not in INDEX_UNDERLYINGS:
                    _dte = dhan_master.trading_days_to_near_monthly_expiry(sym)
                    if _dte is not None and _dte <= switch_days:
                        use_next_month = True

                if use_next_month:
                    result = dhan_master.get_next_monthly_option_contract(sym, last_close, opt_type, offset)
                    if result and result[0]:
                        log.info(f"  ⤳ {sym} physical-settlement window (~{_dte}d to expiry) — NEXT-month {opt_type}")
                    else:
                        log.info(f"  {sym} next-month {opt_type} not listed — falling back to nearest")
                        result = dhan_master.get_option_contract(sym, last_close, opt_type, offset)
                else:
                    result = dhan_master.get_option_contract(sym, last_close, opt_type, offset)

                if not result or not result[0]:
                    log.error(f"  {sym} {opt_type} — contract not found in master CSV")
                    continue

                sec_id, trad_sym, lot_size = result
                actual_qty = lots * (lot_size or 1)   # lot_size CSV se, kabhi hardcode nahi

                # ── Entry ab execution_gateway.execute_signal() se — EK call (Task 3,
                # Rule 6B / ADR-001). Andar wahi sequence jo pehle yahan inline tha:
                # marketable_price (no-premium → skip, TRAP #1) → strategy_safety.
                # gate_entry (poora RMS, fail-closed) → RMS default SL tags →
                # smart_order.execute (marketable-limit + order_store recording).
                mode = "paper" if paper_mode else "live"
                try:
                    import execution_gateway as _gw
                    res = _gw.execute_signal(strategy_id, sym, "BUY", lots, (lot_size or 1),
                                             sec_id, trad_sym, seg="NSE_FNO", mode=mode,
                                             broker_name=_order_broker(tc), tag="RSI",
                                             instrument="options", log=log.info)
                    if res["ok"]:
                        active_opts[sym]  = {"sec_id": sec_id, "trad_sym": trad_sym,
                                              "side": "BUY", "qty": res["qty"]}
                        positions[sym]    = +1 if signal == "BUY" else -1
                        trades_today[sym] = t_count + 1
                    else:
                        _reason = res.get('reason', 'order not ok')
                        log.info(f"  [ENTRY SKIP] {sym} — {_reason}")
                        # Physical-settlement rejection (window estimate off, ya
                        # next-month bhi block) → us symbol ko aaj skip = no spam.
                        _rl = _reason.lower()
                        if "physical delivery" in _rl or "next month" in _rl:
                            phys_skip.add(sym)
                            log.info(f"  ⛔ {sym} physical-settlement reject — skipping rest of today")
                except Exception as _ent:
                    log.error(f"  [ENTRY ERR] {sym}: {_ent}")
                    continue

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
