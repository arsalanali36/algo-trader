"""Delta Exchange India — read-only market-data client (Phase 2, zero-auth).
Public endpoints only: products, candles, tickers. No credentials.
"""
import requests, time, datetime as dt

BASE = "https://api.india.delta.exchange"
_S = requests.Session(); _S.headers.update({"Accept": "application/json"})

def _get(path, params=None, retry=3):
    for i in range(retry):
        try:
            r = _S.get(BASE + path, params=params, timeout=25)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(1.0 + i); continue
            return None
        except requests.RequestException:
            time.sleep(0.5 + i)
    return None

def candles(symbol, resolution, start, end):
    """OHLC list (newest-first). start/end = unix seconds."""
    j = _get("/v2/history/candles",
             {"symbol": symbol, "resolution": resolution, "start": int(start), "end": int(end)})
    return (j or {}).get("result", []) if j else []

def opt_symbol(cp, underlying, strike, expiry_date):
    """cp='C'|'P', expiry_date=datetime.date -> e.g. C-BTC-76000-230826 (DDMMYY)."""
    s = int(strike) if float(strike) == int(strike) else strike
    return f"{cp}-{underlying}-{s}-{expiry_date.strftime('%d%m%y')}"

def spot_at(symbol, ts, res="5m", window=3600):
    """Spot/underlying price nearest to unix ts. symbol e.g. BTCUSD."""
    c = candles(symbol, res, ts - window, ts + window)
    if not c: return None
    best = min(c, key=lambda x: abs(x["time"] - ts))
    return best["close"]
