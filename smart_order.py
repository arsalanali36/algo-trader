"""
smart_order.py — marketable-limit execution with paper==live parity + shadow.

Core idea (best-in-class):
  - A MARKET order on an option slips to the bid/ask (155 shown -> 148 fill).
  - A marketable LIMIT crosses the spread: BUY at ask, SELL at bid. It fills
    INSTANTLY at a KNOWN price — no slippage surprise, no stuck-pending.

Paper == Live:
  - We ALWAYS log the "intended fill" (the price the strategy would get). This
    one line builds the P&L position and is IDENTICAL in paper and live, so the
    two modes never diverge.
  - In LIVE we ALSO fire the real broker order and log a separate [BROKER] line
    with the real status (filled/rejected/pending). The dashboard shows that as
    a small badge — so even if the live order is rejected (e.g. zero balance),
    the position still appears exactly like paper, with the reality annotated.

Price source priority: WebSocket Full feed bid/ask (real-time) -> broker REST
LTP fallback. NSE tick size 0.05 enforced.
"""

import time
import risk_gate

import dhan_feed

TICK = 0.05

_ltp_cache = {}   # sec_id -> (ltp, epoch_ts) — opportunistically warmed by every call


_REJECTED_STATUSES = {"REJECTED", "CANCELLED", "CANCELED", "EXPIRED"}
_TERMINAL_STATUSES = _REJECTED_STATUSES | {
    "TRADED", "FILLED", "COMPLETE",      # Dhan + Kite "actually filled"
}


def _is_rejected(status):
    return str(status or "").upper() in _REJECTED_STATUSES


def _is_terminal(status):
    return str(status or "").upper() in _TERMINAL_STATUSES


def _snap(p):
    """Snap to NSE tick size (0.05)."""
    return round(round(p / TICK) * TICK, 2)


def marketable_price(side, sec_id, seg, broker, buffer_bps=10):
    """Return (price, src). BUY=ask*(1+buf), SELL=bid*(1-buf); fallback LTP±buf.
    buffer_bps crosses the spread a touch so the limit fills immediately."""
    q = dhan_feed.get_quote(sec_id)
    bid, ask, ltp = q.get("bid"), q.get("ask"), q.get("ltp")
    buf = buffer_bps / 10000.0

    if side == "BUY":
        ref, src = (ask, "ask") if ask else (ltp, "feed_ltp")
    else:
        ref, src = (bid, "bid") if bid else (ltp, "feed_ltp")

    if not ref:  # feed empty (cold subscribe) -> REST LTP fallback.
        # Webhook entry fires spot+option quotes back-to-back; Dhan marketfeed is
        # ~1 req/sec, so a fresh option quote can 429. A 3x1.2s blocking retry
        # here used to push total webhook-handler latency past TradingView's own
        # webhook timeout ("request took too long and timed out") — one quick
        # retry + a short-TTL cache instead, so the request path stays fast.
        for _i, delay in enumerate((0, 0.5, 1.0)):
            if delay:
                time.sleep(delay)
            ref = (broker.quote(sec_id, seg) or {}).get("ltp")
            if ref:
                break
        src = "rest_ltp"
        if ref:
            _ltp_cache[sec_id] = (ref, time.time())
        else:
            cached = _ltp_cache.get(sec_id)
            if cached and (time.time() - cached[1]) <= 6:
                ref, src = cached[0], "rest_ltp_cached"
    if not ref:
        return None, "none"

    price = ref * (1 + buf) if side == "BUY" else ref * (1 - buf)
    return _snap(price), src


