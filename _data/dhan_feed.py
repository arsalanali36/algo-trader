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
import json
import logging
import os
import socket
import sqlite3
import threading
import time
from pathlib import Path

# --- IPv4 force (DH-905) — before any Dhan network call ---
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

LIVE = {}                     # sec_id(str) -> {ltp,bid,ask,bid_qty,ask_qty,oi,volume,ts}
_lock = threading.Lock()
_thread = None
_running = False
_creds = None                 # {"client_id":..., "jwt_token":...}
_instruments = []              # list of (exch_code:int, sec_id:str, 21) tuples
_seen = set()                  # (seg_logical, sec_id) already subscribed
_feed = None
_pending_resub = False         # set True to make the loop rebuild cleanly

# ── Cross-process connection ownership (TRAP #87/88/89) ─────────────────────
# Dhan's feed gateway allows only a limited number of concurrent WebSocket
# connections per account — every process that independently calls start()
# (algo-dashboard, algo-monitor, every live strategy) opens its OWN
# connection, and with 2+ running at once they permanently collide (HTTP
# 429 on whichever reconnects while another already holds a slot; backoff
# only slows the collisions, it can't eliminate them — see TRAP #87). Fixed
# 2026-07-03 by electing exactly ONE process-wide "owner" via the same
# sqlite cross-process pattern dhan_rate_limiter.py already uses (works
# identically on Windows dev + the Linux VPS, no new dependency). Only the
# owner actually opens a connection; every other process's LIVE dict just
# stays empty and its callers fall back to REST (already how every
# consumer here is written — this was verified before relying on it,
# see TRAP #88). Heartbeat-based, not a clean release: this codebase has
# no SIGTERM handlers anywhere (TRAP #58), so a killed owner's row simply
# goes stale and another process takes over automatically.
_OWNER_DB          = Path(__file__).resolve().parent.parent / "data" / "dhan_feed_owner.db"
_OWNER_STALE_SECS  = 30    # owner presumed dead if no heartbeat in this long — another process may take over
_HEARTBEAT_EVERY   = 10    # seconds between heartbeat renewals while connected
_NOT_OWNER_RETRY   = 5     # seconds between ownership-claim retries when not the owner

# ── Cross-process QUOTE store + subscription requests (ADR-013, 2026-09-02) ──
# The owner election above fixed the connection collisions, but it left the
# data where only the owner could see it: LIVE is a per-process dict, so in
# every OTHER process (each strategy trader, the dashboard when a strategy
# happened to win the race) get_quote() was permanently {} and smart_order
# priced every real order off REST LTP — measured 26/26 live orders
# `src=rest_ltp`, bid=None (2026-09-01). The owner now mirrors every tick into
# a small sqlite table that any process can read, and any process can REQUEST a
# subscription by inserting a row the owner picks up within ~1s — no reconnect.
#
# Runtime `add()` used to flip `_pending_resub` and REBUILD the whole
# connection for every new instrument; at 09:07-09:10 the dashboard adds legs
# one by one → a reconnect storm → Dhan 429s the next handshake while it still
# holds the just-dropped slot (963 dhan_feed warnings on 2026-08-31 alone).
# New instruments are now subscribed IN the live connection (RequestCode 21
# packet on the existing socket), exactly what dhanhq's subscribe_symbols()
# does minus its `ws.closed` attribute that websockets>=13 removed.
_QUOTE_DB          = Path(__file__).resolve().parent.parent / "data" / "dhan_feed_quotes.db"
_FLUSH_EVERY       = 0.3   # s — batch LIVE → sqlite writes (hundreds of ticks/s otherwise)
_SUBS_CHECK_EVERY  = 1.0   # s — owner polls the subs table for new requests
_RECV_TIMEOUT      = 1.0   # s — ws.recv() timeout so housekeeping runs in a quiet market
_SUBS_MAX_AGE      = 20 * 3600  # s — requests older than this are pruned on reconnect (expired legs)
_qdb_lock = threading.Lock()


def _quote_conn():
    _QUOTE_DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(_QUOTE_DB), timeout=5, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")     # price cache — losing the last 300ms on a crash is fine
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute("CREATE TABLE IF NOT EXISTS quotes (sec_id TEXT PRIMARY KEY, ltp REAL, bid REAL, ask REAL, "
                 "bid_qty INTEGER, ask_qty INTEGER, oi INTEGER, volume INTEGER, ts REAL)")
    conn.execute("CREATE TABLE IF NOT EXISTS subs (seg TEXT, sec_id TEXT, ts REAL, PRIMARY KEY (seg, sec_id))")
    return conn


