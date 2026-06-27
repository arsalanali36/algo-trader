#!/usr/bin/env python3
"""
webhook_executor.py — TradingView webhook → Dhan/Kite order executor (MULTI-STRATEGY).

Idea: TradingView Pine sirf SIGNAL bhejta hai (ENTRY/EXIT + direction). Saara
execution dimaag yahan Python me hai — strike select (ATM / ATM±N), option
buy/sell, qty, paper/live, trailing SL + target + 3:15 squareoff. Strategy ek hi
jagah (Pine) rehti hai → zero drift.

MULTI-STRATEGY ROUTING (2026-06-22):
  Har alert apni pehchaan bhejta hai → JSON me "strategy" field. Position state
  ab (strategy, symbol) se keyed hai, isliye ek hi instrument pe alag-alag
  timeframe/strategy ke alerts takraate nahi:

      {"id":..,"strategy":"range5m","symbol":"NIFTY","signal":"ENTRY","action":"buy"}

  - range5m|NIFTY, ema1m|NIFTY, range5m|BANKNIFTY → sab isolated positions.
  - Har strategy ka apna config block: nifty_config.json["webhooks"]["<strat>"]
    (qty / strike / SL / instrument / paper-live / broker).
  - Limits: per-(strategy,symbol) max_trades_per_day + global daily ₹ loss cap
    (sab strategies milake) → cap hit → naye ENTRY block + monitor squareoff-all.
  - "strategy" missing → "default" (backward-compat with the old single webhook).

Config (nifty_config.json):
  "webhooks": {
    "global":  {"secret_token","daily_amount_cap","global_max_trades"},
    "<strat>": {active,broker,mode,instrument,strike_offset,qty,opt_action,
                long_opt_type,short_opt_type,sl_points,target_points,
                trail_mode,trail_value,max_trades_per_day,no_entry_after,squareoff_at}
  }
  Agar "webhooks" map nahi mila → legacy flat "webhook_v1" se derive (default + global secret).

Reuse:
  - dhan_master.get_option_contract() → ATM±offset sec_id + lot_size
  - dhan_master.get_equity_info()     → index sec_id (NIFTY=13, BANKNIFTY=25)
  - smart_order.execute()             → marketable-limit, paper==live parity
  - brokers.get_broker(name)          → dhan / kite order routing
  - dhan_feed                         → live bid/ask (data ALWAYS Dhan)

Log lines parse_pnl-compatible (P&L tab me webhook trades auto dikhte hain).
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
WH_LOG     = LOG_DIR / "webhook_v1.log"   # shared webhook log (parse_pnl reads it)

# Per-strategy config defaults — overlaid by nifty_config.json["webhooks"][strat].
_DEFAULTS = {
    "active":          True,
    "broker":          "dhan",        # "dhan" | "kite"
    "mode":            "paper",       # "paper" | "live"
    "instrument":      "options",     # "options" | "equity"
    "strike_offset":   0,             # ATM=0, ATM±N (options only)
    "qty":             1,             # options: lots (× lot_size); equity: shares
    "trail_mode":      "premium",     # "premium" | "index"
    "trail_value":     20,            # premium: points to trail; index: atr_mult
    "target_points":   0,             # 0 = no fixed target (trail only)
    "sl_points":       30,            # initial stop distance (premium points)
    "max_trades_per_day": 2,          # per (strategy, symbol)
    "no_entry_after":  "15:15",       # IST — no new entry at/after this
    "squareoff_at":    "15:15",       # IST — force-exit all
    # Default = option SELLING: LONG→sell PE, SHORT→sell CE. Toggle flips to BUY.
    "long_opt_type":   "PE",
    "short_opt_type":  "CE",
    "opt_action":      "SELL",        # "SELL" (option selling) | "BUY" (long option)
}

# Global block defaults (one per webhook engine, not per strategy).
_GLOBAL_DEFAULTS = {
    "secret_token":     "",
    "daily_amount_cap": 0,    # ₹ overall loss cap across all webhook strategies (0=off)
    "global_max_trades": 0,   # total entries/day across all strategies (0=off)
}

# Execution params the Pine alert JSON may override per-signal.
_OVERRIDABLE = ("strike_offset", "qty", "sl_points", "target_points",
                "trail_value", "trail_mode", "opt_action",
                "long_opt_type", "short_opt_type", "instrument")

# ── shared state ────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_wh_state = {}            # "strat|symbol" -> position dict
_trades_today = {}        # "strat|symbol" -> int (reset daily)
_day_realized = 0.0       # ₹ realized P&L today across all webhook strategies
_last_reset_day = None
_seen = {}                # dedup: alert id -> epoch ts
_events = deque(maxlen=80)
_broker_cache = {}        # broker_name -> broker object


def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)


def _key(strat, symbol):
    return f"{strat}|{symbol}"


# ── config ────────────────────────────────────────────────────────────────────

def _raw_cfg():
    try:
        return json.loads(TC_FILE.read_text())
    except Exception:
        return {}


def _all_webhooks():
    """Return {'global': {...}, '<strat>': {merged cfg}, ...} (hot-reload).
    Backward-compat: no 'webhooks' map → derive from legacy flat 'webhook_v1'."""
    allc = _raw_cfg()
    wh = allc.get("webhooks")
    if not isinstance(wh, dict):
        legacy = allc.get("webhook_v1") if isinstance(allc.get("webhook_v1"), dict) else {}
        legacy = legacy or {}
        glob = dict(_GLOBAL_DEFAULTS)
        if legacy.get("secret_token"):
            glob["secret_token"] = legacy["secret_token"]
        default = {k: v for k, v in legacy.items() if k != "secret_token"}
        wh = {"global": glob, "default": default}

    out = {}
    glob = dict(_GLOBAL_DEFAULTS)
    glob.update(wh.get("global") or {})
    out["global"] = glob
    for strat, c in wh.items():
        if strat == "global":
            continue
        merged = dict(_DEFAULTS)
        if isinstance(c, dict):
            merged.update(c)
        out[strat] = merged
    return out


def _strat_cfg(strat):
    """Merged config for one strategy id; falls back to 'default' then bare defaults."""
    whs = _all_webhooks()
    if strat in whs and strat != "global":
        return whs[strat]
    if "default" in whs:
        return whs["default"]
    return dict(_DEFAULTS)


def _global_cfg():
    return _all_webhooks()["global"]


def _register_strategy(strat):
    """Persist a new webhook strategy block (INACTIVE) so an unknown strategy name
    arriving from a TradingView alert auto-appears in the dashboard to configure.
    Inactive by default → no surprise orders until the user reviews + activates."""
    allc = _raw_cfg()
    whs = allc.get("webhooks")
    if not isinstance(whs, dict):
        # legacy config had no map yet — seed it from the in-memory view
        cur = _all_webhooks()
        whs = {"global": cur["global"]}
        for s, c in cur.items():
            if s != "global":
                whs[s] = c
    if strat not in whs:
        block = dict(_DEFAULTS)
        block["active"] = False        # pending — user activates after review
        whs[strat] = block
        allc["webhooks"] = whs
        try:
            TC_FILE.write_text(json.dumps(allc, indent=2))
        except Exception:
            pass
        _log(f"NEW strategy '{strat}' registered (inactive) — configure & activate in dashboard")


def webhook_secret():
    """Shared secret token for /api/webhook/tv auth (from global block)."""
    return _global_cfg().get("secret_token", "")


def _log(msg):
    """Append to webhook log in parse_pnl-compatible format + keep for UI."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{now},000  INFO      {msg}"
    try:
        with open(WH_LOG, "a", encoding="utf-8") as lf:
            lf.write(line + "\n")
    except Exception:
        pass
    _events.append((now, msg))
    print("[WEBHOOK]", msg, flush=True)


