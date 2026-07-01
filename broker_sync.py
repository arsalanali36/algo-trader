"""
broker_sync.py — Ghost position detector + reconciler (TRAP #44)

Problem: jab broker pe position manually close ho ya exit order reject ho,
app DB mein "OPEN" dikhata rehta hai → monitor 5s har cycle watch karta hai →
jab trailing profit lock fire karta hai → BUY/SELL order bhejta hai already-flat
position pe → new accidental LONG/SHORT open ho jaata hai.

Fix: har 30s pe broker ke actual positions se compare karo. Jo broker pe
flat (qty=0) hai lekin DB mein OPEN hai:
  1. Broker trades fetch karo → exit fill price dhundho (S3/S8 fix)
  2. order_store mein exit leg record karo taaki P&L closed ho (pnl null na rahe)
  3. entry leg mark_externally_closed
  4. Agar group_id hai aur sibling OPEN hai → NAKED POSITION alert (S5 fix)
  5. webhook_executor._wh_state ko clear karo taaki TV EXIT ghost order na bheje (S7 fix)

Also: _do_squareoff mein is_flat() call karo BEFORE placing exit order, taaki
agar position already flat hai to exit order na daala jaye (TRAP #44 ka main guard).
"""

import threading
import time

_lock     = threading.Lock()
_INTERVAL = 30    # seconds between auto-syncs (was 120 — reduced for faster ghost detection, S6 fix)
_CACHE_TTL = 35   # seconds — pre-exit check uses this cached data (avoids per-SL API hit)
_UNTRACKED_INTERVAL = 30   # seconds between untracked-position scans (TRAP #58)

# broker_name → {"positions": {sym_key: net_qty}, "ts": float}
_cache: dict = {}
_last_auto_sync = 0.0
_last_untracked_scan = 0.0


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


def untracked_scan_if_due(log=print) -> None:
    """
    Call from pos_monitor_loop every tick (mirrors sync_if_due's cadence).
    Detects the MIRROR-IMAGE gap of ghost-position detection (TRAP #44):
    sync_if_due/_run_sync only ever look at positions order_store ALREADY
    thinks are open, and check if the broker has gone flat on them. This
    scan instead pulls the broker's actual live positions directly — so it
    also catches a position the broker has, that order_store has NO row for
    at all (e.g. the process was SIGTERM'd mid-way through smart_order.execute()'s
    live fill-confirm poll, before order_store.record() ever ran — no signal
    handler exists anywhere in this codebase to prevent that). See TRAP #58.
    Independent of open_positions (doesn't need order_store to already know
    about ANYTHING) — this is what makes it catch the worst case: the
    orphaned position being the ONLY position that exists.
    """
    global _last_untracked_scan
    with _lock:
        if time.time() - _last_untracked_scan < _UNTRACKED_INTERVAL:
            return
        _last_untracked_scan = time.time()
    _run_untracked_scan(log)


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

    # Build group_id → [leg, ...] map for naked-leg detection (S5)
    group_map: dict = {}
    for p in open_positions:
        gid = p.get("group_id") or ""
        if gid:
            group_map.setdefault(gid, []).append(p)

    for broker_name, legs in by_broker.items():
        broker_pos = _fetch_and_cache(broker_name, log)
        if broker_pos is None:
            continue  # fetch failed — skip, don't wrongly close anything

        # Fetch today's broker fills once per broker (for exit price — S3/S8 fix)
        broker_fills = _fetch_fills(broker_name, log)  # {sym_key: avg_px} or {}

        for p in legs:
            sym    = p.get("sym") or p.get("trad_sym") or ""
            sec_id = str(p.get("sec_id") or "")
            row_id = p.get("id")

            if not _check_flat(broker_name, broker_pos, sym, sec_id):
                continue

            # ── S3/S8: record exit leg with actual fill price ─────────────────
            exit_px = _resolve_exit_price(broker_name, broker_fills, sym, sec_id)
            if exit_px and exit_px > 0:
                try:
                    import order_store
                    close_side = "SELL" if (p.get("entry") or p.get("side") or "BUY") == "BUY" else "BUY"
                    order_store.record(
                        close_side,
                        int(p.get("qty") or 0),
                        exit_px,
                        source=p.get("source") or "broker_sync",
                        strategy=p.get("strategy") or "",
                        mode=p.get("mode") or "live",
                        broker=broker_name,
                        symbol=p.get("symbol") or "",
                        instrument=p.get("instrument") or "",
                        trad_sym=sym,
                        sec_id=sec_id,
                        segment=p.get("segment") or "",
                        status="filled",
                        tags=["EXTERNALLY_CLOSED", "MANUAL_EXIT_BROKER"],
                        group_id=p.get("group_id") or "",
                    )
                    log(f"[broker_sync] 📝 EXIT RECORDED — {sym} @ ₹{exit_px:.2f} "
                        f"(broker fill price fetched, P&L now captured in order_store)", flush=True)
                except Exception as _re:
                    log(f"[broker_sync] ⚠️ exit record failed for {sym}: {_re}", flush=True)
            else:
                log(f"[broker_sync] ⚠️ {sym} flat at broker but fill price unavailable "
                    f"— marking externally_closed without exit leg (P&L will be null)", flush=True)
            # ─────────────────────────────────────────────────────────────────

            try:
                import order_store
                order_store.mark_externally_closed(row_id)
                closed_ids.add(row_id)
                log(f"[broker_sync] ✅ GHOST CLEARED — {sym} flat at {broker_name} "
                    f"(id={row_id}). Marked externally_closed. TRAP #44 prevented.", flush=True)
            except Exception as _e:
                log(f"[broker_sync] ⚠️ mark_externally_closed failed for {sym}: {_e}", flush=True)
                continue

            # ── S7: clear webhook_executor _wh_state so TV EXIT doesn't re-open ──
            try:
                import webhook_executor as _wh
                _wh.release_position(sec_id=sec_id, trad_sym=sym,
                                     reason="broker_sync_externally_closed")
            except Exception:
                pass
            # ──────────────────────────────────────────────────────────────────

            # ── S5: naked leg alert — hedge sibling closed, main SELL still open ─
            gid = p.get("group_id") or ""
            if gid and gid in group_map:
                siblings = [s for s in group_map[gid] if s.get("id") != row_id]
                for sib in siblings:
                    sib_entry = sib.get("entry") or sib.get("side") or ""
                    # If sibling is a SELL leg (naked option) and still open → alert
                    if sib_entry == "SELL" and sib.get("id") not in closed_ids:
                        _write_naked_alert(sib.get("sym") or sib.get("trad_sym") or sym,
                                           sib.get("id"), log)
            # ──────────────────────────────────────────────────────────────────

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


