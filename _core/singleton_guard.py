"""singleton_guard.py — OS-level "one process per strategy --id" lock.

Recurring money-path bug (EOD report 2026-07-13): two copies of the SAME strategy
ran at once (scheduler re-run / restart churn while an old process was still alive).
Each copy keeps its OWN in-memory position state (`pos=None`), so BOTH enter on the
same signal → duplicate live orders + inflated trade count (orbst_v1 12:45 ×2,
dvert_v1 10:30 ×2, both proven via 4s-offset doubled heartbeats in the live log).

The dashboard's `get_pid()` dedup is a best-effort check with a TOCTOU race between
two near-simultaneous starts. This closes the gap at the OS level: the FIRST live
process for an id holds an exclusive `flock`; any later one fails to acquire it and
should exit immediately. The lock is tied to the holding process's file descriptor,
so the kernel releases it automatically when that process dies — a legitimate restart
(old killed → new started) just works, no manual cleanup, no stale-lock trap.

Fail-OPEN by design on every path EXCEPT a genuine "another process holds it": if the
lock dir/file/fcntl is unavailable (e.g. local Windows dev, no fcntl), the guard is a
no-op and trading proceeds — it never falsely blocks a lone legitimate process. It
only ever returns False when another live process actually holds the lock.
"""
import os
from pathlib import Path

_LOCK_DIR = Path(__file__).resolve().parent.parent / "data" / "locks"
_held_fds = {}   # strategy_id -> fd, kept open for the process lifetime (releases on exit)


def acquire_singleton(strategy_id):
    """Try to become the sole live process for `strategy_id`.

    Returns True  → this process now holds the lock (safe to run), or the guard is
                    unavailable on this platform (fail-open, lone-process safe).
    Returns False → another live process already holds it → caller MUST exit to
                    avoid duplicate orders.
    """
    try:
        import fcntl
    except Exception:
        return True   # non-POSIX (local Windows dev) — no guard, fail open
    try:
        _LOCK_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = _LOCK_DIR / f"{strategy_id}.lock"
        fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    except Exception:
        return True   # can't set up lock file — don't block trading, fail open
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        try:
            os.close(fd)
        except Exception:
            pass
        return False  # another live process holds the lock
    # won the lock — stamp our pid (informational) and keep the fd open for life
    try:
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
    except Exception:
        pass
    _held_fds[strategy_id] = fd
    return True