def _broker(name="dhan"):
    """Cached broker object by name (dhan/kite). DATA always comes from Dhan."""
    name = (name or "dhan").lower()
    if name not in _broker_cache:
        from brokers import get_broker
        _broker_cache[name] = get_broker(name)
    return _broker_cache[name]


def _hm(s):
    """'15:15' -> (15, 15); bad input -> (15, 15)."""
    try:
        h, m = str(s).split(":")
        return int(h), int(m)
    except Exception:
        return (15, 15)


_spot_cache = {}     # symbol -> (ltp, epoch_ts) — kept warm by _spot_refresher_loop
_spot_started = False
_spot_lock = threading.Lock()


def _spot_info(symbol):
    import dhan_master
    info = dhan_master.get_equity_info(symbol)
    if not info:
        info = {"NIFTY": ("13", "IDX_I", "INDEX"),
                "BANKNIFTY": ("25", "IDX_I", "INDEX")}.get(symbol)
    return info


def _spot_refresher_loop():
    """Background thread — refreshes _spot_cache every ~3s for symbols actually
    used by configured webhook strategies, ONE Dhan call at a time (no burst).
    Keeps the webhook request path (handle_signal) fully non-blocking: it just
    reads this cache instead of making a live network call + retries, which
    used to add 1.5-4.5s of latency inside the HTTP response and trip
    TradingView's own webhook timeout ('request took too long and timed out')."""
    while True:
        try:
            # webhook strategy configs don't carry a symbol field (it comes per-alert),
            # so just keep both indices warm — cheap, one call each, ~1.1s apart.
            for sym in ("NIFTY", "BANKNIFTY"):
                info = _spot_info(sym)
                if not info:
                    continue
                sec_id, seg, _inst = info
                try:
                    q = _broker("dhan").quote(sec_id, seg) or {}
                    ltp = q.get("ltp")
                    if ltp:
                        _spot_cache[sym] = (ltp, time.time())
                except Exception:
                    pass
                time.sleep(1.1)   # stay well under Dhan's ~1 req/sec LTP limit
        except Exception:
            pass
        time.sleep(2)


