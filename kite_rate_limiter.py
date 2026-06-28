"""
kite_rate_limiter.py — cross-process throttle + priority gate for every
Zerodha Kite Connect API call (orders, margins), same pattern as
dhan_rate_limiter.py but with Kite's own (separate, stricter) limits and its
own sqlite file — Dhan and Kite quotas are independent accounts/APIs and must
never share one gate.

Kite Connect's documented limits are roughly 10 req/sec for order placement
and much lower for other endpoints; we deliberately stay conservative (3/sec
default, with a slice reserved for "order" priority) since this account also
shares CPU/network with the Dhan-data side on the same box.

Usage:
    import kite_rate_limiter as krl
    krl.acquire("order")
    ... call kite.place_order(...) ...
    # KiteConnect raises kiteconnect.exceptions.NetworkException / a 429-like
    # error on throttling — call krl.note_429() from the except block.
"""

import os
import sqlite3
import time
from pathlib import Path

_DB_FILE = Path(__file__).resolve().parent / "data" / "kite_rate_limiter.db"
_DB_FILE.parent.mkdir(exist_ok=True)

DEFAULT_CAP_PER_SEC = int(os.environ.get("KITE_RATE_LIMIT_PER_SEC", "3"))
RESERVE_FOR_ORDER    = 1
COOLDOWN_SECONDS     = 8.0
COOLDOWN_CAP_PER_SEC = 1


def _connect():
    conn = sqlite3.connect(str(_DB_FILE), timeout=5, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("CREATE TABLE IF NOT EXISTS windows (epoch INTEGER PRIMARY KEY, count INTEGER)")
    conn.execute("CREATE TABLE IF NOT EXISTS cooldown (id INTEGER PRIMARY KEY, until REAL)")
    return conn


def _effective_cap(priority, now, cooldown_until):
    base = COOLDOWN_CAP_PER_SEC if now < cooldown_until else DEFAULT_CAP_PER_SEC
    if priority == "order":
        return base
    return max(0, base - RESERVE_FOR_ORDER)


def _try_take(priority):
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


def acquire(priority="order", timeout=8.0):
    deadline = time.time() + timeout
    poll = 0.05 if priority == "order" else 0.12
    while True:
        if _try_take(priority):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(poll)


def note_429(cooldown_seconds=COOLDOWN_SECONDS):
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