def execute(side, sym, sec_id, seg, qty, trad_sym, mode, broker,
            buffer_bps=10, log=print, tag="UNIV",
            source="", strategy="", instrument="", broker_name="", group_id="",
            extra_tags=None):
    """Execute one entry/exit.

    Returns: {ok, price, src, status, reason, order_id}
      status: 'paper' | 'filled' | 'pending' | 'rejected'
    Always logs an intended-fill line (P&L truth, same for paper & live).
    Live also fires the real order and logs a [BROKER] status line.
    source/strategy/instrument/broker_name are recorded into order_store (trade DB).
    extra_tags: caller-supplied tags (e.g. an RMS default per-instrument SL) to
    stamp onto a NEW position's record — pass None/[] for exit calls.
    """
    price, src = marketable_price(side, sec_id, seg, broker, buffer_bps)
    if price is None:
        log(f"[SKIP] {side} {trad_sym} — no price (feed+REST empty)")
        return {"ok": False, "reason": "no_price"}

    # 1) intended fill — builds P&L position; identical in paper & live
    mtag = "LIVE" if mode == "live" else "PAPER"
    log(f"[{mtag}] {side} {qty} {trad_sym} @ {price:.2f}  "
        f"correlationId={tag}_{sym}_{int(time.time())}")

    res = {"ok": True, "price": price, "src": src, "status": "paper",
           "reason": "paper", "order_id": None}

    # 2) live — fire the real order, record broker reality (shadow badge)
    if mode == "live":
        r = broker.place_order(side, sec_id, seg, qty, "LIMIT", price,
                               trad_sym=trad_sym, tag=f"{tag}_{sym}")
        res.update(status=r["status"], reason=r["reason"], order_id=r["order_id"])
        log(f"[BROKER] {trad_sym} {side} {qty} -> {r['status'].upper()} "
            f"({r['reason']})")

        # A broker that outright rejects the order (bad symbol, margin,
        # F&O permission) must NOT be treated as a successful entry — the
        # caller builds an in-memory "position" from res["ok"] alone, so an
        # unconditional ok=True here would let a strategy track a position
        # that was never actually opened at the broker.
        if _is_rejected(r["status"]):
            res["ok"] = False

        # A "pending"/"accepted" response is not a confirmed fill — some
        # rejects (price-band, freeze) only arrive a moment later, async,
        # after the initial 200. Re-query once if the broker supports it.
        if not _is_terminal(r["status"]) and r.get("order_id") and hasattr(broker, "order_status"):
            time.sleep(1.2)
            try:
                confirmed = broker.order_status(r["order_id"])
            except Exception:
                confirmed = None
            if confirmed:
                res["status"] = confirmed
                log(f"[BROKER-CONFIRM] {trad_sym} order {r['order_id']} -> {confirmed}")
                if _is_rejected(confirmed):
                    res["ok"] = False
                    res["reason"] = f"async reject confirmed: {confirmed}"
    elif mode == "paper":
        # Diagnostic-only shadow order — fires a REAL broker order at the same
        # paper price/qty purely to compare against Dhan's actual fill/reject
        # (slippage check). Never touches `res` — the paper fill price/P&L
        # recorded below stays exactly what it would have been without this.
        try:
            import risk_gate
            if risk_gate.shadow_live_enabled(strategy):
                sr = broker.place_order(side, sec_id, seg, qty, "LIMIT", price,
                                        trad_sym=trad_sym, tag=f"{tag}_{sym}_SHADOW")
                log(f"[BROKER-SHADOW] {trad_sym} {side} {qty} @ {price:.2f} -> "
                    f"{sr['status'].upper()} ({sr['reason']}) — paper fill unaffected")
        except Exception as e:
            log(f"[BROKER-SHADOW] failed (paper fill unaffected): {e}")

    # 3) persist to the trade DB (best-effort, never blocks the order)
    try:
        import order_store
        bname = broker_name or (broker.name() if hasattr(broker, "name") else risk_gate.default_broker())
        order_store.record(side, qty, price, source=source, strategy=strategy,
                           mode=mode, broker=bname, symbol=sym, instrument=instrument,
                           trad_sym=trad_sym, sec_id=sec_id, segment=seg,
                           correlation_id=f"{tag}_{sym}",
                           broker_order_id=res.get("order_id") or "",
                           status=res.get("status", "paper"), group_id=group_id,
                           tags=list(extra_tags) if extra_tags else None)
    except Exception:
        pass

    return res


def place_hedge_if_configured(symbol, spot_price, sell_option_type, sell_offset, qty,
                               mode, broker, group_id, strategy,
                               buffer_bps=10, log=print, tag="HEDGE",
                               source="", instrument="", broker_name="",
                               min_strikes_override=None):
    """Resolve (via strategy_safety.compute_hedge_target — the ONE place hedge
    sizing logic lives, see LESSONS.md TRAP #15) and place the auto-hedge BUY
    leg for a SELL that already went through, tagged with the same `group_id`
    so the dashboard can show/close them together.

    Hedge config (min strikes floor + optional max-premium ₹ floor) comes
    from risk_gate.hedge_config(strategy) — the RMS Risk tab. `min_strikes_
    override`, if given, overrides just the floor (a caller's own legacy
    config field) — same as compute_hedge_target's param of the same name.

    Returns the execute() result dict, or None if hedge is off / contract
    resolution failed (best-effort — never raises into the caller, and never
    blocks the SELL leg that already happened before this is called).
    """
    try:
        import strategy_safety

        def _quote(sec_id):
            try:
                q = broker.quote(sec_id, "NSE_FNO") or {}
                return q.get("ltp")
            except Exception:
                return None

        h_sec_id, h_trad_sym, _lot = strategy_safety.compute_hedge_target(
            strategy, symbol, spot_price, sell_option_type, sell_offset,
            quote_fn=_quote, min_strikes_override=min_strikes_override, log=log)
        if not h_sec_id:
            return None
        return execute("BUY", symbol, h_sec_id, "NSE_FNO", qty, h_trad_sym, mode, broker,
                       buffer_bps=buffer_bps, log=log, tag=tag,
                       source=source, strategy=strategy, instrument=instrument,
                       broker_name=broker_name, group_id=group_id)
    except Exception as e:
        log(f"[HEDGE] failed (sell leg unaffected): {e}")
        return None