def _ensure_spot_refresher():
    global _spot_started
    with _spot_lock:
        if not _spot_started:
            _spot_started = True
            threading.Thread(target=_spot_refresher_loop, daemon=True).start()


def _index_spot(symbol):
    """Live index/equity spot price — reads the background-refreshed cache
    (non-blocking, see _spot_refresher_loop). Falls back to a single direct
    Dhan call only if the cache is missing/stale, so a cold start still works."""
    _ensure_spot_refresher()
    cached = _spot_cache.get(symbol)
    if cached and (time.time() - cached[1]) <= 8:
        return cached[0]

    info = _spot_info(symbol)
    if not info:
        return None
    sec_id, seg, _inst = info
    q = _broker("dhan").quote(sec_id, seg) or {}
    ltp = q.get("ltp")
    if ltp:
        _spot_cache[symbol] = (ltp, time.time())
        return ltp

    # last resort: a slightly-stale cached value beats failing the entry outright
    if cached:
        _log(f"_index_spot {symbol} — Dhan quote failed, using {time.time()-cached[1]:.1f}s-old cached price")
        return cached[0]
    return None


def _current_premium(sec_id):
    """Live option premium — WebSocket feed first, Dhan REST quote fallback."""
    try:
        import dhan_feed
        q = dhan_feed.get_quote(sec_id) or {}
        if q.get("ltp"):
            return float(q["ltp"])
    except Exception:
        pass
    q = _broker("dhan").quote(str(sec_id), "NSE_FNO") or {}
    return float(q["ltp"]) if q.get("ltp") else None


