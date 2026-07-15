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

_FILE = Path(__file__).resolve().parent.parent / "data" / "shared_ltp_cache.json"
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


# Index spot sec_ids — same set ltp_poller._IDX_ALWAYS keeps warm every cycle
# (risk_gate ITM checks, webhook index-trailing, strike resolution read these too).
_INDEX_SEC = {"NIFTY": "13", "BANKNIFTY": "25", "BANKNIFTY_": "25"}


def get_index(symbol, max_age=60.0):
    """Cached index spot for NIFTY/BANKNIFTY, read from the poller-warmed cache.

    ltp_poller writes index spot under its sec_id key ("13"/"25") each cycle, so
    this serves a spot with ZERO extra Dhan calls. max_age is deliberately loose
    (60s): under rate-limit contention the poller's own writes can lag 20-30s,
    and the callers (positional VRP / short-vol) only need a roughly-current
    index level, not a live tick — a slightly-stale cached spot is far better
    than falling through and hammering the rate-limiter. Returns None only if the
    symbol is unknown or the poller has genuinely gone silent (>60s), in which
    case the caller's direct-call fallback takes over. (Was missing entirely, so
    every fetch_spot() cache-first read hit AttributeError and fell through to
    the rate-limiter, spamming "no spot".)"""
    sec = _INDEX_SEC.get(str(symbol).upper())
    if not sec:
        return None
    return get(sec, max_age=max_age)


def put(sec_id, ltp):
    """Record a freshly-fetched ltp so other processes can reuse it."""
    if not ltp:
        return
    put_many({sec_id: ltp})


def put_many(ltp_by_sec_id):
    """Batch write — one file rewrite for a whole poller cycle's results
    (ltp_poller feeds every open position + index spot through here)."""
    if not ltp_by_sec_id:
        return
    data = _read_all()
    now = time.time()
    for k, v in ltp_by_sec_id.items():
        if v:
            data[str(k)] = (v, now)
    # keep the file small — drop anything older than 5 minutes
    cutoff = now - 300
    data = {k: v for k, v in data.items() if v[1] >= cutoff}
    try:
        _FILE.write_text(json.dumps(data))
    except Exception:
        pass
