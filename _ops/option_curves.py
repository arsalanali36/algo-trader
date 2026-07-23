"""
option_curves.py — Sensibull-style intraday option curves from the on-disk
option-chain snapshots.

SOURCE: `_TRADING_DATA/OptionChain/<U>/<U>_<date>.csv`, written every minute by
`_ops/option_chain_collector.py` — per strike: ltp/oi/iv + REAL Dhan greeks
(delta/theta/gamma/vega) + spot + India-VIX.

WHAT IT COMPUTES (per minute, for a chosen expiry):
  - ATM straddle premium = ATM CE ltp + ATM PE ltp  (the credit you'd collect if
    you SELL the ATM call + put — Sensibull's "Auto ATM Straddle"; ATM is re-picked
    each minute as spot moves).
  - ATM straddle gamma = ATM CE gamma + ATM PE gamma  (real greeks — to spot the
    gamma spike Sensibull doesn't show).
  - spot, VIX, PCR (total PE OI / total CE OI for the expiry).

DISPLAY-ONLY: reads CSV, touches NO order/live path. mtime-cached.
"""
import os
import csv
import calendar
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)  # _ops/ -> project root


def _lake_dirs(u):
    # same resolution as the collector: parent "._TRADING DATA" (local) or the
    # in-project _TRADING_DATA (VPS).
    return [
        os.path.join(os.path.dirname(PROJECT), "._TRADING DATA", "OptionChain", u),
        os.path.join(PROJECT, "_TRADING_DATA", "OptionChain", u),
    ]


def _csv_path(u, date):
    for d in _lake_dirs(u):
        p = os.path.join(d, f"{u}_{date}.csv")
        if os.path.exists(p):
            return p
    return None


_CACHE = {}  # (u, date) -> (mtime, rows)


def _load_rows(u, date):
    p = _csv_path(u, date)
    if not p:
        return None, []
    mt = os.path.getmtime(p)
    key = (u, date)
    hit = _CACHE.get(key)
    if hit and hit[0] == mt:
        return p, hit[1]
    rows = []
    try:
        with open(p, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append(r)
    except Exception:
        return p, []
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[key] = (mt, rows)
    return p, rows


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _epoch_ist(dtstr):
    # "2026-07-23 10:29:54" is IST wall-clock. Encode it as an as-if-UTC epoch so a
    # lightweight-charts UTC axis prints the IST time verbatim (same convention as
    # trade_chart.html). No timezone shift is applied on purpose.
    try:
        t = datetime.strptime(dtstr, "%Y-%m-%d %H:%M:%S")
        return calendar.timegm(t.timetuple())
    except Exception:
        return None


def curves(u, date, expiry=None):
    """Return {ok, underlying, expiry, expiries[], points[]} for one expiry's day."""
    _, rows = _load_rows(u, date)
    if not rows:
        return {"ok": False, "underlying": u, "expiry": expiry, "expiries": [], "points": []}

    exps = []
    for r in rows:
        e = r.get("expiry")
        if e and e not in exps:
            exps.append(e)
    exps = sorted(exps)
    if expiry not in exps:
        expiry = exps[0] if exps else None

    # group the chosen expiry's rows by timestamp
    bydt = {}
    for r in rows:
        if r.get("expiry") != expiry:
            continue
        bydt.setdefault(r.get("datetime"), []).append(r)

    points = []
    for dt in sorted(k for k in bydt.keys() if k):
        legs = bydt[dt]
        spot = _f(legs[0].get("spot"))
        vix = _f(legs[0].get("vix"))
        if spot is None:
            continue
        strikes = sorted({_f(l.get("strike")) for l in legs if _f(l.get("strike")) is not None})
        if not strikes:
            continue
        atm = min(strikes, key=lambda k: abs(k - spot))
        ce = next((l for l in legs if _f(l.get("strike")) == atm and l.get("opt_type") == "CE"), None)
        pe = next((l for l in legs if _f(l.get("strike")) == atm and l.get("opt_type") == "PE"), None)
        if not ce or not pe:
            continue
        ce_ltp, pe_ltp = _f(ce.get("ltp")), _f(pe.get("ltp"))
        if ce_ltp is None or pe_ltp is None:
            continue
        ce_g, pe_g = _f(ce.get("gamma")) or 0.0, _f(pe.get("gamma")) or 0.0
        ce_oi = sum(_f(l.get("oi")) or 0.0 for l in legs if l.get("opt_type") == "CE")
        pe_oi = sum(_f(l.get("oi")) or 0.0 for l in legs if l.get("opt_type") == "PE")
        ep = _epoch_ist(dt)
        if ep is None:
            continue
        points.append({
            "t": ep,
            "spot": round(spot, 2),
            "atm": atm,
            "ce": round(ce_ltp, 2),
            "pe": round(pe_ltp, 2),
            "straddle": round(ce_ltp + pe_ltp, 2),
            "gamma": round(ce_g + pe_g, 6),
            "vix": round(vix, 2) if vix is not None else None,
            "pcr": round(pe_oi / ce_oi, 3) if ce_oi else None,
        })

    return {"ok": True, "underlying": u, "expiry": expiry, "expiries": exps, "points": points}
