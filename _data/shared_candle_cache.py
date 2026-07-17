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

_FILE = Path(__file__).resolve().parent.parent / "data" / "shared_candle_cache.json"
_FILE.parent.mkdir(exist_ok=True)


def _key(sec_id, interval, days):
    """`days` is part of the identity, not a detail.

    It used to be `sec_id:interval` — but the rows behind that key are NOT
    interchangeable: every producer asks Dhan for a different window. On
    NIFTY 5m alone, chainzone_v1 stores 10 days (its ATR warm-up), straddle_v1
    and backspread_v1 store 5, and range_v1 wants today only. Same key, four
    different meanings — so whoever wrote last decided what everyone else read,
    and it flipped every 20s (the TTL).

    That silently fed range_v1 chainzone_v1's 10-day window. Its engine has no
    date awareness, so its 2-trade cap applied to the whole 10 days instead of
    to today — the cap was spent days ago, and every real signal since was
    dropped. Observed live 2026-07-17: TV entered SHORT at 10:05, range_v1's
    engine had the identical signal and never acted on it.

    Same window still shares (that's the whole point of this cache, TRAP #2) —
    straddle_v1 and backspread_v1 both ask 5 days and still get one fetch
    between them. Different windows now simply cannot collide.
    """
    return f"{sec_id}:{interval}:{days}d"


def _read_all():
    try:
        return json.loads(_FILE.read_text())
    except Exception:
        return {}


def get(sec_id, interval, days, max_age=20.0):
    """Return cached candle rows (list of dicts) for sec_id+interval+days if
    fresher than max_age seconds — OR still bar-aligned-valid: a fetch made
    after the current bar opened contains every CLOSED bar there is (a 5-min
    candle set genuinely can't gain a new closed bar until the next 5-min
    boundary), so serving it is lossless for closed-bar signal logic and cuts
    per-loop refetches massively on the wider timeframes. The +3s grace after
    the boundary covers Dhan's own delay in publishing the just-closed bar."""
    data = _read_all()
    entry = data.get(_key(sec_id, interval, days))
    if not entry:
        return None
    rows, ts = entry
    now = time.time()
    if now - ts <= max_age:
        return rows
    try:
        bar_s = int(str(interval)) * 60
        boundary = (now // bar_s) * bar_s   # IST offset is 330 min — whole minutes, so 1/5/15/30m boundaries align in epoch too
        if ts >= boundary + 3.0:
            return rows
    except (ValueError, TypeError):
        pass
    return None


def put(sec_id, interval, days, rows):
    """Record freshly-fetched candle rows so other processes/loops reuse them.
    `days` = the window you asked Dhan for (0 = today only). It must describe
    the rows you are actually storing — see _key().
    `rows` must be JSON-serializable (e.g. df.to_dict('records') with time
    columns already converted to ISO strings)."""
    if not rows:
        return
    data = _read_all()
    data[_key(sec_id, interval, days)] = (rows, time.time())
    # keep the file small — drop anything older than 5 minutes
    cutoff = time.time() - 300
    data = {k: v for k, v in data.items() if v[1] >= cutoff}
    try:
        _FILE.write_text(json.dumps(data))
    except Exception:
        pass
