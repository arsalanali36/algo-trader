"""
broker_sync.py — Ghost position detector + reconciler (TRAP #44)

Problem: jab broker pe position manually close ho ya exit order reject ho,
app DB mein "OPEN" dikhata rehta hai → monitor 5s har cycle watch karta hai →
jab trailing profit lock fire karta hai → BUY/SELL order bhejta hai already-flat
position pe → new accidental LONG/SHORT open ho jaata hai.

Fix: har 2 minute pe broker ke actual positions se compare karo. Jo broker pe
flat (qty=0) hai lekin DB mein OPEN hai → mark externally_closed → monitor skip.

Also: _do_squareoff mein is_flat() call karo BEFORE placing exit order, taaki
agar position already flat hai to exit order na daala jaye (TRAP #44 ka main guard).
"""

import threading
import time

_lock     = threading.Lock()
_INTERVAL = 120   # seconds between auto-syncs in pos_monitor_loop
_CACHE_TTL = 35   # seconds — pre-exit check uses this cached data (avoids per-SL API hit)

# broker_name → {"positions": {sym_key: net_qty}, "ts": float}
_cache: dict = {}
_last_auto_sync = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Public API — called from pos_monitor_loop and _do_squareoff
# ──────────────────────────────────────────────────────────────────────────────

def sync_if_due(open_positions: list, log=print) -> set:
    """
    Call from pos_monitor_loop every tick.
    Runs reconciliation every _INTERVAL seconds.
    Returns set of DB row IDs that were just marked externally_closed.
    """
    global _last_auto_sync
    with _lock:
        if time.time() - _last_auto_sync < _INTERVAL:
            return set()
        _last_auto_sync = time.time()
    return _run_sync(open_positions, log)


def force_sync(open_positions: list, log=print) -> set:
    """Manual trigger — bypass cooldown (for /api/sync-positions route)."""
    global _last_auto_sync
    with _lock:
        _last_auto_sync = 0.0
    return _run_sync(open_positions, log)


def is_flat(broker_name: str, trad_sym: str, sec_id: str) -> bool:
    """
    Pre-exit check in _do_squareoff: is this position already flat at broker?
    Uses cached data (updated by sync_if_due / force_sync). Returns False if
    cache is stale / unavailable — FAIL OPEN so real exits are never blocked.
    """
    with _lock:
        entry = _cache.get(broker_name)
    if not entry:
        return False
    if time.time() - entry["ts"] > _CACHE_TTL:
        return False  # stale cache — assume still open
    pos = entry["positions"]
    return _check_flat(broker_name, pos, trad_sym, sec_id)


# ──────────────────────────────────────────────────────────────────────────────
# Core reconciliation
# ──────────────────────────────────────────────────────────────────────────────

def _run_sync(open_positions: list, log=print) -> set:
    closed_ids: set = set()
    if not open_positions:
        return closed_ids

    # Group by broker
    by_broker: dict = {}
    for p in open_positions:
        br = (p.get("broker") or "dhan").lower()
        by_broker.setdefault(br, []).append(p)

    for broker_name, legs in by_broker.items():
        broker_pos = _fetch_and_cache(broker_name, log)
        if broker_pos is None:
            continue  # fetch failed — skip, don't wrongly close anything

        for p in legs:
            sym    = p.get("sym") or p.get("trad_sym") or ""
            sec_id = str(p.get("sec_id") or "")
            row_id = p.get("id")

            if _check_flat(broker_name, broker_pos, sym, sec_id):
                try:
                    import order_store
                    order_store.mark_externally_closed(row_id)
                    closed_ids.add(row_id)
                    log(f"[broker_sync] ✅ GHOST CLEARED — {sym} flat at {broker_name} "
                        f"(id={row_id}). Marked externally_closed. TRAP #44 prevented.", flush=True)
                except Exception as _e:
                    log(f"[broker_sync] ⚠️ mark_externally_closed failed for {sym}: {_e}", flush=True)

    if closed_ids:
        log(f"[broker_sync] Cleared {len(closed_ids)} ghost position(s) this cycle.", flush=True)
    return closed_ids


def _fetch_and_cache(broker_name: str, log=print):
    """Fetch live positions from broker, update cache. Returns {sym_key: net_qty} or None."""
    try:
        from brokers import get_broker
        broker = get_broker(broker_name)
        pos = broker.positions()   # {kite_tradingsymbol: qty} or {sec_id: qty}
        with _lock:
            _cache[broker_name] = {"positions": pos, "ts": time.time()}
        return pos
    except Exception as e:
        log(f"[broker_sync] ⚠️ {broker_name}.positions() failed: {e}", flush=True)
        return None


def _check_flat(broker_name: str, broker_pos: dict, trad_sym: str, sec_id: str) -> bool:
    """
    Return True ONLY if we have definitive evidence the position is flat.
    Return False if uncertain (safe default — never wrongly clear a real position).
    """
    if broker_name == "kite":
        # Kite uses its own date-encoded tradingsymbol (e.g. NIFTY2463023900PE)
        # resolve_kite_symbol() maps our trad_sym → kite format
        try:
            from brokers.kite_broker import resolve_kite_symbol
            kite_sym = resolve_kite_symbol(trad_sym)
            if kite_sym and kite_sym in broker_pos:
                return int(broker_pos[kite_sym]) == 0
        except Exception:
            pass
        return False  # can't map → uncertain → assume open

    else:
        # Dhan: broker_pos is {sec_id: net_qty}
        if sec_id and sec_id in broker_pos:
            return int(broker_pos[sec_id]) == 0
        return False  # not in response → uncertain → assume open