def _fetch_fills(broker_name: str, log=print) -> dict:
    """Fetch today's fills from broker. Returns {sym_or_secid: avg_px} for closed legs.
    Used to record exit price when a ghost position is detected (S3/S8 fix)."""
    try:
        from brokers import get_broker
        broker = get_broker(broker_name)
        fills = broker.trades()   # broker-specific — see kite_broker/dhan_broker
        result = {}
        for f in (fills or []):
            # Kite: tradingsymbol + average_price + transaction_type
            # Dhan: tradingSymbol + tradedPrice + transactionType
            sym = (f.get("tradingsymbol") or f.get("tradingSymbol") or
                   f.get("trad_sym") or "")
            sid = str(f.get("securityId") or f.get("sec_id") or "")
            px  = float(f.get("average_price") or f.get("tradedPrice") or
                        f.get("price") or 0)
            if px > 0:
                if sym:
                    result[sym] = px
                if sid:
                    result[sid] = px
        return result
    except Exception as e:
        log(f"[broker_sync] ⚠️ fills fetch ({broker_name}) failed: {e}", flush=True)
        return {}


def _resolve_exit_price(broker_name: str, fills: dict, trad_sym: str, sec_id: str):
    """Look up the exit fill price for a position that went flat. Returns float or None."""
    if not fills:
        return None
    # Try Kite symbol first, then sec_id, then trad_sym directly
    if broker_name == "kite":
        try:
            from brokers import get_broker
            kite_sym = get_broker("kite").resolve_symbol(trad_sym, sec_id=sec_id)
            if kite_sym and kite_sym in fills:
                return fills[kite_sym]
        except Exception:
            pass
    # Try sec_id
    if sec_id and sec_id in fills:
        return fills[sec_id]
    # Try trad_sym as-is (Dhan format often matches)
    if trad_sym and trad_sym in fills:
        return fills[trad_sym]
    return None


_NAKED_ALERT_KEY = "naked_leg"

