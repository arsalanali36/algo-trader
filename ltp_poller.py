"""
ltp_poller.py — single batched Dhan LTP poller (worklist P7, TRAP #2 family).

Problem: brokers/dhan_broker.quote() and trader_dashboard._rest_ltp_fallback()
each hit /v2/marketfeed/ltp with ONE sec_id per request, while Dhan accepts up
to 1000 instruments per call at the same ~1 req/sec account-wide budget.
pos_monitor_loop wants every open position's LTP every 5s — N open positions
meant N separate calls (or stale-cache misses), all competing with real orders
for the same 1 req/sec. _rest_ltp_fallback additionally bypassed
dhan_rate_limiter entirely (no acquire(), no note_429()) — invisible to the
cross-process throttle/cooldown.

Fix: ONE daemon thread (started by monitor_daemon, next to pos_monitor_loop)
polls all of today's open positions' sec_ids + NIFTY/BANKNIFTY spot in ONE
batched request per cycle, grouped by segment, and fans the results out via
shared_ltp_cache — the cache every quote path already reads FIRST. Consumers
keep their direct-call fallbacks for sec_ids the poller doesn't watch (e.g. a
brand-new entry's option contract before its order_store row exists), but
those are now rare cache-miss one-offs, not the steady-state path.
"""
import json
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"

POLL_INTERVAL = 1.5      # s between batched calls — leaves budget for orders/candles
OFF_HOURS_SLEEP = 30     # s between market-hours re-checks when closed

# Always polled: index spot (risk_gate ITM checks, webhook index-trailing,
# strike resolution all read these constantly).
_IDX_ALWAYS = (("IDX_I", "13"), ("IDX_I", "25"))   # NIFTY, BANKNIFTY

_started = False
_start_lock = threading.Lock()


def _ist_now():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _market_hours() -> bool:
    n = _ist_now()
    if n.weekday() >= 5:
        return False
    hm = n.hour * 60 + n.minute
    return (9 * 60) <= hm <= (15 * 60 + 35)


def _watchlist() -> dict:
    """{sec_id_str: segment} worth polling — today's open positions + index spot."""
    pairs = {sid: seg for seg, sid in _IDX_ALWAYS}
    try:
        import order_store
        data = order_store.trades_for(_ist_now().strftime("%Y-%m-%d"))
        for p in (data.get("open") or []):
            sid = str(p.get("sec_id") or "")
            if not sid:
                continue
            if p.get("status") == "blocked" or "CAPITAL_BLOCKED" in (p.get("tags") or []):
                continue   # not real holdings — no price needed
            seg = p.get("segment") or \
                ("NSE_EQ" if (p.get("instrument") or "").upper() == "EQUITY" else "NSE_FNO")
            pairs[sid] = seg
    except Exception:
        pass   # order_store hiccup — poll just the indices this cycle
    return pairs


def _poll_once(log=print) -> None:
    pairs = _watchlist()
    if not pairs:
        return
    body = {}
    for sid, seg in pairs.items():
        try:
            body.setdefault(seg, []).append(int(sid))
        except (ValueError, TypeError):
            continue
    if not body:
        return
    try:
        import requests
        import dhan_rate_limiter as _rl
        cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        headers = {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"],
                   "Content-Type": "application/json"}
        _rl.acquire("ltp")
        r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                          json=body, headers=headers, timeout=6)
        if r.status_code == 429:
            _rl.note_429()
            return
        if r.status_code != 200:
            return
        out = {}
        for seg, node in (r.json().get("data", {}) or {}).items():
            for sid, v in (node or {}).items():
                try:
                    ltp = float(v.get("last_price") or v.get("ltp") or 0)
                except (TypeError, AttributeError):
                    continue
                if ltp > 0:
                    out[str(sid)] = ltp
        if out:
            import shared_ltp_cache
            shared_ltp_cache.put_many(out)
    except Exception as e:
        log(f"[ltp_poller] poll failed: {e}", flush=True)


def _loop(log=print) -> None:
    log("[ltp_poller] batched LTP poller started — one Dhan call per "
        f"{POLL_INTERVAL}s covers all open positions + index spot "
        "(via shared_ltp_cache)", flush=True)
    while True:
        try:
            if _market_hours():
                _poll_once(log)
                time.sleep(POLL_INTERVAL)
            else:
                time.sleep(OFF_HOURS_SLEEP)
        except Exception as e:
            log(f"[ltp_poller] loop error: {e}", flush=True)
            time.sleep(5)


def start(log=print) -> None:
    """Idempotent — safe to call from multiple init paths in one process.
    Do NOT start this in more than one PROCESS: monitor_daemon owns it
    (pos_monitor_loop is the main consumer); everyone else just reads the
    shared cache it keeps warm."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, kwargs={"log": log}, daemon=True).start()
