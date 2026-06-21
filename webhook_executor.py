#!/usr/bin/env python3
"""
webhook_executor.py — TradingView webhook → Dhan order executor.

Idea: TradingView Pine sirf SIGNAL bhejta hai (ENTRY/EXIT + direction). Saara
execution dimaag yahan Python me hai — strike select (ATM / ATM±N), option
buy/sell, qty, paper/live, aur (Phase 2) trailing SL + target + 3:15 squareoff.
Strategy ek hi jagah (Pine) rehti hai → zero drift.

Reuse:
  - dhan_master.get_option_contract()  → ATM±offset sec_id + lot_size
  - dhan_master.get_equity_info()      → index sec_id (NIFTY=13, BANKNIFTY=25)
  - smart_order.execute()              → marketable-limit, paper==live parity
  - brokers/dhan_broker.DhanBroker     → REST quote + live order
  - dhan_feed                          → live bid/ask (subscribe option sec_id)

Log lines are written in the SAME format trader_dashboard.parse_pnl() expects,
so webhook trades show up in the P&L tab automatically:
  entry : "<ts>,000  INFO      [PAPER] BUY 65 NIFTY-Jun2026-24100-CE @ 150.25  ..."
  exit  : opposite-side line (smart_order) — parse_pnl nets it into a closed trade.

Phase 1: handle_signal() (ENTRY/EXIT, paper), safety (max/day, no-entry-after).
Phase 2 (next): monitor_tick() trailing SL / target / 3:15 force squareoff.
"""

import json
import socket
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

# IPv4 force — Dhan rejects IPv6 on the VPS (DH-905). Must be before any Dhan call.
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR   = Path(__file__).resolve().parent
TC_FILE    = BASE_DIR / "nifty_config.json"
LOG_DIR    = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
WH_LOG     = LOG_DIR / "webhook_v1.log"
CFG_KEY    = "webhook_v1"

# Config defaults — overlaid by nifty_config.json["webhook_v1"] on every read (hot-reload).
_DEFAULTS = {
    "active":          True,
    "mode":            "paper",       # "paper" | "live"
    "instrument":      "options",
    "strike_offset":   0,             # ATM=0, ATM±N
    "qty":             1,             # lots (× lot_size from scrip master)
    "trail_mode":      "premium",     # "premium" | "index"  (used in Phase 2)
    "trail_value":     20,            # premium: points to trail by; index: atr_mult
    "target_points":   0,             # 0 = no fixed target (trail only)
    "sl_points":       30,            # initial stop distance (premium points)
    "max_trades_per_day": 2,
    "no_entry_after":  "15:15",       # IST — no new entry at/after this
    "squareoff_at":    "15:15",       # IST — force-exit all (Phase 2)
    "secret_token":    "",
    "long_opt_type":   "CE",          # LONG signal → this option type
    "short_opt_type":  "PE",          # SHORT signal → this option type
    "opt_action":      "BUY",         # "BUY" (long option) | "SELL" (option selling)
}

# ── shared state ────────────────────────────────────────────────────────────────
_lock = threading.Lock()
# _wh_state[symbol] = {position, direction, opt_sec_id, opt_trad_sym, opt_qty,
#                      opt_action, entry_premium, sl, target, entry_time}
_wh_state = {}
_trades_today = {}            # symbol -> int (reset daily)
_last_reset_day = None
_seen = {}                    # dedup: alert id -> epoch ts
_events = deque(maxlen=80)    # recent (ts, text) for the UI live log
_broker_cache = None


def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)


def _cfg():
    """webhook_v1 config, defaults overlaid by nifty_config.json (hot-reload)."""
    cfg = dict(_DEFAULTS)
    try:
        allc = json.loads(TC_FILE.read_text())
        if isinstance(allc.get(CFG_KEY), dict):
            cfg.update(allc[CFG_KEY])
    except Exception:
        pass
    return cfg


