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

import dhan_feed

TICK = 0.05

_ltp_cache = {}   # sec_id -> (ltp, epoch_ts) — opportunistically warmed by every call


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
        for _i, delay in enumerate((0, 0.3)):
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
            source="", strategy="", instrument="", broker_name="", group_id=""):
    """Execute one entry/exit.

    Returns: {ok, price, src, status, reason, order_id}
      status: 'paper' | 'filled' | 'pending' | 'rejected'
    Always logs an intended-fill line (P&L truth, same for paper & live).
    Live also fires the real order and logs a [BROKER] status line.
    source/strategy/instrument/broker_name are recorded into order_store (trade DB).
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
        bname = broker_name or (broker.name() if hasattr(broker, "name") else "dhan")
        order_store.record(side, qty, price, source=source, strategy=strategy,
                           mode=mode, broker=bname, symbol=sym, instrument=instrument,
                           trad_sym=trad_sym, sec_id=sec_id, segment=seg,
                           correlation_id=f"{tag}_{sym}",
                           broker_order_id=res.get("order_id") or "",
                           status=res.get("status", "paper"), group_id=group_id)
    except Exception:
        pass

    return res


def place_hedge_if_configured(symbol, spot_price, sell_option_type, sell_offset, qty,
                               mode, broker, group_id, hedge_offset_strikes,
                               buffer_bps=10, log=print, tag="HEDGE",
                               source="", strategy="", instrument="", broker_name=""):
    """If `hedge_offset_strikes` is configured (truthy), auto-place a hedge BUY
    leg further OTM than the sold leg, same option_type, tagged with the same
    `group_id` as the SELL leg so the dashboard can show/close them together.

    hedge_offset_strikes is OFF by default (None/0/absent) — opt-in per
    strategy/webhook. `sell_offset` + `hedge_offset_strikes` is the strike
    INDEX further OTM (dhan_master.get_option_contract's `offset` param is
    already an index into the sorted-strikes list, not a points value — same
    unit the rest of the codebase already uses for strike_offset).

    Returns the execute() result dict, or None if not configured / contract
    resolution failed (best-effort — never raises into the caller, and never
    blocks the SELL leg that already happened before this is called).
    """
    if not hedge_offset_strikes:
        return None
    try:
        hedge_offset_strikes = int(hedge_offset_strikes)
    except Exception:
        return None
    if hedge_offset_strikes == 0:
        return None

    try:
        import dhan_master
        hedge_offset = sell_offset + hedge_offset_strikes
        h_sec_id, h_trad_sym, _lot = dhan_master.get_option_contract(
            symbol, spot_price, sell_option_type, hedge_offset)
        if not h_sec_id:
            log(f"[HEDGE] contract resolve failed for {symbol} {sell_option_type} offset={hedge_offset} — hedge leg skipped")
            return None
        return execute("BUY", symbol, h_sec_id, "NSE_FNO", qty, h_trad_sym, mode, broker,
                       buffer_bps=buffer_bps, log=log, tag=tag,
                       source=source, strategy=strategy, instrument=instrument,
                       broker_name=broker_name, group_id=group_id)
    except Exception as e:
        log(f"[HEDGE] failed (sell leg unaffected): {e}")
        return None
