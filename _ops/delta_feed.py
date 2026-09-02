"""
delta_feed.py — Delta Exchange India crypto data feed for the /crypto page.

DISPLAY-ONLY. Zero order path, zero Dhan/Kite touch, credential-free (public API).
Builds: live BTC/ETH spot, full option chain (one /v2/tickers call, not per-strike),
available expiries, and the validated daily Iron-Fly setup (ATM straddle + wings)
with live premiums — the same structure Phase-2 backtest validated (H=12h, defined-risk).

Reused by trader_dashboard's /crypto + /api/delta-chain routes.
"""
import os
import sys
import time
import datetime as dt
import requests

# --- CODE3B path bootstrap (safe if already set) -----------------------------
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

BASE = "https://api.india.delta.exchange"
_S = requests.Session()
_S.headers.update({"Accept": "application/json", "User-Agent": "khazana-delta/1.0"})

# strike step per underlying (near-ATM; Delta lists finer far out)
_STEP = {"BTC": 500, "ETH": 20}
_LOT = {"BTC": 0.001, "ETH": 0.01}   # contract_value

_cache = {}   # key -> (ts, payload)
_TTL = 20.0   # seconds


def _get(path, params=None):
    try:
        r = _S.get(BASE + path, params=params, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _cached(key, builder):
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _TTL:
        return hit[1]
    val = builder()
    if val is not None:
        _cache[key] = (now, val)
    return val


def spot(underlying="BTC"):
    """Live underlying spot (perpetual mark)."""
    def _b():
        j = _get(f"/v2/tickers/{underlying}USD")
        t = (j or {}).get("result") if j else None
        if not t:
            return None
        return _f(t.get("mark_price")) or _f(t.get("close")) or _f(t.get("spot_price"))
    return _cached(f"spot:{underlying}", _b)


def _all_option_tickers(underlying):
    """One call: every live option ticker for the underlying (quotes+greeks+oi)."""
    j = _get("/v2/tickers", {"contract_types": "call_options,put_options"})
    res = (j or {}).get("result", []) if j else []
    pre_c, pre_p = f"C-{underlying}-", f"P-{underlying}-"
    out = []
    for t in res:
        s = t.get("symbol", "")
        if s.startswith(pre_c) or s.startswith(pre_p):
            out.append(t)
    return out


def expiries(underlying="BTC"):
    """Distinct expiry dates (from live option symbols), soonest first."""
    def _b():
        seen = {}
        for t in _all_option_tickers(underlying):
            parts = t.get("symbol", "").split("-")
            if len(parts) != 4:
                continue
            code = parts[3]                      # DDMMYY
            try:
                d = dt.datetime.strptime(code, "%d%m%y").date()
            except ValueError:
                continue
            seen[code] = d
        rows = sorted(seen.items(), key=lambda kv: kv[1])
        today = dt.date.today()
        out = []
        for code, d in rows:
            dte = (d - today).days
            out.append({"code": code, "date": d.isoformat(),
                        "label": d.strftime("%d %b"), "dte": dte,
                        "kind": "weekly" if d.weekday() == 4 else "daily"})
        return out
    return _cached(f"exp:{underlying}", _b)


def chain(underlying="BTC", expiry_code=None, n=8):
    """Option chain for one expiry: ATM +/- n strikes, CE & PE with live data."""
    def _b():
        sp = spot(underlying)
        exps = expiries(underlying)
        if not exps:
            return None
        code = expiry_code or exps[0]["code"]
        step = _STEP.get(underlying, 500)
        atm = round(sp / step) * step if sp else None

        # index tickers by (cp, strike)
        by = {}
        exp_meta = None
        for t in _all_option_tickers(underlying):
            parts = t.get("symbol", "").split("-")
            if len(parts) != 4 or parts[3] != code:
                continue
            cp, k = parts[0], parts[1]
            try:
                strike = int(parts[2])
            except ValueError:
                continue
            q = t.get("quotes") or {}
            g = t.get("greeks") or {}
            by[(cp, strike)] = {
                "ltp": _f(t.get("mark_price")), "bid": _f(q.get("best_bid")),
                "ask": _f(q.get("best_ask")), "iv": _f(q.get("mark_iv")),
                "oi": _f(t.get("oi")), "vol": _f(t.get("volume")),
                "delta": _f(g.get("delta")), "theta": _f(g.get("theta")),
                "gamma": _f(g.get("gamma")), "vega": _f(g.get("vega")),
                "symbol": t.get("symbol"),
            }
            exp_meta = code

        # build ladder from strikes that ACTUALLY exist for this expiry
        # (daily options list coarser strikes than weeklies) — no empty rows
        avail = sorted({k for (_cp, k), v in by.items()
                        if v.get("ltp") is not None})
        rows = []
        if avail:
            # nearest available strike to ATM = display centre
            centre = min(avail, key=lambda k: abs(k - (atm or avail[len(avail) // 2])))
            ci = avail.index(centre)
            lo, hi = max(0, ci - n), min(len(avail), ci + n + 1)
            for k in reversed(avail[lo:hi]):
                rows.append({"strike": k, "atm": (k == atm),
                             "ce": by.get(("C", k)), "pe": by.get(("P", k))})
        exp_lbl = next((e for e in exps if e["code"] == code), None)
        return {"underlying": underlying, "spot": sp, "atm": atm, "step": step,
                "expiry": code, "expiry_date": (exp_lbl or {}).get("date"),
                "expiry_label": (exp_lbl or {}).get("label"),
                "dte": (exp_lbl or {}).get("dte"),
                "rows": rows, "expiries": exps,
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    return _cached(f"chain:{underlying}:{expiry_code}:{n}", _b)


def ironfly_setup(underlying="BTC", expiry_code=None, wing=None):
    """Validated daily Iron-Fly: SELL ATM CE+PE, BUY OTM wings (defined risk).
    Returns legs with live premiums + net credit + max loss/profit + breakevens.
    DISPLAY-ONLY context (Phase-2 winner: H=12h before 12:00 UTC expiry)."""
    def _b():
        c = chain(underlying, expiry_code, n=12)
        if not c or not c.get("atm"):
            return None
        atm, step, sp = c["atm"], c["step"], c["spot"]
        wing_off = wing if wing is not None else (2000 if underlying == "BTC" else 60)
        lot = _LOT.get(underlying, 0.001)

        def _leg(cp, strike, side):
            for r in c["rows"]:
                if r["strike"] == strike:
                    d = r["ce"] if cp == "C" else r["pe"]
                    if d:
                        return {"cp": cp, "strike": strike, "side": side,
                                "premium": d.get("ltp"), "iv": d.get("iv"),
                                "symbol": d.get("symbol")}
            return {"cp": cp, "strike": strike, "side": side,
                    "premium": None, "iv": None, "symbol": None}

        legs = [_leg("C", atm, "SELL"), _leg("P", atm, "SELL"),
                _leg("C", atm + wing_off, "BUY"), _leg("P", atm - wing_off, "BUY")]
        prem = [l["premium"] for l in legs]
        credit = max_loss = max_profit = be_up = be_dn = None
        if all(p is not None for p in prem):
            net = (prem[0] + prem[1]) - (prem[2] + prem[3])   # points (per-BTC)
            credit = net
            max_profit = net
            max_loss = wing_off - net       # defined-risk cap per side
            be_up = atm + net
            be_dn = atm - net
        return {"underlying": underlying, "atm": atm, "spot": sp,
                "wing": wing_off, "lot": lot, "expiry": c["expiry"],
                "expiry_label": c["expiry_label"], "dte": c["dte"],
                "legs": legs, "net_credit_pts": credit,
                "max_profit_pts": max_profit, "max_loss_pts": max_loss,
                "breakeven_up": be_up, "breakeven_dn": be_dn,
                "note": "Phase-2 validated: enter ~12h before 12:00 UTC (05:30 IST) "
                        "expiry, hold to cash-settlement. Defined-risk, PAPER first.",
                "generated_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    return _cached(f"fly:{underlying}:{expiry_code}:{wing}", _b)


def candles(underlying="BTC", resolution="5m", n=120):
    """Recent OHLC candles for the perpetual (spot proxy) — `/v2/history/candles`.
    Returns oldest→newest list of {time(epoch s), open, high, low, close} for the
    last `n` bars; [] on any failure. Cached (_TTL) like everything else here.
    Used by level_slots (BTC index-level slots) — same shape as the Dhan-bucketed
    bars trader_dashboard._tf_bars returns, so the state machine is source-agnostic."""
    res_s = {"1m": 60, "3m": 180, "5m": 300, "15m": 900}.get(resolution, 300)

    def _b():
        end = int(time.time())
        j = _get("/v2/history/candles", {"resolution": resolution,
                                        "symbol": f"{underlying}USD",
                                        "start": end - res_s * (n + 2), "end": end})
        rows = (j or {}).get("result") if j else None
        if not rows:
            return None
        out = []
        for r in rows:
            try:
                out.append({"time": int(r["time"]), "open": float(r["open"]),
                            "high": float(r["high"]), "low": float(r["low"]),
                            "close": float(r["close"])})
            except (KeyError, TypeError, ValueError):
                continue
        out.sort(key=lambda x: x["time"])
        return out[-n:]
    return _cached(f"candles:{underlying}:{resolution}:{n}", _b) or []


if __name__ == "__main__":
    import json
    print("spot BTC:", spot("BTC"))
    exps = expiries("BTC")
    print("expiries:", [(e["label"], e["kind"], e["dte"]) for e in exps[:6]])
    c = chain("BTC", n=4)
    print("chain atm:", c["atm"], "rows:", len(c["rows"]))
    for r in c["rows"]:
        ce, pe = r["ce"] or {}, r["pe"] or {}
        print(f"  {r['strike']:>7}{'*' if r['atm'] else ' '} "
              f"CE {ce.get('ltp')} (iv {ce.get('iv')})  |  PE {pe.get('ltp')}")
    print("\nironfly:", json.dumps(ironfly_setup("BTC"), indent=1, default=str)[:900])
