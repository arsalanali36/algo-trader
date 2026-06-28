"""
dhan_rate_limiter.py — single cross-process throttle + priority gate for EVERY
Dhan API call (LTP, candles, funds/margin, orders) from EVERY process:
range_trader, rsi_trader, universe_trader, webhook_executor, manual order,
bulk order, dashboard LTP polling, debug routes — all of them.

Why this exists (on top of shared_ltp_cache.py): Dhan enforces an
ACCOUNT-WIDE limit (~1 req/sec sustained, bursts trip DH-904), not
per-process. shared_ltp_cache already cuts down duplicate LTP reads across
processes, but it doesn't help order placement (never cached) or protect
against N independently-polite processes all deciding "I'm under my own
local limit" at the same instant and colliding on the shared account quota.

The key idea is PRIORITY, not just throttling: a manual/webhook/strategy
ORDER must never sit queued behind a background candle-scan loop just
because both want a Dhan slot at the same moment. So a fixed slice of every
1-second window is reserved for "order" priority and never consumed by
LTP/candle/account calls — and during a 429 cooldown, non-order traffic is
shut out entirely so the account has headroom to recover while orders still
get through.

Cross-process coordination uses sqlite (stdlib only, no new dependency) —
its file locking works identically on the Windows dev box and the Linux VPS.

Usage:
    import dhan_rate_limiter as rl
    rl.acquire("order")              # blocks briefly for a free slot, always eventually allowed
    r = requests.post(...)
    if r.status_code == 429:
        rl.note_429()                 # shrinks the effective rate for a cooldown window

    # or, for plain requests.post call sites:
    r = rl.throttled_post(url, priority="ltp", json=..., headers=..., timeout=5)

Priorities (highest to lowest):
    "order"   — real order placement/modify/cancel (manual, bulk, webhook, all strategies)
    "ltp"     — quote/LTP polling
    "candle"  — historical/intraday candle fetch
    "account" — funds() / margin-calculator calls
"""

import os
import sqlite3
import time
from pathlib import Path

_DB_FILE = Path(__file__).resolve().parent / "data" / "dhan_rate_limiter.db"
_DB_FILE.parent.mkdir(exist_ok=True)

# Account-wide cap (Dhan's real limit is ~1 req/sec sustained; default leaves
# a little burst headroom). Override via env if Dhan support confirms a
# higher number for this account.
DEFAULT_CAP_PER_SEC  = int(os.environ.get("DHAN_RATE_LIMIT_PER_SEC", "3"))
RESERVE_FOR_ORDER     = 1     # slots/sec carved out exclusively for "order" priority
COOLDOWN_SECONDS      = 8.0   # after a 429, shrink the cap for this long
COOLDOWN_CAP_PER_SEC  = 1     # cap during cooldown (orders only — non-order cap becomes 0)


def _connect():
    conn = sqlite3.connect(str(_DB_FILE), timeout=5, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE IF NOT EXISTS windows (epoch INTEGER PRIMARY KEY, count INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY, until REAL)")
    return conn


def _effective_cap(priority: str, now: float, cooldown_until: float) -> int:
    base = COOLDOWN_CAP_PER_SEC if now < cooldown_until else DEFAULT_CAP_PER_SEC
    if priority == "order":
        return base
    return max(0, base - RESERVE_FOR_ORDER)


def _try_take(priority: str) -> bool:
    now = time.time()
    epoch = int(now)
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
        cooldown_until = row[0] if row else 0.0

        cap = _effective_cap(priority, now, cooldown_until)
        row = conn.execute("SELECT count FROM windows WHERE epoch=?", (epoch,)).fetchone()
        count = row[0] if row else 0

        if count >= cap:
            conn.execute("ROLLBACK")
            return False

        if row:
            conn.execute("UPDATE windows SET count = count + 1 WHERE epoch=?", (epoch,))
        else:
            conn.execute("INSERT INTO windows(epoch, count) VALUES (?, 1)", (epoch,))
        # keep the table tiny — drop windows older than 10s
        conn.execute("DELETE FROM windows WHERE epoch < ?", (epoch - 10,))
        conn.execute("COMMIT")
        return True
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        return False
    finally:
        conn.close()


def acquire(priority: str = "ltp", timeout: float = 8.0) -> bool:
    """Block (briefly) until a Dhan call slot is free for this priority.

    Returns True once a slot was taken. Returns False only if `timeout`
    elapsed first — caller should treat that like any other transient Dhan
    failure (skip / use stale cache / log), never as "go ahead anyway"."""
    deadline = time.time() + timeout
    poll = 0.05 if priority == "order" else 0.12
    while True:
        if _try_take(priority):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll)


def note_429(cooldown_seconds: float = COOLDOWN_SECONDS) -> None:
    """Call this right after seeing a 429/DH-904 from Dhan. Shrinks the
    account-wide cap for everyone (all processes) for the cooldown window,
    with non-order traffic shut out entirely so orders keep flowing while
    the account recovers."""
    conn = _connect()
    try:
        until = time.time() + cooldown_seconds
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT until FROM cooldown WHERE id=1").fetchone()
        new_until = max(until, row[0]) if row else until
        if row:
            conn.execute("UPDATE cooldown SET until=? WHERE id=1", (new_until,))
        else:
            conn.execute("INSERT INTO cooldown(id, until) VALUES (1, ?)", (new_until,))
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
    finally:
        conn.close()


def throttled_post(url, priority: str = "ltp", timeout: float = 8.0,
                    max_retries: int = 2, retry_backoff: float = 1.5, **kwargs):
    """requests.post wrapped with the priority gate + 429 feedback loop.
    `timeout` here is the gate-wait budget, not the HTTP timeout (pass that
    inside kwargs as usual, e.g. timeout=10)."""
    import requests
    r = None
    for attempt in range(max_retries + 1):
        acquire(priority, timeout=timeout)  # best-effort wait; proceed even if it timed out
        r = requests.post(url, **kwargs)
        if r.status_code == 429:
            note_429()
            if attempt < max_retries:
                time.sleep(retry_backoff * (attempt + 1))
                continue
        return r
    return r