def _flush_quotes(rows):
    """rows: {sec_id: quote-dict}. Best-effort; a failed flush just delays sharing."""
    if not rows:
        return
    try:
        with _qdb_lock:
            conn = _quote_conn()
            try:
                conn.executemany(
                    "INSERT OR REPLACE INTO quotes (sec_id, ltp, bid, ask, bid_qty, ask_qty, oi, volume, ts) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    [(sid, q.get("ltp"), q.get("bid"), q.get("ask"), q.get("bid_qty"), q.get("ask_qty"),
                      q.get("oi"), q.get("volume"), q.get("ts")) for sid, q in rows.items()])
            finally:
                conn.close()
    except Exception as e:
        logging.getLogger("dhan_feed").warning(f"[dhan_feed] quote flush failed: {e}")


def _read_shared_quote(sec_id):
    try:
        conn = _quote_conn()
        try:
            r = conn.execute("SELECT ltp, bid, ask, bid_qty, ask_qty, oi, volume, ts FROM quotes WHERE sec_id=?",
                             (str(sec_id),)).fetchone()
        finally:
            conn.close()
    except Exception:
        return {}
    if not r:
        return {}
    return {"ltp": r[0], "bid": r[1], "ask": r[2], "bid_qty": r[3], "ask_qty": r[4],
            "oi": r[5], "volume": r[6], "ts": r[7], "src": "shared"}


def _request_sub(seg, sec_id):
    """Any process → 'owner, please subscribe this'. Idempotent."""
    try:
        conn = _quote_conn()
        try:
            conn.execute("INSERT OR IGNORE INTO subs (seg, sec_id, ts) VALUES (?,?,?)",
                         (seg, str(sec_id), time.time()))
        finally:
            conn.close()
    except Exception as e:
        logging.getLogger("dhan_feed").warning(f"[dhan_feed] sub request failed {seg}:{sec_id}: {e}")


def _read_subs(prune_older_than=None):
    try:
        conn = _quote_conn()
        try:
            if prune_older_than is not None:
                conn.execute("DELETE FROM subs WHERE ts < ?", (time.time() - prune_older_than,))
            return [(seg, sid) for seg, sid in conn.execute("SELECT seg, sec_id FROM subs").fetchall()]
        finally:
            conn.close()
    except Exception:
        return []


def owner_alive(max_age=None):
    """True if SOME process currently holds the feed connection (heartbeat fresh).
    Callers use this to decide whether a short wait for a quote is worth it."""
    try:
        conn = _owner_conn()
        try:
            row = conn.execute("SELECT heartbeat FROM owner WHERE id=1").fetchone()
        finally:
            conn.close()
    except Exception:
        return False
    if not row:
        return False
    return (time.time() - row[0]) <= (max_age if max_age is not None else _OWNER_STALE_SECS)


# Code generation of THIS module. Bump when the owner's on-the-wire behaviour
# changes in a way older owners can't provide (gen 2 = writes the shared quote
# store + honours the subs table, ADR-013). A newer-gen process may TAKE OVER
# ownership from an older-gen owner even while its heartbeat is fresh —
# otherwise a long-lived strategy fork running yesterday's module (supervisor
# re-warms only at 09:10) would hold the socket all day and nobody would ever
# populate the shared store. Same-gen owners still respect the heartbeat.
_FEED_GEN = 2


def _owner_conn():
    _OWNER_DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(_OWNER_DB), timeout=5, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE IF NOT EXISTS owner (id INTEGER PRIMARY KEY, pid INTEGER, heartbeat REAL)")
    try:
        conn.execute("ALTER TABLE owner ADD COLUMN gen INTEGER")   # older DBs; NULL = gen-1 owner
    except sqlite3.OperationalError:
        pass
    return conn


def _claim_or_renew_ownership():
    """Returns True if THIS process is (or just became) the sole owner
    allowed to hold a dhan_feed WebSocket connection right now."""
    now = time.time()
    pid = os.getpid()
    conn = _owner_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT pid, heartbeat, gen FROM owner WHERE id=1").fetchone()
        if row is None:
            conn.execute("INSERT INTO owner(id, pid, heartbeat, gen) VALUES (1, ?, ?, ?)", (pid, now, _FEED_GEN))
            conn.execute("COMMIT")
            return True
        owner_pid, heartbeat, owner_gen = row
        if owner_pid == pid:
            conn.execute("UPDATE owner SET heartbeat=?, gen=? WHERE id=1", (now, _FEED_GEN))
            conn.execute("COMMIT")
            return True
        stale = (now - heartbeat) > _OWNER_STALE_SECS
        older_gen = (owner_gen or 1) < _FEED_GEN     # NULL = pre-gen owner (old module)
        if stale or older_gen:
            conn.execute("UPDATE owner SET pid=?, heartbeat=?, gen=? WHERE id=1", (pid, now, _FEED_GEN))
            conn.execute("COMMIT")
            if older_gen and not stale:
                logging.getLogger("dhan_feed").info(
                    f"[dhan_feed] took over feed ownership from older-gen owner pid={owner_pid} (gen {owner_gen or 1} → {_FEED_GEN})")
            return True
        conn.execute("ROLLBACK")
        return False
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return False
    finally:
        conn.close()

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


