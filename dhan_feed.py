"""
dhan_feed.py — live bid/ask store via Dhan WebSocket Full packet.

Runs the dhanhq MarketFeed in a background thread and keeps an in-memory
LIVE dict of best bid/ask/LTP per security_id. Used by smart_order.py to
place marketable-limit orders (BUY=ask, SELL=bid) that fill instantly at a
known price.

Usage:
    import dhan_feed
    dhan_feed.start(creds, [("NSE_EQ","2885"), ("NSE_FNO","56374")])
    q = dhan_feed.get_quote("2885")     # {'ltp','bid','ask','ts',...}
    dhan_feed.add(("NSE_FNO","79730"))  # subscribe more at runtime

Full packet gives 5-level depth; we keep level-1 (best) bid/ask.
"""

import asyncio
import socket
import threading
import time

# --- IPv4 force (DH-905) — before any Dhan network call ---
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

LIVE = {}                     # sec_id(str) -> {ltp,bid,ask,bid_qty,ask_qty,oi,ts}
_lock = threading.Lock()
_thread = None
_running = False
_ctx = None
_instruments = []             # list of MarketFeed tuples (seg_const, sec_id, Full)
_seen = set()                 # (seg_logical, sec_id) already subscribed
_feed = None
_pending_resub = False        # set True to make the loop rebuild cleanly


def _seg_const(seg_logical):
    from dhanhq import MarketFeed
    return {
        "NSE_EQ":  MarketFeed.NSE,
        "NSE_FNO": MarketFeed.NSE_FNO,
        "IDX_I":   MarketFeed.IDX,
    }.get(seg_logical, MarketFeed.NSE)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run_loop():
    """Background thread: one persistent connection; reconnect only on drop/error.

    Subscribe the full set BEFORE start() — a single connection holds up to 5000
    instruments, so the whole universe fits. We never tear down mid-session
    (avoids leaking Dhan's 5-connection budget)."""
    global _feed, _running, _pending_resub
    asyncio.set_event_loop(asyncio.new_event_loop())  # own loop for this thread
    from dhanhq import MarketFeed
    while _running:
        try:
            with _lock:
                instruments = list(_instruments)
            if not instruments:
                time.sleep(1)
                continue
            _feed = MarketFeed(_ctx, instruments, version="v2")
            _pending_resub = False
            while _running and not _pending_resub:
                _feed.run_forever()
                r = _feed.get_data()
                if not r:
                    continue
                if r.get("type") == "Full Data":
                    sid = str(r.get("security_id"))
                    dep = (r.get("depth") or [{}])[0]
                    with _lock:
                        LIVE[sid] = {
                            "ltp":     _f(r.get("LTP")),
                            "bid":     _f(dep.get("bid_price")),
                            "ask":     _f(dep.get("ask_price")),
                            "bid_qty": dep.get("bid_quantity"),
                            "ask_qty": dep.get("ask_quantity"),
                            "oi":      r.get("OI"),
                            "ts":      time.time(),
                        }
            # clean close before any reconnect (await the coroutine properly)
            _safe_disconnect()
        except Exception:
            _safe_disconnect()
            time.sleep(2)  # reconnect after a brief pause


def _safe_disconnect():
    global _feed
    try:
        if _feed is not None:
            coro = _feed.disconnect()
            if asyncio.iscoroutine(coro):
                asyncio.get_event_loop().run_until_complete(coro)
    except Exception:
        pass
    finally:
        _feed = None


def start(creds, sec_tuples=None):
    """Start the feed thread. creds={jwt_token,client_id}. sec_tuples=[(seg,sec_id),...]."""
    global _thread, _running, _ctx
    if _running:
        if sec_tuples:
            for t in sec_tuples:
                add(t)
        return
    from dhanhq import DhanContext
    _ctx = DhanContext(creds["client_id"], creds["jwt_token"])
    if sec_tuples:
        for t in sec_tuples:
            _queue(t)
    _running = True
    _thread = threading.Thread(target=_run_loop, daemon=True)
    _thread.start()


def _queue(sec_tuple):
    """Add to instrument list without restarting (used before start)."""
    from dhanhq import MarketFeed
    seg, sid = sec_tuple[0], str(sec_tuple[1])
    if (seg, sid) in _seen:
        return
    _seen.add((seg, sid))
    with _lock:
        _instruments.append((_seg_const(seg), sid, MarketFeed.Full))


def add(sec_tuple):
    """Subscribe one more instrument at runtime; loop rebuilds connection cleanly.
    Prefer subscribing the full set before start() — runtime adds cause a
    reconnect. Options at order-time usually use smart_order's REST fallback."""
    global _pending_resub
    before = len(_seen)
    _queue(sec_tuple)
    if _running and len(_seen) != before:
        _pending_resub = True


def get_quote(sec_id):
    with _lock:
        return dict(LIVE.get(str(sec_id), {}))


def best_bid(sec_id):
    return get_quote(sec_id).get("bid")


def best_ask(sec_id):
    return get_quote(sec_id).get("ask")


def stop():
    global _running
    _running = False
