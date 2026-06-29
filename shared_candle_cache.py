"""
shared_candle_cache.py — cross-PROCESS intraday-candle cache (file-backed),
same pattern as shared_ltp_cache.py but for /v2/charts/intraday.

Problem (LESSONS.md TRAP #2 root cause, second half): range_trader.py AND
rsi_trader.py each independently re-fetch the FULL day's candles for EVERY
symbol on EVERY loop (every 60s) — and when both strategies trade the same
underlying (e.g. SBIN), that's 2x the Dhan calls for data that's identical
within the same few seconds. dhan_rate_limiter's account-wide cap then queues
or 429s these duplicate calls — the cap was never the real problem, the
duplicate fetching was.

Fix: same symbol+interval, fetched again within TTL seconds, returns the
cached DataFrame (as records) instead of hitting Dhan again. A 1-minute
candle genuinely doesn't change more than once a minute, so a short TTL
(default 20s) loses nothing real while collapsing "N processes x M symbols"
candle calls into ~1 per symbol per TTL window — same effect shared_ltp_cache
already proved out for LTP.
"""

import json
import time
from pathlib import Path

_FILE = Path(__file__).resolve().parent / "data" / "shared_candle_cache.json"
_FILE.parent.mkdir(exist_ok=True)


def _key(sec_id, interval):
    return f"{sec_id}:{interval}"


def _read_all():
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return {}


def get(sec_id, interval, max_age=20.0):
    """Return cached candle rows (list of dicts) for sec_id+interval if
    fresher than max_age seconds, else None."""
    data = _read_all()
    entry = data.get(_key(sec_id, interval))
    if not entry:
        return None
    rows, ts = entry
    if time.time() - ts > max_age:
        return None
    return rows


def put(sec_id, interval, rows):
    """Record freshly-fetched candle rows so other processes/loops reuse them.
    `rows` must be JSON-serializable (e.g. df.to_dict('records') with time
    columns already converted to ISO strings)."""
    if not rows:
        return
    data = _read_all()
    data[_key(sec_id, interval)] = (rows, time.time())
    # keep the file small — drop anything older than 5 minutes
    cutoff = time.time() - 300
    data = {k: v for k, v in data.items() if v[1] >= cutoff}
    try:
        _FILE.write_text(json.dumps(data))
    except Exception:
        pass