def _close_feed(feed):
    """Release a DhanFeed's WebSocket + asyncio transport before dropping it.

    MEMORY-LEAK FIX (2026-08-07): the loop below creates a BRAND-NEW DhanFeed
    on every reconnect (429 storm + every runtime `add()` that flips
    `_pending_resub`) and used to just reassign `_feed` away — the dead
    websocket's socket/receive-buffers stayed pinned by this thread's
    persistent event loop and were never GC'd, growing RSS ~900MB over a
    trading day until the VPS swap-thrashed. Worse, dhanhq==2.0.2's own
    `disconnect()` only SENDS a RequestCode-12 message and never calls
    `ws.close()`, so we must close the socket ourselves.
    """
    if feed is None:
        return
    # (1) tell the server we're going away (best-effort; harmless if already dead)
    try:
        feed.close_connection()   # sync wrapper → loop.run_until_complete(disconnect())
    except Exception:
        pass
    # (2) actually close the underlying websocket — dhanhq's disconnect() doesn't
    try:
        ws = getattr(feed, "ws", None)
        loop = getattr(feed, "loop", None)
        if ws is not None and loop is not None and not loop.is_closed():
            loop.run_until_complete(ws.close())
    except Exception:
        pass


_TICKER = 15  # DhanFeed v2 RequestCode for Ticker (LTP only)


def _instrument_tuple(seg, sid):
    # Indices: Dhan sends NOTHING for a Full (21) subscribe on IDX_I (verified live
    # 2026-09-02: code 21 → 0 msgs in 5s, code 15 → Ticker stream). Index has no
    # depth anyway, so subscribe Ticker there and Full everywhere else.
    return (_EXCH_CODE.get(seg, 1), str(sid), _TICKER if seg == "IDX_I" else _FULL)


def _send_subscribe(feed, tuples):
    """Subscribe more instruments ON the live socket (no reconnect).

    Same v2 packet dhanhq's subscribe_symbols() builds, sent synchronously on the
    feed's own loop from the feed thread. dhanhq's version is avoided because it
    tests `ws.closed`, an attribute websockets>=13 no longer has (AttributeError
    on the VPS's websockets 16)."""
    if not tuples:
        return
    by_code = {}
    for ex, sid, rc in tuples:
        by_code.setdefault(int(rc), []).append((ex, sid))
    for rc, items in by_code.items():
        for i in range(0, len(items), 100):
            batch = items[i:i + 100]
            msg = {"RequestCode": rc, "InstrumentCount": len(batch),
                   "InstrumentList": [{"ExchangeSegment": feed.get_exchange_segment(ex), "SecurityId": sid}
                                      for ex, sid in batch]}
            feed.loop.run_until_complete(feed.ws.send(json.dumps(msg)))
    # keep dhanhq's own list coherent (cosmetic — a reconnect rebuilds from _instruments anyway)
    try:
        feed.instruments = list(set(feed.instruments) | set(tuples))
    except Exception:
        pass


def _recv(feed, timeout):
    """One message (processed dict) or None on timeout. Raises on socket death."""
    try:
        raw = feed.loop.run_until_complete(asyncio.wait_for(feed.ws.recv(), timeout))
    except asyncio.TimeoutError:
        return None
    r = feed.process_data(raw)
    if getattr(feed, "on_close", False):
        # first_byte 50 = server disconnection (805 too many connections / 807
        # token expired / ...). dhanhq only prints it — surface it so the outer
        # loop backs off and reconnects instead of spinning on a dead socket.
        raise RuntimeError("server disconnection packet (see dhanhq print above)")
    return r