def _index_atr(symbol, period=14, mult=2.0):
    """Best-effort index ATR-based trail distance (index points)."""
    try:
        import dhan_master
        info = dhan_master.get_equity_info(symbol)
        if not info:
            return None
        sec_id, seg, inst = info
        df = _broker("dhan").intraday_candles(sec_id, seg, inst, days=2, interval=5)
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
    global _last_reset_day, _day_realized
    today = ist_now().date()
    if _last_reset_day != today:
        _last_reset_day = today
        _trades_today.clear()
        _day_realized = 0.0
        _log(f"new trading day {today} — trade counters + day P&L reset")


def _dedup(alert_id):
    """True if this alert id was seen recently (TradingView can double-fire)."""
    if not alert_id:
        return False
    now = time.time()
    for k in [k for k, v in _seen.items() if now - v > 120]:
        _seen.pop(k, None)
    if alert_id in _seen:
        return True
    _seen[alert_id] = now
    return False


def _leg_pnl(st, exit_px):
    """Realized ₹ P&L for a closed leg, given the exit premium."""
    q = st.get("opt_qty", 0)
    ep = st.get("entry_premium", 0) or 0
    if st.get("opt_action") == "BUY":
        return (exit_px - ep) * q
    return (ep - exit_px) * q       # SELL: profit when premium falls


def _day_pnl():
    """(realized, unrealized, total) ₹ across all open webhook positions today.
    Unrealized hits the live premium feed — call sparingly (entry/monitor only)."""
    unreal = 0.0
    for st in _wh_state.values():
        if not st.get("position"):
            continue
        prem = _current_premium(st["opt_sec_id"])
        if prem is None:
            continue
        unreal += _leg_pnl(st, prem)
    return _day_realized, unreal, _day_realized + unreal


# ── public API ──────────────────────────────────────────────────────────────────

def handle_signal(payload: dict) -> dict:
    """Process one TradingView alert. Returns {ok, msg}.

    payload: {
      "id":       unique alert id (dedup),
      "strategy": webhook config id (routing key; missing → "default"),
      "symbol":   "NIFTY" | "BANKNIFTY" | "SBIN" | ...,
      "signal":   "ENTRY" | "EXIT",
      "action":   "buy" | "sell"   (buy=LONG, sell=SHORT; ENTRY only),
    }
    """
    with _lock:
        _maybe_reset_day()

        strat = str(payload.get("strategy") or "default").strip() or "default"
        whs = _all_webhooks()
        if strat not in whs:
            # unknown strategy name from Pine → auto-register (inactive) so it shows
            # up in the dashboard for the user to configure. No trade this time.
            _register_strategy(strat)
            return {"ok": False,
                    "msg": f"strategy '{strat}' registered — configure & activate it in the dashboard"}
        cfg = whs[strat]

        if not cfg.get("active", True):
            return {"ok": False, "msg": f"webhook strategy '{strat}' inactive"}

        alert_id = str(payload.get("id") or "")
        if _dedup(f"{strat}:{alert_id}"):
            _log(f"DEDUP skip strat={strat} id={alert_id}")
            return {"ok": True, "msg": "duplicate ignored"}

        symbol = str(payload.get("symbol") or "NIFTY").upper().strip()
        signal = str(payload.get("signal") or "ENTRY").upper().strip()
        action = str(payload.get("action") or "buy").lower().strip()

        if signal == "EXIT":
            return _do_exit(strat, symbol, cfg, reason="TV_EXIT")
        if signal == "ENTRY":
            return _do_entry(strat, symbol, action, cfg, payload)
        return {"ok": False, "msg": f"unknown signal '{signal}'"}


def _merge_overrides(cfg, payload):
    """Alert payload values win over dashboard config for execution params."""
    eff = dict(cfg)
    for k in _OVERRIDABLE:
        if k in payload and payload[k] not in (None, ""):
            eff[k] = payload[k]
    return eff


