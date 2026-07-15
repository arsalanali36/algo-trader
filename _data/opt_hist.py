"""_data/opt_hist.py — shared Dhan `rollingoption` (paid Expired-Options add-on) fetcher.

SINGLE source for the expired-option premium call/parse (Rule 6B). Used by BOTH:
  • scratch/nifty_trend/optchain_dl.py   — builds the historical OptChainLake
  • _ops/backfill_trade_ohlc.py          — fills a specific traded contract's bars

Endpoint: POST /v2/charts/rollingoption. Per call returns, for ONE relative strike
(rolling ATM±off) + ONE side, a 5-min series with the ACTUAL strike + spot per bar —
so a fixed held-strike series is reconstructed by fetching a few offsets and keeping
the rows whose `strike` == the target strike.

Response shape: {"data": {"ce": {timestamp,open,high,low,close,volume,oi,strike,spot,iv},
                          "pe": {...}}}  (both sides in one call; we read the asked side).
"""
import time
import requests

URL = "https://api.dhan.co/v2/charts/rollingoption"

# underlying -> (dhan securityId, instrument). Index options only for these;
# anything else is treated as a stock (OPTSTK) with its NSE_EQ securityId.
INDEX_UNDERLYINGS = {
    "NIFTY": (13, "OPTIDX"),
    "BANKNIFTY": (25, "OPTIDX"),
    "FINNIFTY": (27, "OPTIDX"),
    "MIDCPNIFTY": (442, "OPTIDX"),
    "SENSEX": (51, "OPTIDX"),
}


def strike_label(off):
    """offset int -> Dhan 'strike' param ('ATM', 'ATM+1', 'ATM-2', ...)."""
    return "ATM" if off == 0 else ("ATM+%d" % off if off > 0 else "ATM%d" % off)


def series_slug(off):
    """filesystem-safe offset slug for lake filenames ('ATM','ATMp1','ATMm2')."""
    return strike_label(off).replace("+", "p").replace("-", "m")


def fetch_rolling(headers, sec_id, instrument, flag, off, dtype, side,
                  frm, to, interval="5", timeout=30, rl=None):
    """One rollingoption call.

    Returns (rows, status). rows = list of tuples in CSV/lake column order:
        (ts:int, o, h, l, c: float, volume, iv, oi, strike: float|None, spot: float|None)
    status = HTTP code (200 ok; 429 rate-limited; -1 network exception).
    side='ce'|'pe'; dtype='CALL'|'PUT'; off=int; flag='WEEK'|'MONTH'.
    `rl` = optional dhan_rate_limiter module (acquire 'account' before the call).
    """
    if rl is not None:
        try:
            for _ in range(120):
                if rl.acquire("account"):
                    break
                time.sleep(1.0)
        except Exception:
            pass
    body = {
        "exchangeSegment": "NSE_FNO", "securityId": int(sec_id),
        "instrument": instrument, "interval": str(interval),
        "expiryFlag": flag, "expiryCode": 1, "strike": strike_label(off),
        "drvOptionType": dtype,
        "requiredData": ["open", "high", "low", "close", "volume", "oi", "strike", "spot"],
        "fromDate": str(frm), "toDate": str(to),
    }
    try:
        r = requests.post(URL, json=body, headers=headers, timeout=timeout)
    except Exception as e:
        if rl is not None and "429" in str(e):
            try:
                rl.note_429()
            except Exception:
                pass
        return [], -1
    if r.status_code == 429:
        if rl is not None:
            try:
                rl.note_429()
            except Exception:
                pass
        return [], 429
    if r.status_code != 200:
        return [], r.status_code
    try:
        s = (r.json().get("data") or {}).get(side) or {}
    except Exception:
        return [], r.status_code
    close = s.get("close") or []
    n = len(close)
    if not n:
        return [], 200
    ts = s.get("timestamp", []) or []
    o = s.get("open", []) or []
    h = s.get("high", []) or []
    lo = s.get("low", []) or []
    vol = s.get("volume", []) or [0] * n
    iv = s.get("iv", []) or [""] * n
    oi = s.get("oi", []) or [""] * n
    st = s.get("strike", []) or [None] * n
    sp = s.get("spot", []) or [None] * n
    out = []
    for i in range(n):
        try:
            out.append((
                int(ts[i]), float(o[i]), float(h[i]), float(lo[i]), float(close[i]),
                (vol[i] if i < len(vol) else 0),
                (iv[i] if i < len(iv) else ""),
                (oi[i] if i < len(oi) else ""),
                (float(st[i]) if st[i] not in (None, "") else None),
                (float(sp[i]) if sp[i] not in (None, "") else None),
            ))
        except Exception:
            continue
    return out, 200


def held_strike_series(headers, sec_id, instrument, flag, side, dtype,
                       target_strike, date_str, off_range=8, rl=None,
                       interval="5", tol=0.5):
    """Reconstruct ONE fixed contract's intraday bars on `date_str`.

    Fetches offsets -off_range..+off_range for (flag, side) and keeps the bars
    whose per-bar `strike` == target_strike (±tol). Merges + dedups by timestamp.
    Returns {epoch_str: [o,h,l,c]} ready to write into data/trade_ohlc/.
    Stops early (outward from ATM) once the strike has been fully captured and a
    subsequent offset yields none — the strike has rolled out of view.
    """
    merged = {}
    tgt = round(float(target_strike))
    seen_any = False
    # walk outward from ATM: 0, +1, -1, +2, -2 ... so we hit the target fast
    offs = [0]
    for k in range(1, off_range + 1):
        offs += [k, -k]
    misses_after_hit = 0
    for off in offs:
        rows, status = fetch_rolling(headers, sec_id, instrument, flag, off, dtype,
                                     side, date_str, date_str, interval=interval,
                                     timeout=30, rl=rl)
        hit_this = 0
        for row in rows:
            ts, o, h, l, c, strike = row[0], row[1], row[2], row[3], row[4], row[8]
            if strike is None:
                continue
            if abs(round(strike) - tgt) <= tol:
                merged[str(int(ts))] = [round(o, 2), round(h, 2), round(l, 2), round(c, 2)]
                hit_this += 1
        if hit_this:
            seen_any = True
            misses_after_hit = 0
        elif seen_any:
            misses_after_hit += 1
            # once we've captured the strike, two consecutive empty offsets = done
            if misses_after_hit >= 2:
                break
    return merged