def _run_loop():
    """Background thread (OWNER only): one persistent connection; reconnect on
    drop/error; new instruments subscribed in-connection; every tick mirrored to
    the shared sqlite store so all other processes can read it.

    DhanFeed's own `run_forever()` only does the one-shot connect+subscribe
    (see dhanhq source — `connect()` returns after subscribing, it doesn't
    loop). The continuous receive loop is ours (`_recv`, with a timeout so
    heartbeat / subscription requests / flushes still run when no ticks arrive)."""
    global _feed, _running, _pending_resub
    asyncio.set_event_loop(asyncio.new_event_loop())  # own loop for this thread
    from dhanhq import DhanFeed
    log = logging.getLogger("dhan_feed")

    # Exponential backoff on reconnect: Dhan 429s a handshake while it still
    # holds a just-dropped slot for the same account. Backoff caps at 30s and
    # resets to 2s the moment a connection is actually accepted.
    _backoff = 2
    _BACKOFF_MAX = 30
    _last_heartbeat = 0.0
    _last_flush = 0.0
    _last_subs_check = 0.0
    _dirty = {}

    while _running:
        try:
            # Only the elected owner actually connects — everyone else waits
            # and retries the claim periodically (in case the owner dies).
            if not _claim_or_renew_ownership():
                time.sleep(_NOT_OWNER_RETRY)
                continue

            # Union of this process's own instruments + every other process's requests.
            for seg, sid in _read_subs(prune_older_than=_SUBS_MAX_AGE):
                _queue((seg, sid))
            with _lock:
                instruments = list(_instruments)
            if not instruments:
                time.sleep(1)
                continue
            subscribed = set(instruments)

            _close_feed(_feed)   # release the previous feed's socket before reconnecting (leak fix)
            _feed = None
            _feed = DhanFeed(_creds["client_id"], _creds["jwt_token"], instruments, version="v2")
            _feed.run_forever()  # connect + subscribe (one-shot)
            _pending_resub = False
            _backoff = 2  # connection accepted — reset backoff for next time
            _last_heartbeat = _last_flush = _last_subs_check = time.time()
            log.info(f"[dhan_feed] connected (owner pid={os.getpid()}) — {len(instruments)} instruments")

            while _running and not _pending_resub:
                r = _recv(_feed, _RECV_TIMEOUT)
                now = time.time()
                rtype = r.get("type") if r else None
                if rtype == "Full Data":
                    sid = str(r.get("security_id"))
                    dep = (r.get("depth") or [{}])[0]
                    q = {
                        "ltp":     _f(r.get("LTP")),
                        "bid":     _f(dep.get("bid_price")),
                        "ask":     _f(dep.get("ask_price")),
                        "bid_qty": dep.get("bid_quantity"),
                        "ask_qty": dep.get("ask_quantity"),
                        "oi":      r.get("OI"),
                        "volume":  r.get("volume"),
                        "ts":      now,
                    }
                    with _lock:
                        LIVE[sid] = q
                    _dirty[sid] = q
                elif rtype in ("Ticker Data", "Quote Data") and r.get("LTP") is not None:
                    # Indices (IDX_I) never get a Full packet — Dhan answers a code-21
                    # subscribe on an index with Ticker packets only (verified live
                    # 2026-09-02: sec 13 → first_byte 2 "Ticker Data", option → 8
                    # "Full Data"). The old loop dropped these, so index LTP from the
                    # feed was ALWAYS None. Keep last known depth, refresh LTP + ts.
                    sid = str(r.get("security_id"))
                    with _lock:
                        q = dict(LIVE.get(sid) or {"bid": None, "ask": None, "bid_qty": None,
                                                   "ask_qty": None, "oi": None, "volume": None})
                        q["ltp"] = _f(r.get("LTP")); q["ts"] = now
                        LIVE[sid] = q
                    _dirty[sid] = q
                # ── housekeeping (runs on every message AND on every 1s timeout) ──
                if _dirty and now - _last_flush >= _FLUSH_EVERY:
                    _flush_quotes(_dirty); _dirty = {}; _last_flush = now
                if now - _last_heartbeat > _HEARTBEAT_EVERY:
                    _claim_or_renew_ownership(); _last_heartbeat = now
                if now - _last_subs_check >= _SUBS_CHECK_EVERY:
                    _last_subs_check = now
                    for seg, sid in _read_subs():
                        _queue((seg, sid))
                    with _lock:
                        new = [t for t in _instruments if t not in subscribed]
                    if new:
                        _send_subscribe(_feed, new)
                        subscribed.update(new)
                        log.info(f"[dhan_feed] +{len(new)} subscribed in-connection (total {len(subscribed)})")
        except Exception as e:
            _close_feed(_feed)   # release the failed feed's socket before backing off (leak fix)
            _feed = None
            log.warning(f"[dhan_feed] loop error, reconnecting in {_backoff}s: {e}")
            time.sleep(_backoff)
            _backoff = min(_backoff * 2, _BACKOFF_MAX)


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
    if not sid.isdigit():
        # Dhan security ids are numeric. Non-numeric = another venue's symbol
        # (Delta crypto legs "C-BTC-77400-020926" came through the dashboard's
        # per-position subscribe) — never send those to the Dhan socket (ADR-021).
        return
    if (seg, sid) in _seen:
        return
    _seen.add((seg, sid))
    with _lock:
        _instruments.append(_instrument_tuple(seg, sid))