def _do_entry(strat, symbol, action, cfg, payload=None):
    import dhan_master
    import smart_order

    cfg = _merge_overrides(cfg, payload or {})
    key = _key(strat, symbol)
    now = ist_now()

    # naye signal ki direction — reversal detect karne ke liye pehle chahiye
    direction = "LONG" if action in ("buy", "long") else "SHORT"

    # ── safety net (server-side) — entry cutoff ──
    if (now.hour, now.minute) >= _hm(cfg.get("no_entry_after", "15:15")):
        _log(f"ENTRY blocked {key} — after {cfg.get('no_entry_after')}")
        return {"ok": False, "msg": "no entry after cutoff"}

    # ── REVERSAL vs duplicate (TV ka strategy.entry auto-reverse mirror karo) ──
    # TV reverse karta hai par alert sirf naya ENTRY bhejta. Pehle hum "position
    # already open" pe block kar dete the → Python purani pakde rehta, TV nayi pe.
    # Ab: opposite direction = reversal (purani exit, nayi enter); same = ignore.
    existing = _wh_state.get(key)
    reversing = False
    if existing and existing.get("position"):
        if existing.get("direction") == direction:
            _log(f"ENTRY skip {key} — already {direction} (pyramiding off)")
            return {"ok": True, "msg": f"already {direction}, ignored"}
        reversing = True   # opposite → checks pass hone ke baad reverse karenge

    if _trades_today.get(key, 0) >= int(cfg.get("max_trades_per_day", 2)):
        # max hit → naya entry nahi. Reversal ho to bhi purani ko flat nahi karte
        # (half-state se bachne ko) — purani as-is, monitor 3:15 pe squareoff karega.
        _log(f"ENTRY blocked {key} — max trades/day reached")
        return {"ok": False, "msg": "max trades/day reached"}

    # ── global limits (across all strategies) ──
    glob = _global_cfg()
    gmax = int(glob.get("global_max_trades", 0) or 0)
    if gmax > 0 and sum(_trades_today.values()) >= gmax:
        _log(f"ENTRY blocked {key} — global max trades/day ({gmax}) reached")
        return {"ok": False, "msg": "global max trades/day reached"}
    # ── SUPREME RMS daily-loss breaker (unified) — same cap that pos_monitor
    # uses to square off; blocks new entries for this strategy once breached.
    # Replaces the webhook's old standalone daily_amount_cap so there's ONE cap.
    # realized-only here (unrealized is per-leg and would cross-contaminate
    # strategies if summed globally); any still-open losing leg gets squared off
    # by pos_monitor's same-cap check, which realizes it and feeds back here.
    try:
        import risk_gate
        breached, why = risk_gate.daily_loss_breached(strat, unrealized=0.0)
        if breached:
            _log(f"ENTRY blocked {key} — {why}")
            return {"ok": False, "msg": "RMS daily loss cap hit"}
    except Exception as _e:
        _log(f"RMS daily-loss gate check failed (allowing entry): {_e}")

    # ── reversal: ab jab naya entry allowed hai, pehle purani leg close karo ──
    # (atomic-ish: purani exit fail hui to nayi entry NAHI lete — half-state se bacho)
    if reversing:
        _log(f"REVERSAL {key} — closing {existing.get('direction')} before opening {direction}")
        ex = _do_exit(strat, symbol, cfg, reason="REVERSAL")
        if not ex.get("ok"):
            _log(f"REVERSAL abort {key} — purani exit fail, nayi entry nahi li")
            return {"ok": False, "msg": f"reversal exit failed: {ex.get('msg')}"}

    opt_type   = cfg.get("long_opt_type", "CE") if direction == "LONG" else cfg.get("short_opt_type", "PE")
    opt_action = (cfg.get("opt_action", "BUY") or "BUY").upper()

    spot = _index_spot(symbol)
    if not spot:
        _log(f"ENTRY fail {key} — no spot price")
        return {"ok": False, "msg": "no spot price"}

    offset = int(cfg.get("strike_offset", 0))
    sec_id, trad_sym, lot_size = dhan_master.get_option_contract(symbol, spot, opt_type, offset)
    if not sec_id:
        _log(f"ENTRY fail {key} — contract not found {opt_type} off={offset}")
        return {"ok": False, "msg": "contract not found"}

    lot_size = lot_size or 65
    lots = int(cfg.get("qty", 1))
    qty = lots * lot_size
    mode = cfg.get("mode", "paper")
    broker = _broker(cfg.get("broker", "dhan"))

    try:
        import dhan_feed
        dhan_feed.add(("NSE_FNO", str(sec_id)))
    except Exception:
        pass

    # ── risk gate (RMS Stage 1+2+3) — drawdown breaker > concentration cap >
    # capital allocation (size-down if configured) > real broker funds (live
    # only). Blocked/sized-down legs are logged tagged in Orders & P&L instead
    # of the signal silently vanishing. ──
    def _record_blocked(price_, qty_, tag_reason):
        try:
            import order_store
            order_store.record(opt_action, qty_, price_, source="webhook", strategy=strat,
                               mode=mode, broker=cfg.get("broker", "dhan"), symbol=symbol,
                               instrument=cfg.get("instrument", "options"), trad_sym=trad_sym,
                               sec_id=sec_id, segment="NSE_FNO", status="blocked",
                               tags=["CAPITAL_BLOCKED", tag_reason])
        except Exception:
            pass

    try:
        import risk_gate
        dd_ok, dd_reason = risk_gate.check_drawdown()
        if not dd_ok:
            # No price available here yet, so don't record a PX 0.00 ghost row
            # (that was the confusing entry the user flagged). The block is still
            # logged, and the RMS panel surfaces the gating status separately.
            _log(f"ENTRY blocked {key} — {dd_reason}")
            return {"ok": False, "msg": dd_reason}

        est_price, _src = smart_order.marketable_price(opt_action, sec_id, "NSE_FNO", broker)
        if est_price:
            conc_ok, conc_reason = risk_gate.check_concentration(symbol, qty, est_price, side=opt_action)
            if not conc_ok:
                _log(f"ENTRY blocked {key} — {conc_reason}")
                _record_blocked(est_price, qty, conc_reason)
                return {"ok": False, "msg": conc_reason}

            cap_ok, cap_reason = risk_gate.check_capital(strat, qty, est_price, side=opt_action, sec_id=sec_id)
            if not cap_ok:
                if risk_gate.capital_mode(strat) == "size_down":
                    fit_lots = risk_gate.sized_lots(strat, lots, lot_size, est_price, side=opt_action, sec_id=sec_id)
                else:
                    fit_lots = 0
                if fit_lots > 0:
                    _log(f"ENTRY sized down {key} — {lots}L -> {fit_lots}L ({cap_reason})")
                    lots, qty = fit_lots, fit_lots * lot_size
                else:
                    _log(f"ENTRY blocked {key} — {cap_reason}")
                    _record_blocked(est_price, qty, cap_reason)
                    return {"ok": False, "msg": cap_reason}

            if mode == "live":
                fund_ok, fund_reason = risk_gate.check_broker_funds(broker, qty * est_price)
                if not fund_ok:
                    _log(f"ENTRY blocked {key} — {fund_reason}")
                    _record_blocked(est_price, qty, fund_reason)
                    return {"ok": False, "msg": fund_reason}
    except Exception as e:
        _log(f"risk gate check failed (allowing entry): {e}")

    instrument = cfg.get("instrument", "options")
    # group_id links this leg to its auto-hedge BUY (if hedge_offset_strikes is
    # configured below) — empty string if no hedge, so existing single-leg
    # behavior/netting is completely unaffected when the feature is off.
    hedge_offset_strikes = cfg.get("hedge_offset_strikes")
    group_id = f"{strat}_{symbol}_{int(time.time())}" if (opt_action == "SELL" and hedge_offset_strikes) else ""
    try:
        default_sl_tags = risk_gate.default_instrument_sl_tags(strat, symbol)
    except Exception:
        default_sl_tags = []
    res = smart_order.execute(opt_action, symbol, sec_id, "NSE_FNO", qty,
                              trad_sym, mode, broker, log=_log, tag="TVWH",
                              source="webhook", strategy=strat, instrument=instrument,
                              broker_name=cfg.get("broker", "dhan"), group_id=group_id,
                              extra_tags=default_sl_tags)
    if not res.get("ok"):
        return {"ok": False, "msg": f"execute failed: {res.get('reason')}"}

    if group_id:
        smart_order.place_hedge_if_configured(
            symbol, spot, opt_type, offset, qty, mode, broker, group_id, hedge_offset_strikes,
            log=_log, tag="TVWH_HEDGE", source="webhook", strategy=strat,
            instrument=instrument, broker_name=cfg.get("broker", "dhan"))

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
        "strategy": strat, "symbol": symbol,
        "position": direction, "direction": direction,
        "opt_sec_id": str(sec_id), "opt_trad_sym": trad_sym, "opt_qty": qty,
        "opt_action": opt_action, "entry_premium": entry_px,
        "sl": sl, "target": target, "entry_spot": spot,
        "idx_sl": None, "idx_trail_dist": None,
        "entry_time": now.strftime("%H:%M"), "mode": mode,
        "broker": cfg.get("broker", "dhan"), "instrument": instrument,
    }
    if cfg.get("trail_mode") == "index":
        dist = _index_atr(symbol, mult=float(cfg.get("trail_value", 2) or 2)) or 30.0
        st["idx_trail_dist"] = dist
        st["idx_sl"] = spot - dist if direction == "LONG" else spot + dist

    _wh_state[key] = st
    _trades_today[key] = _trades_today.get(key, 0) + 1
    _log(f"ENTRY {strat} {direction} {symbol} {opt_action} {qty} {trad_sym} @ {entry_px:.2f} "
         f"(spot={spot:.2f} off={offset} SL={sl} TGT={target} mode={cfg.get('trail_mode')})")
    return {"ok": True, "msg": f"{opt_action} {trad_sym} @ {entry_px:.2f}", "trade": st}


