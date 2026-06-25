"""
shared_ltp_cache.py — cross-PROCESS LTP cache (file-backed, not just in-memory).

Problem: every strategy process (range_trader.py, rsi_trader.py, the dashboard's
webhook handler, universe_trader.py, ...) creates its own DhanBroker and hits
Dhan's /v2/marketfeed/ltp independently. Dhan's real limit is ~1 req/sec for
the WHOLE account, not per-process — so as soon as 2+ processes poll the same
or different symbols around the same time, everyone starts seeing 429 (DH-904),
including fresh webhook entries that only needed one quick quote.

Fix: ALL processes read/write the same small JSON file. Whoever successfully
fetches a symbol's LTP shares it with every other process for the next few
seconds, instead of each process being forced to make its own independent
Dhan call. This turns "N processes x M symbols" Dhan calls into roughly
"1 call per symbol per TTL window", regardless of how many strategies run.

Not a perfect distributed lock (plain read-modify-write, last-writer-wins on
the rare concurrent-write race) — fine for a price cache where staleness of a
few seconds is already acceptable and an occasional dropped write just means
one process re-fetches.
"""

import json
import time
from pathlib import Path

_FILE = Path(__file__).resolve().parent / "data" / "shared_ltp_cache.json"
_FILE.parent.mkdir(exist_ok=True)


def _read_all():
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return {}


def get(sec_id, max_age=3.0):
    """Return cached ltp for sec_id if fresher than max_age seconds, else None."""
    data = _read_all()
    entry = data.get(str(sec_id))
    if not entry:
        return None
    ltp, ts = entry
    if time.time() - ts > max_age:
        return None
    return ltp


def get_stale(sec_id, max_age=15.0):
    """Wider-tolerance read for last-resort fallback when a live call just failed."""
    return get(sec_id, max_age=max_age)


def put(sec_id, ltp):
    """Record a freshly-fetched ltp so other processes can reuse it."""
    if not ltp:
        return
    data = _read_all()
    data[str(sec_id)] = (ltp, time.time())
    # keep the file small — drop anything older than 5 minutes
    cutoff = time.time() - 300
    data = {k: v for k, v in data.items() if v[1] >= cutoff}
    try:
        _FILE.write_text(json.dumps(data))
    except Exception:
        pass
