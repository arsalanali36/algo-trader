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

    if not ref:  # feed empty -> REST LTP fallback
        ref = (broker.quote(sec_id, seg) or {}).get("ltp")
        src = "rest_ltp"
    if not ref:
        return None, "none"

    price = ref * (1 + buf) if side == "BUY" else ref * (1 - buf)
    return _snap(price), src


def execute(side, sym, sec_id, seg, qty, trad_sym, mode, broker,
            buffer_bps=10, log=print, tag="UNIV"):
    """Execute one entry/exit.

    Returns: {ok, price, src, status, reason, order_id}
      status: 'paper' | 'filled' | 'pending' | 'rejected'
    Always logs an intended-fill line (P&L truth, same for paper & live).
    Live also fires the real order and logs a [BROKER] status line.
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

    return res
