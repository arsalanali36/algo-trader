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
            extra_tags=None, product=None):
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

    res = {"ok": True, "price": price, "src": src, "status": "paper",
           "reason": "paper", "order_id": None}
    provisional_id = None   # order_store row written the instant the broker
                             # accepts, before the fill-confirm poll — see below

    # ── LIVE mode: fire order first, wait for actual fill, THEN record P&L ──
    if mode == "live":
        r = broker.place_order(side, sec_id, seg, qty, "LIMIT", price,
                               trad_sym=trad_sym, tag=f"{tag}_{sym}",
                               product=product)
        res.update(status=r["status"], reason=r["reason"], order_id=r["order_id"])
        log(f"[BROKER] {trad_sym} {side} {qty} @ {price:.2f} -> {r['status'].upper()} "
            f"({r['reason']}) orderId={r.get('order_id')}")

        if _is_rejected(r["status"]):
            log(f"[LIVE-SKIP] {trad_sym} — broker rejected, P&L not recorded")
            res["ok"] = False
            return res

        # ── TRAP #58/#62 root fix: write a PROVISIONAL row right now, before
        # polling for fill confirmation. The old design only recorded AFTER
        # confirming TRADED within ~8s — but stock options (RELIANCE, SUNPHARMA,
        # MARUTI, HINDUNILVR — all wider-spread than NIFTY) routinely take
        # longer than that to confirm, even though the broker fill genuinely
        # happens. When the poll timed out, execute() returned early and
        # order_store.record() at the bottom never ran — the position existed
        # for real at the broker but nowhere in this app (2026-07-01, same day,
        # 4 separate real occurrences). Writing this row FIRST means a timeout
        # no longer means "invisible" — worst case it sits tagged
        # UNCONFIRMED_FILL until broker_sync's regular sync reconciles it,
        # instead of not existing at all. ──
        try:
            import order_store
            bname = broker_name or (broker.name() if hasattr(broker, "name") else risk_gate.default_broker())
            provisional_id = order_store.record(
                side, qty, price, source=source, strategy=strategy, mode=mode,
                broker=bname, symbol=sym, instrument=instrument, trad_sym=trad_sym,
                sec_id=sec_id, segment=seg, correlation_id=f"{tag}_{sym}",
                broker_order_id=r.get("order_id") or "", status="filled",
                group_id=group_id,
                tags=(list(extra_tags) if extra_tags else []) + ["UNCONFIRMED_FILL"])
        except Exception as _pe:
            log(f"[order_store] provisional record failed (fill-confirm gap NOT closed this call): {_pe}")

        # Poll for actual fill — max ~8s (5 attempts × 1.5s).
        # Marketable limits on liquid options typically fill in <1s.
        fill_st, fill_price = None, None
        oid = r.get("order_id")
        if oid and hasattr(broker, "get_fill"):
            for attempt in range(5):
                time.sleep(1.5)
                try:
                    fill_st, fill_price = broker.get_fill(oid)
                except Exception:
                    fill_st = None
                log(f"[FILL-POLL] {trad_sym} attempt {attempt+1}/5 -> {fill_st}")
                if fill_st in ("TRADED", "REJECTED"):
                    break

        if fill_st == "REJECTED":
            log(f"[LIVE-SKIP] {trad_sym} — fill confirmed REJECTED, P&L not recorded")
            res["ok"] = False
            res["status"] = "rejected"
            res["reason"] = "fill rejected at broker"
            if provisional_id:
                try:
                    import order_store
                    order_store.update_fill(provisional_id, status="rejected")
                except Exception:
                    pass
            return res

        if fill_st != "TRADED":
            # Timeout — order may still have filled at the broker; we just
            # can't confirm it from here. The provisional row (already
            # written above) stays exactly as-is: pos_monitor_loop protects
            # it like any other open position from this point on, and if it
            # turns out to have never actually filled, broker_sync's regular
            # ghost-sync will find it flat with no matching fill and cleanly
            # exclude it (TRAP #61's no-exit-price branch) — no dangling leg
            # either way.
            log(f"[LIVE-PENDING] {trad_sym} orderId={oid} — fill not confirmed in 8s; "
                f"provisional order_store row (id={provisional_id}) protects it either way. "
                f"Monitor Open Positions or check broker manually.")
            res["ok"] = False
            res["status"] = "pending"
            res["reason"] = "fill not confirmed within timeout"
            return res

        # Use actual fill price if broker returned one (more accurate P&L)
        if fill_price and fill_price > 0:
            actual_price = fill_price
            log(f"[FILL-ACTUAL] {trad_sym} slippage {price:.2f} → {actual_price:.2f} "
                f"({actual_price - price:+.2f})")
        else:
            actual_price = price  # broker returned TRADED but no average_price yet

        res["price"] = actual_price
        res["status"] = "filled"
        # NOW log the intended fill — this is what builds the P&L position
        log(f"[LIVE] {side} {qty} {trad_sym} @ {actual_price:.2f}  "
            f"correlationId={tag}_{sym}_{int(time.time())}")

        if provisional_id:
            try:
                import order_store
                order_store.update_fill(
                    provisional_id, price=actual_price,
                    tags=list(extra_tags) if extra_tags else [])
            except Exception as _ue:
                log(f"[order_store] provisional update failed: {_ue}")

    # ── PAPER mode: log immediately (no real broker, P&L is simulation) ──
    elif mode == "paper":
        log(f"[PAPER] {side} {qty} {trad_sym} @ {price:.2f}  "
            f"correlationId={tag}_{sym}_{int(time.time())}")
        # Diagnostic-only shadow order (slippage check — never touches P&L)
        try:
            import risk_gate
            if risk_gate.shadow_live_enabled(strategy):
                sr = broker.place_order(side, sec_id, seg, qty, "LIMIT", price,
                                        trad_sym=trad_sym, tag=f"{tag}_{sym}_SHADOW")
                log(f"[BROKER-SHADOW] {trad_sym} {side} {qty} @ {price:.2f} -> "
                    f"{sr['status'].upper()} ({sr['reason']}) — paper fill unaffected")
        except Exception as e:
            log(f"[BROKER-SHADOW] failed (paper fill unaffected): {e}")

    # ── Persist to trade DB (best-effort, never blocks) ──
    # Live mode already recorded above (provisional row written before the
    # poll, then updated on confirm/reject) — skip here to avoid a duplicate.
    # Paper mode, and live mode where the provisional write itself failed,
    # still need this as their only record.
    if not (mode == "live" and provisional_id):
        try:
            import order_store
            bname = broker_name or (broker.name() if hasattr(broker, "name") else risk_gate.default_broker())
            order_store.record(side, qty, res["price"], source=source, strategy=strategy,
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
                       broker_name=broker_name, group_id=group_id,
                       product="NRML")
    except Exception as e:
        log(f"[HEDGE] failed (sell leg unaffected): {e}")
        return None