def release_position(sec_id=None, trad_sym=None, reason="external_squareoff"):
    """Mark a webhook position closed because something OUTSIDE this module
    already squared it off (e.g. the dashboard's global pos_monitor_loop 1%
    max-loss / SL / EOD net). Without this, the webhook's own monitor_tick or a
    later TradingView EXIT would fire a SECOND closing order against an already-
    closed leg, leaving an orphan position (the CE-BUY-not-netted bug). Matches
    by option sec_id (preferred) or trad_sym. Returns True if it cleared one.
    Caller (pos_monitor) records the actual closing order itself; here we only
    clear in-memory state so we never double-close."""
    with _lock:
        for key, st in _wh_state.items():
            if not st or not st.get("position"):
                continue
            if (sec_id and str(st.get("opt_sec_id")) == str(sec_id)) or \
               (trad_sym and st.get("opt_trad_sym") == trad_sym):
                st["position"] = None
                _log(f"RELEASE {key} {st.get('opt_trad_sym')} — closed externally ({reason}), "
                     f"webhook monitor backing off (no double-close)")
                return True
    return False


def _do_exit(strat, symbol, cfg, reason="TV_EXIT"):
    global _day_realized
    import smart_order
    key = _key(strat, symbol)
    st = _wh_state.get(key)
    if not st or not st.get("position"):
        _log(f"EXIT skip {key} — no open position")
        return {"ok": True, "msg": "no open position"}

    close_side = "SELL" if st["opt_action"] == "BUY" else "BUY"
    mode = cfg.get("mode", "paper")
    broker = _broker(cfg.get("broker", st.get("broker", "dhan")))
    res = smart_order.execute(close_side, symbol, st["opt_sec_id"], "NSE_FNO",
                              st["opt_qty"], st["opt_trad_sym"], mode, broker,
                              log=_log, tag="TVWH",
                              source="webhook", strategy=strat,
                              instrument=st.get("instrument", "options"),
                              broker_name=cfg.get("broker", st.get("broker", "dhan")))
    if not res.get("ok"):
        _log(f"EXIT fail {key} — {res.get('reason')}")
        return {"ok": False, "msg": f"exit failed: {res.get('reason')}"}

    exit_px = res["price"]
    pnl = _leg_pnl(st, exit_px)
    _day_realized += pnl
    _log(f"EXIT_INFO {strat} {st['opt_trad_sym']} reason={reason} @ {exit_px:.2f} pnl={pnl:.0f}")
    _wh_state[key]["position"] = None
    return {"ok": True, "msg": f"closed {st['opt_trad_sym']} @ {exit_px:.2f} ({reason})"}


