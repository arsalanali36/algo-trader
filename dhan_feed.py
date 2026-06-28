"""
dhan_feed.py — live bid/ask store via Dhan WebSocket Full packet.

Runs the dhanhq feed in a background thread and keeps an in-memory LIVE
dict of best bid/ask/LTP per security_id. Used by smart_order.py to place
marketable-limit orders (BUY=ask, SELL=bid) that fill instantly at a known
price, and by pos_monitor_loop for SL/TP/EOD checks.

2026-06-27 REWRITE: the original version imported `DhanContext`/`MarketFeed`
from `dhanhq` — neither symbol exists in the installed dhanhq==2.0.2 (it
exports `DhanFeed`, `OrderSocket`, `marketfeed`, `orderupdate` instead), so
the feed never started at all, silently (later loudly, after a 2026-06-24
fix) — see LESSONS.md TRAP #10/#2. This version is built against the
ACTUAL installed class: `dhanhq.DhanFeed(client_id, access_token,
instruments, version='v2')`, subscribing Full packets (RequestCode 21,
same depth/OI/LTP fields as before) so every caller below needs zero
changes — same `LIVE` dict shape, same `start/add/get_quote` API.

Usage (unchanged):
    import dhan_feed
    dhan_feed.start(creds, [("NSE_EQ","2885"), ("NSE_FNO","56374")])
    q = dhan_feed.get_quote("2885")     # {'ltp','bid','ask','ts',...}
    dhan_feed.add(("NSE_FNO","79730"))  # subscribe more at runtime

Why this matters beyond SL/TP: once this actually connects, LTP no longer
needs to come from REST polling (`/v2/marketfeed/ltp`) for any subscribed
instrument — that's the single biggest source of load on `dhan_rate_limiter`
(see LESSONS.md TRAP #2 v2). `shared_ltp_cache`/`dhan_broker.quote()` should
prefer this LIVE dict first wherever practical going forward.
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
_creds = None                 # {"client_id":..., "jwt_token":...}
_instruments = []              # list of (exch_code:int, sec_id:str, 21) tuples
_seen = set()                  # (seg_logical, sec_id) already subscribed
_feed = None
_pending_resub = False         # set True to make the loop rebuild cleanly

# exchange segment (string, as used everywhere else in this repo) -> Dhan's
# numeric exchange code expected by DhanFeed's instrument tuples (matches
# DhanFeed.get_exchange_segment's reverse mapping).
_EXCH_CODE = {
    "IDX_I": 0, "NSE_EQ": 1, "NSE_FNO": 2, "NSE_CURRENCY": 3,
    "BSE_EQ": 4, "MCX_COMM": 5, "BSE_CURRENCY": 7, "BSE_FNO": 8,
}

_FULL = 21  # DhanFeed v2 RequestCode for the Full packet (5-level depth + OI)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run_loop():
    """Background thread: one persistent connection; reconnect on drop/error.

    DhanFeed's own `run_forever()` only does the one-shot connect+subscribe
    (see dhanhq source — `connect()` returns after subscribing, it doesn't
    loop). The actual continuous receive loop is `get_data()` called
    repeatedly, which we do here."""
    global _feed, _running, _pending_resub
    asyncio.set_event_loop(asyncio.new_event_loop())  # own loop for this thread
    from dhanhq import DhanFeed

    while _running:
        try:
            with _lock:
                instruments = list(_instruments)
            if not instruments:
                time.sleep(1)
                continue
            _feed = DhanFeed(_creds["client_id"], _creds["jwt_token"], instruments, version="v2")
            _feed.run_forever()  # connect + subscribe (one-shot)
            _pending_resub = False

            while _running and not _pending_resub:
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
        except Exception as e:
            import logging
            logging.getLogger("dhan_feed").warning(f"[dhan_feed] loop error, reconnecting in 2s: {e}")
            time.sleep(2)  # reconnect after a brief pause


def start(creds, sec_tuples=None):
    """Start the feed thread. creds={jwt_token,client_id}. sec_tuples=[(seg,sec_id),...]."""
    global _thread, _running, _creds
    if _running:
        if sec_tuples:
            for t in sec_tuples:
                add(t)
        return
    _creds = {"client_id": creds["client_id"], "jwt_token": creds["jwt_token"]}
    if sec_tuples:
        for t in sec_tuples:
            _queue(t)
    _running = True
    _thread = threading.Thread(target=_run_loop, daemon=True)
    _thread.start()


def _queue(sec_tuple):
    """Add to instrument list without restarting (used before start)."""
    seg, sid = sec_tuple[0], str(sec_tuple[1])
    if (seg, sid) in _seen:
        return
    _seen.add((seg, sid))
    with _lock:
        _instruments.append((_EXCH_CODE.get(seg, 1), sid, _FULL))


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