def add(sec_tuple):
    """Subscribe one more instrument at runtime — from ANY process.

    Queues it locally (if this process is/becomes the owner it is subscribed
    in-connection within ~1s) AND records the request in the shared subs table
    so the current owner — whichever process that is — picks it up within ~1s.
    No reconnect is triggered any more (that was the 429 storm)."""
    seg, sid = sec_tuple[0], str(sec_tuple[1])
    before = len(_seen)
    _queue(sec_tuple)
    if len(_seen) != before or not _running:
        _request_sub(seg, sid)


# Kitna purana tick "abhi ka" mana jaaye. Har wo caller jo is feed ke price pe
# paisa lagata hai (order pricing, SL/TP, liquidity gate) isko pass kare — value
# teen jagah alag-alag likhi thi (trader_dashboard._FEED_MAX_AGE, webhook me
# hardcoded 12), ab yahin se.
FEED_MAX_AGE = 12


def get_quote(sec_id, max_age=None):
    """Latest WebSocket tick for sec_id, or {} if none.

    Source order: this process's own LIVE dict (owner) → the shared sqlite
    store written by whichever process owns the connection (everyone else).

    max_age (seconds): if given, a tick older than max_age (or one with no
    timestamp) is treated as STALE and {} is returned — so callers using the
    `get_quote(...).get("ltp") or rest_fallback` pattern fall through to a
    fresh price source instead of silently trusting a frozen last tick. This
    is the guard for the class of bug where a contract's WS subscription dies
    (429 throttle etc.) but the last tick stays in LIVE forever, non-zero,
    short-circuiting the fallback and pinning a position's P&L/SL to a stale
    price. Default None = unchanged (returns whatever is cached)."""
    sid = str(sec_id)
    with _lock:
        q = dict(LIVE.get(sid, {}))

    def _fresh(qq):
        if not qq:
            return False
        if max_age is None:
            return True
        ts = qq.get("ts")
        return bool(ts) and (time.time() - ts) <= max_age

    if _fresh(q):
        return q
    shared = _read_shared_quote(sid)
    if _fresh(shared):
        return shared
    return q if (q and max_age is None) else {}


def snapshot(max_age=None):
    """All known quotes {sec_id: quote} — this process's LIVE merged over the
    shared store (local wins). For bulk readers (SSE stream) that used to iterate
    `LIVE` directly and therefore saw {} in every non-owner process."""
    out = {}
    try:
        conn = _quote_conn()
        try:
            rows = conn.execute("SELECT sec_id, ltp, bid, ask, bid_qty, ask_qty, oi, volume, ts FROM quotes").fetchall()
        finally:
            conn.close()
        for r in rows:
            out[str(r[0])] = {"ltp": r[1], "bid": r[2], "ask": r[3], "bid_qty": r[4], "ask_qty": r[5],
                              "oi": r[6], "volume": r[7], "ts": r[8], "src": "shared"}
    except Exception:
        pass
    with _lock:
        for sid, q in LIVE.items():
            out[str(sid)] = dict(q)
    if max_age is not None:
        cut = time.time() - max_age
        out = {sid: q for sid, q in out.items() if q.get("ts") and q["ts"] >= cut}
    return out


def wait_quote(sec_id, seg="NSE_FNO", timeout=2.0, max_age=None, poll=0.1):
    """Request a subscription and wait up to `timeout` s for a fresh tick.
    Returns the quote dict or {}. Returns {} IMMEDIATELY (no wait) when no
    process holds a feed connection — a wait can't help then, and an order
    path must not stall on it."""
    q = get_quote(sec_id, max_age=max_age)
    if q:
        return q
    if not owner_alive():
        return {}
    add((seg, str(sec_id)))
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll)
        q = get_quote(sec_id, max_age=max_age)
        if q:
            return q
    return {}


def best_bid(sec_id):
    return get_quote(sec_id).get("bid")


def best_ask(sec_id):
    return get_quote(sec_id).get("ask")


def stop():
    global _running
    _running = False