def _log(msg):
    """Append to webhook_v1.log in parse_pnl-compatible format + keep for UI."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{now},000  INFO      {msg}"
    try:
        with open(WH_LOG, "a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except Exception:
        pass
    _events.append((now, msg))
    print("[WEBHOOK]", msg, flush=True)


def _broker():
    global _broker_cache
    if _broker_cache is None:
        from brokers.dhan_broker import DhanBroker
        _broker_cache = DhanBroker()   # loads creds from data/config.json
    return _broker_cache


def _hm(s):
    """'15:15' -> (15, 15); bad input -> (15, 15)."""
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return (15, 15)


def _index_spot(symbol):
    """Live index/equity spot price via broker REST quote."""
    import dhan_master
    info = dhan_master.get_equity_info(symbol)
    if not info:
        # NIFTY/BANKNIFTY fallbacks if not in cache
        info = {"NIFTY": ("13", "IDX_I", "INDEX"),
                "BANKNIFTY": ("25", "IDX_I", "INDEX")}.get(symbol)
    if not info:
        return None
    sec_id, seg, _inst = info
    q = _broker().quote(sec_id, seg) or {}
    return q.get("ltp")


def _current_premium(sec_id):
    """Live option premium — WebSocket feed first, REST quote fallback."""
    try:
        import dhan_feed
        q = dhan_feed.get_quote(sec_id) or {}
        if q.get("ltp"):
            return float(q["ltp"])
    except Exception:
        pass
    q = _broker().quote(str(sec_id), "NSE_FNO") or {}
    return float(q["ltp"]) if q.get("ltp") else None


def _index_atr(symbol, period=14, mult=2.0):
    """Best-effort index ATR-based trail distance (index points). Falls back to
    None if candles unavailable (e.g. token expired) — caller uses a default."""
    try:
        import dhan_master
        info = dhan_master.get_equity_info(symbol)
        if not info:
            return None
        sec_id, seg, inst = info
        df = _broker().intraday_candles(sec_id, seg, inst, days=2, interval=5)
        if df is None or len(df) < period + 1:
            return None
        import pandas as pd
        h, l, c = df["high"], df["low"], df["close"]
        pc = c.shift(1)
        tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1]  # Wilder RMA
        return float(atr) * mult if atr and atr == atr else None
    except Exception:
        return None


def _maybe_reset_day():
    global _last_reset_day
    today = ist_now().date()
    if _last_reset_day != today:
        _last_reset_day = today
        _trades_today.clear()
        _log(f"new trading day {today} — trade counters reset")


def _dedup(alert_id):
    """True if this alert id was seen recently (TradingView can double-fire)."""
    if not alert_id:
        return False
    now = time.time()
    # prune old ids (>120s)
    for k in [k for k, v in _seen.items() if now - v > 120]:
        _seen.pop(k, None)
    if alert_id in _seen:
        return True
    _seen[alert_id] = now
    return False


# ── public API ──────────────────────────────────────────────────────────────────

def handle_signal(payload: dict) -> dict:
    """Process one TradingView alert. Returns {ok, msg}.

    payload: {
      "id":     unique alert id (dedup),
      "symbol": "NIFTY" | "BANKNIFTY" | ...,
      "signal": "ENTRY" | "EXIT",
      "action": "buy" | "sell"   (direction: buy=LONG, sell=SHORT; ENTRY only),
    }
    """
    with _lock:
        _maybe_reset_day()
        cfg = _cfg()

        if not cfg.get("active", True):
            return {"ok": False, "msg": "webhook strategy inactive"}

        alert_id = str(payload.get("id") or "")
        if _dedup(alert_id):
            _log(f"DEDUP skip id={alert_id}")
            return {"ok": True, "msg": "duplicate ignored"}

        symbol = str(payload.get("symbol") or "NIFTY").upper().strip()
        signal = str(payload.get("signal") or "ENTRY").upper().strip()
        action = str(payload.get("action") or "buy").lower().strip()

        if signal == "EXIT":
            return _do_exit(symbol, cfg, reason="TV_EXIT")
        if signal == "ENTRY":
            return _do_entry(symbol, action, cfg)
        return {"ok": False, "msg": f"unknown signal '{signal}'"}


def _do_entry(symbol, action, cfg):
    import dhan_master
    import smart_order

    # ── safety net (server-side) ──
    now = ist_now()
    if (now.hour, now.minute) >= _hm(cfg.get("no_entry_after", "15:15")):
        _log(f"ENTRY blocked {symbol} — after {cfg.get('no_entry_after')}")
        return {"ok": False, "msg": "no entry after cutoff"}

    if _trades_today.get(symbol, 0) >= int(cfg.get("max_trades_per_day", 2)):
        _log(f"ENTRY blocked {symbol} — max trades/day reached")
        return {"ok": False, "msg": "max trades/day reached"}

    if symbol in _wh_state and _wh_state[symbol].get("position"):
        _log(f"ENTRY blocked {symbol} — position already open")
        return {"ok": False, "msg": "position already open"}

    direction = "LONG" if action in ("buy", "long") else "SHORT"
    opt_type  = cfg.get("long_opt_type", "CE") if direction == "LONG" else cfg.get("short_opt_type", "PE")
    opt_action = (cfg.get("opt_action", "BUY") or "BUY").upper()

    spot = _index_spot(symbol)
    if not spot:
        _log(f"ENTRY fail {symbol} — no spot price")
        return {"ok": False, "msg": "no spot price"}

    offset = int(cfg.get("strike_offset", 0))
    sec_id, trad_sym, lot_size = dhan_master.get_option_contract(symbol, spot, opt_type, offset)
    if not sec_id:
        _log(f"ENTRY fail {symbol} — contract not found {opt_type} off={offset}")
        return {"ok": False, "msg": "contract not found"}

    lot_size = lot_size or 65
    qty = int(cfg.get("qty", 1)) * lot_size
    mode = cfg.get("mode", "paper")

    # subscribe option to live feed for future ticks (Phase 2 trailing)
    try:
        import dhan_feed
        dhan_feed.add(("NSE_FNO", str(sec_id)))
    except Exception:
        pass

    res = smart_order.execute(opt_action, symbol, sec_id, "NSE_FNO", qty,
                              trad_sym, mode, _broker(), log=_log, tag="TVWH")
    if not res.get("ok"):
        return {"ok": False, "msg": f"execute failed: {res.get('reason')}"}

    entry_px = res["price"]
    sl_pts   = float(cfg.get("sl_points", 0) or 0)
    tgt_pts  = float(cfg.get("target_points", 0) or 0)
    if opt_action == "BUY":
        sl     = entry_px - sl_pts if sl_pts else None
        target = entry_px + tgt_pts if tgt_pts else None
    else:  # SELL (option selling): loss when premium rises
        sl     = entry_px + sl_pts if sl_pts else None
        target = entry_px - tgt_pts if tgt_pts else None

    st = {
        "position": direction, "direction": direction,
        "opt_sec_id": str(sec_id), "opt_trad_sym": trad_sym, "opt_qty": qty,
        "opt_action": opt_action, "entry_premium": entry_px,
        "sl": sl, "target": target, "entry_spot": spot,
        "idx_sl": None, "idx_trail_dist": None,
        "entry_time": now.strftime("%H:%M"),
    }
    # index-mode trailing: trail the underlying by ATR×mult (fallback 30 pts)
    if cfg.get("trail_mode") == "index":
        dist = _index_atr(symbol, mult=float(cfg.get("trail_value", 2) or 2)) or 30.0
        st["idx_trail_dist"] = dist
        st["idx_sl"] = spot - dist if direction == "LONG" else spot + dist

    _wh_state[symbol] = st
    _trades_today[symbol] = _trades_today.get(symbol, 0) + 1
    _log(f"ENTRY {direction} {symbol} {opt_action} {qty} {trad_sym} @ {entry_px:.2f} "
         f"(spot={spot:.2f} off={offset} SL={sl} TGT={target} mode={cfg.get('trail_mode')})")
    return {"ok": True, "msg": f"{opt_action} {trad_sym} @ {entry_px:.2f}", "trade": _wh_state[symbol]}


def _do_exit(symbol, cfg, reason="TV_EXIT"):
    import smart_order
    st = _wh_state.get(symbol)
    if not st or not st.get("position"):
        _log(f"EXIT skip {symbol} — no open position")
        return {"ok": True, "msg": "no open position"}

    close_side = "SELL" if st["opt_action"] == "BUY" else "BUY"
    mode = cfg.get("mode", "paper")
    res = smart_order.execute(close_side, symbol, st["opt_sec_id"], "NSE_FNO",
                              st["opt_qty"], st["opt_trad_sym"], mode, _broker(),
                              log=_log, tag="TVWH")
    if not res.get("ok"):
        # leave position open so a retry/monitor can close it
        _log(f"EXIT fail {symbol} — {res.get('reason')}")
        return {"ok": False, "msg": f"exit failed: {res.get('reason')}"}

    exit_px = res["price"]
    # human-readable reason (parse_pnl ignores this line; the smart_order line nets P&L)
    _log(f"EXIT_INFO {st['opt_trad_sym']} reason={reason} @ {exit_px:.2f}")
    _wh_state[symbol]["position"] = None
    return {"ok": True, "msg": f"closed {st['opt_trad_sym']} @ {exit_px:.2f} ({reason})"}


def monitor_tick():
    """Called every few seconds by the dashboard daemon. Trails SL, hits
    target/SL, and force-squares-off open webhook positions at 3:15 PM.

    trail_mode 'premium' → trail on the option premium (default).
    trail_mode 'index'   → trail a stop on the underlying; exit option on breach.
    """
    with _lock:
        _maybe_reset_day()
        cfg = _cfg()
        now = ist_now()
        force_sq = (now.hour, now.minute) >= _hm(cfg.get("squareoff_at", "15:15"))
        tv = float(cfg.get("trail_value", 0) or 0)
        mode = cfg.get("trail_mode", "premium")

        for symbol in list(_wh_state.keys()):
            st = _wh_state[symbol]
            if not st.get("position"):
                continue

            if force_sq:
                _do_exit(symbol, cfg, reason="SQUAREOFF_315")
                continue

            if mode == "index":
                spot = _index_spot(symbol)
                if spot is None:
                    continue
                dist = st.get("idx_trail_dist") or 30.0
                if st["direction"] == "LONG":
                    new = spot - dist
                    if st.get("idx_sl") is None or new > st["idx_sl"]:
                        st["idx_sl"] = new
                    if spot <= st["idx_sl"]:
                        _do_exit(symbol, cfg, reason="IDX_TRAIL")
                else:  # SHORT (bought PE / sold CE) — exit if underlying rises
                    new = spot + dist
                    if st.get("idx_sl") is None or new < st["idx_sl"]:
                        st["idx_sl"] = new
                    if spot >= st["idx_sl"]:
                        _do_exit(symbol, cfg, reason="IDX_TRAIL")
                continue

            # premium mode — trail on the option premium itself
            prem = _current_premium(st["opt_sec_id"])
            if prem is None:
                continue
            if st["opt_action"] == "BUY":      # long option: SL below, ratchet up
                if tv:
                    new = prem - tv
                    if st.get("sl") is None or new > st["sl"]:
                        st["sl"] = new
                if st.get("sl") is not None and prem <= st["sl"]:
                    _do_exit(symbol, cfg, reason="TRAIL_SL")
                elif st.get("target") and prem >= st["target"]:
                    _do_exit(symbol, cfg, reason="TARGET")
            else:                              # short option: SL above, ratchet down
                if tv:
                    new = prem + tv
                    if st.get("sl") is None or new < st["sl"]:
                        st["sl"] = new
                if st.get("sl") is not None and prem >= st["sl"]:
                    _do_exit(symbol, cfg, reason="TRAIL_SL")
                elif st.get("target") and prem <= st["target"]:
                    _do_exit(symbol, cfg, reason="TARGET")


def status():
    """Snapshot for the UI: open positions + recent events + counters."""
    with _lock:
        opens = {s: v for s, v in _wh_state.items() if v.get("position")}
        return {
            "active": _cfg().get("active", True),
            "mode": _cfg().get("mode", "paper"),
            "positions": opens,
            "trades_today": dict(_trades_today),
            "events": [{"t": t, "msg": m} for t, m in list(_events)[-40:]],
        }