def _write_naked_alert(sym: str, row_id, log=print):
    """Write a red banner alert when a SELL leg is left naked (hedge closed manually)."""
    try:
        import json as _j
        from pathlib import Path
        _af = Path(__file__).resolve().parent / "data" / "downloader_alert.json"
        existing = []
        try:
            existing = _j.loads(_af.read_text())
        except Exception:
            pass
        # Remove any previous naked alert for this sym. downloader_alert.json is
        # shared with auto_data_downloader.py, which writes plain strings, not
        # dicts — isinstance guard needed or .get() crashes (found via TRAP #59).
        existing = [a for a in existing if not (isinstance(a, dict)
                                                  and a.get("key") == _NAKED_ALERT_KEY
                                                  and a.get("sym") == sym)]
        existing.append({
            "key": _NAKED_ALERT_KEY,
            "sym": sym,
            "row_id": row_id,
            "level": "error",
            "msg": (f"🚨 NAKED POSITION: {sym} — hedge leg was closed at broker "
                    f"but SELL leg is still open. Margin risk HIGH. "
                    f"Close the SELL leg immediately or replace the hedge."),
        })
        _af.write_text(_j.dumps(existing))
        log(f"[broker_sync] 🚨 NAKED LEG ALERT written for {sym} (row_id={row_id})", flush=True)
    except Exception as _ae:
        log(f"[broker_sync] ⚠️ naked alert write failed: {_ae}", flush=True)


def _check_flat(broker_name: str, broker_pos: dict, trad_sym: str, sec_id: str) -> bool:
    """
    Return True ONLY if we have definitive evidence the position is flat.
    Return False if uncertain (safe default — never wrongly clear a real position).
    """
    if broker_name == "kite":
        # Kite uses its own date-encoded tradingsymbol (e.g. NIFTY2463023900PE)
        # resolve_symbol() maps our trad_sym → kite format
        try:
            from brokers import get_broker
            kite_sym = get_broker("kite").resolve_symbol(trad_sym, sec_id=sec_id)
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


# ──────────────────────────────────────────────────────────────────────────────
# Untracked-position scan (TRAP #58) — the mirror image of ghost detection.
# Ghost detection (above) asks "DB says open, is broker actually flat?"
# This asks "broker has a real position, does DB even know it exists?"
# ──────────────────────────────────────────────────────────────────────────────

_UNTRACKED_ALERT_KEY = "untracked_position"


def _ist_today_str() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def _known_broker_keys(broker_name: str, open_for_broker: list) -> set:
    """Identity keys order_store already has an OPEN row for, on this broker —
    sec_id for Dhan (exact match, no guessing needed), resolved kite_sym for
    Kite (forward-only Dhan-trad_sym -> Kite-symbol via KiteBroker.resolve_symbol,
    same direction TRAP #44's ghost detection already trusts)."""
    keys = set()
    kite_broker = None
    for p in open_for_broker:
        if broker_name == "kite":
            try:
                if kite_broker is None:
                    from brokers import get_broker
                    kite_broker = get_broker("kite")
                ks = kite_broker.resolve_symbol(p.get("sym") or p.get("trad_sym") or "",
                                                 sec_id=p.get("sec_id"))
                if ks:
                    keys.add(ks)
            except Exception:
                pass
        else:
            sid = str(p.get("sec_id") or "")
            if sid:
                keys.add(sid)
    return keys


def _run_untracked_scan(log=print) -> None:
    for broker_name in ("dhan", "kite"):
        try:
            import order_store
            from brokers import get_broker
            data = order_store.trades_for(_ist_today_str())
            open_for_broker = [p for p in (data.get("open") or [])
                                if (p.get("broker") or "dhan").lower() == broker_name]
            known = _known_broker_keys(broker_name, open_for_broker)

            broker = get_broker(broker_name)
            live = broker.positions_detailed() if hasattr(broker, "positions_detailed") else []
        except Exception as e:
            log(f"[broker_sync] untracked-scan ({broker_name}) fetch failed: {e}", flush=True)
            continue

        for pos in (live or []):
            key = pos.get("sec_id") if broker_name != "kite" else pos.get("kite_sym")
            key = str(key or "")
            if not key or key in known:
                continue
            _handle_untracked(broker_name, key, pos, log)