def monitor_tick():
    """Called every few seconds by the dashboard daemon. Trails SL, hits
    target/SL, force-squares-off at 3:15 PM, and enforces the global ₹ loss cap.
    Each (strategy, symbol) position uses its own strategy config."""
    with _lock:
        _maybe_reset_day()
        now = ist_now()

        # global daily loss cap → squareoff everything
        glob = _global_cfg()
        cap = float(glob.get("daily_amount_cap", 0) or 0)
        cap_hit = False
        if cap > 0 and any(v.get("position") for v in _wh_state.values()):
            _, _, total = _day_pnl()
            cap_hit = total <= -cap

        for key in list(_wh_state.keys()):
            st = _wh_state[key]
            if not st.get("position"):
                continue
            strat, symbol = key.split("|", 1)
            cfg = _strat_cfg(strat)

            if cap_hit:
                _do_exit(strat, symbol, cfg, reason="GLOBAL_CAP")
                continue

            force_sq = (now.hour, now.minute) >= _hm(cfg.get("squareoff_at", "15:15"))
            if force_sq:
                _do_exit(strat, symbol, cfg, reason="SQUAREOFF_315")
                continue

            tv = float(cfg.get("trail_value", 0) or 0)
            mode = cfg.get("trail_mode", "premium")

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
                        _do_exit(strat, symbol, cfg, reason="IDX_TRAIL")
                else:
                    new = spot + dist
                    if st.get("idx_sl") is None or new < st["idx_sl"]:
                        st["idx_sl"] = new
                    if spot >= st["idx_sl"]:
                        _do_exit(strat, symbol, cfg, reason="IDX_TRAIL")
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
                    _do_exit(strat, symbol, cfg, reason="TRAIL_SL")
                elif st.get("target") and prem >= st["target"]:
                    _do_exit(strat, symbol, cfg, reason="TARGET")
            else:                              # short option: SL above, ratchet down
                if tv:
                    new = prem + tv
                    if st.get("sl") is None or new < st["sl"]:
                        st["sl"] = new
                if st.get("sl") is not None and prem >= st["sl"]:
                    _do_exit(strat, symbol, cfg, reason="TRAIL_SL")
                elif st.get("target") and prem <= st["target"]:
                    _do_exit(strat, symbol, cfg, reason="TARGET")


def status():
    """Snapshot for the UI: per-strategy meta + open positions + counters + day P&L."""
    with _lock:
        whs = _all_webhooks()
        opens = {k: v for k, v in _wh_state.items() if v.get("position")}
        strategies = {
            s: {"active": c.get("active"), "mode": c.get("mode"),
                "instrument": c.get("instrument"), "broker": c.get("broker"),
                "qty": c.get("qty")}
            for s, c in whs.items() if s != "global"
        }
        return {
            "strategies": strategies,
            "global": {
                "daily_amount_cap": whs["global"].get("daily_amount_cap"),
                "global_max_trades": whs["global"].get("global_max_trades"),
                "day_realized": round(_day_realized, 2),
            },
            "positions": opens,
            "trades_today": dict(_trades_today),
            "events": [{"t": t, "msg": m} for t, m in list(_events)[-40:]],
        }