def _handle_untracked(broker_name: str, key: str, pos: dict, log=print) -> None:
    """A live broker position exists with NO matching order_store OPEN row.
    Always alert loudly. Auto-adopt into order_store ONLY for Dhan, where the
    broker gives us its own tradingSymbol/segment directly (no guessing) — for
    Kite, alert-only (never guess a Dhan trad_sym from a kite_sym, see TRAP #13/#22)."""
    label = pos.get("trad_sym") or key
    qty, side, avg = pos.get("qty"), pos.get("side"), pos.get("avg_price")
    adopted = False

    if broker_name == "dhan":
        trad_sym = pos.get("trad_sym") or ""
        seg = pos.get("segment") or "NSE_FNO"
        is_opt = "-CE" in trad_sym or "-PE" in trad_sym
        instrument = "options" if is_opt else ("EQUITY" if "EQ" in seg else "unknown")
        if trad_sym and pos.get("sec_id"):
            try:
                import order_store
                # avg_price==0 means Dhan itself didn't give us a cost basis
                # (e.g. proxy/edge response shape) — fall back to a live LTP
                # so this still gets *some* SL protection rather than being
                # dropped (TRAP #1's ₹0-fill guard would otherwise reject it).
                price = float(avg or 0)
                approx = False
                if price <= 0:
                    try:
                        import shared_ltp_cache
                        _sid = str(pos.get("sec_id"))
                        price = float(shared_ltp_cache.get(_sid) or
                                      shared_ltp_cache.get_stale(_sid) or 0)
                        approx = True
                    except Exception:
                        pass
                if price > 0:
                    tags = ["UNTRACKED_ADOPTED"]
                    if approx:
                        tags.append("APPROX_ENTRY_PRICE")
                    order_store.record(
                        side=(pos.get("side") or "BUY"),
                        qty=abs(int(qty or 0)),
                        price=price,
                        source="broker_sync",
                        strategy="unknown",
                        mode="live",
                        broker=broker_name,
                        symbol=trad_sym.split("-")[0] if trad_sym else "",
                        instrument=instrument,
                        trad_sym=trad_sym,
                        sec_id=str(pos.get("sec_id") or ""),
                        segment=seg,
                        status="open",
                        tags=tags,
                    )
                    adopted = True
                    log(f"[broker_sync] 🔧 ADOPTED untracked {trad_sym} qty={qty} "
                        f"@ ₹{price:.2f}{' (approx — LTP, not real cost basis)' if approx else ''} "
                        f"— order_store row created so SL/EOD protection now applies. "
                        f"Verify strategy/entry-price manually. TRAP #58.", flush=True)
            except Exception as _ae:
                log(f"[broker_sync] ⚠️ untracked adoption failed for {trad_sym}: {_ae}", flush=True)

    if not adopted:
        _write_untracked_alert(broker_name, key, label, qty, side, avg, log)


def _write_untracked_alert(broker_name, key, label, qty, side, avg, log=print):
    try:
        import json as _j
        from pathlib import Path
        _af = Path(__file__).resolve().parent / "data" / "downloader_alert.json"
        existing = []
        try:
            existing = _j.loads(_af.read_text())
        except Exception:
            pass
        # downloader_alert.json is shared with auto_data_downloader.py, which
        # writes PLAIN STRINGS (not dicts) — a naive a.get(...) on those raises
        # AttributeError. Keep any non-dict entry untouched (not ours to dedupe),
        # only dedupe our own dict-shaped entries.
        existing = [a for a in existing
                    if not (isinstance(a, dict) and a.get("key") == _UNTRACKED_ALERT_KEY
                            and a.get("broker") == broker_name and a.get("sym") == key)]
        existing.append({
            "key": _UNTRACKED_ALERT_KEY,
            "broker": broker_name,
            "sym": key,
            "level": "error",
            "msg": (f"🚨 UNTRACKED LIVE POSITION ({broker_name}): {label} qty={qty} side={side} "
                    f"avg=₹{avg or 0:.2f} — this position exists at the broker but has NO row in "
                    f"order_store. It has ZERO SL/TP/EOD protection and is invisible to RMS capital "
                    f"checks. Close it manually or add it via the dashboard. See LESSONS.md TRAP #58."),
        })
        _af.write_text(_j.dumps(existing))
        log(f"[broker_sync] 🚨 UNTRACKED POSITION ALERT written for {broker_name}:{label}", flush=True)
    except Exception as _ae:
        log(f"[broker_sync] ⚠️ untracked alert write failed: {_ae}", flush=True)
