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
import sys
import csv
import calendar
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)  # _ops/ -> project root

_bs_mod = None


def _bs():
    """Lazy-import the project's SINGLE Black-Scholes source (scratch/nifty_trend/
    bs_option.py — same one _core/payoff.py uses; Rule 6B, no second BS here).
    Returns None if unavailable → the theoretical-decay line just stays empty."""
    global _bs_mod
    if _bs_mod is not None:
        return _bs_mod or None
    d = os.path.join(PROJECT, "scratch", "nifty_trend")
    if os.path.isdir(d) and d not in sys.path:
        sys.path.insert(0, d)
    try:
        import bs_option as _m
        _bs_mod = _m
    except Exception:
        _bs_mod = False
    return _bs_mod or None


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
        # ATM straddle IV (avg CE/PE — percent, so it plots directly against VIX) and
        # net straddle delta (CE delta + PE delta; ~0 at ATM, drifts as spot moves).
        ce_iv, pe_iv = _f(ce.get("iv")), _f(pe.get("iv"))
        _ivs = [x for x in (ce_iv, pe_iv) if x is not None]
        atm_iv = round(sum(_ivs) / len(_ivs), 2) if _ivs else None
        ce_d, pe_d = _f(ce.get("delta")), _f(pe.get("delta"))
        net_delta = round(ce_d + pe_d, 4) if (ce_d is not None and pe_d is not None) else None
        # Max-OI strike (full captured chain, not just ATM): call wall vs put wall.
        ce_oi_by, pe_oi_by = {}, {}
        for l in legs:
            k = _f(l.get("strike"))
            if k is None:
                continue
            o = _f(l.get("oi")) or 0.0
            if l.get("opt_type") == "CE":
                ce_oi_by[k] = o
            elif l.get("opt_type") == "PE":
                pe_oi_by[k] = o
        call_wall = max(ce_oi_by, key=ce_oi_by.get) if ce_oi_by else None
        put_wall = max(pe_oi_by, key=pe_oi_by.get) if pe_oi_by else None
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
            "ce_oi": ce_oi,
            "pe_oi": pe_oi,
            "atm_iv": atm_iv,
            "net_delta": net_delta,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "straddle_theo": None,   # filled in the theoretical-decay pass below
        })

    # Theoretical decay reference: freeze the ATM IV at the day's FIRST reading and let
    # only time-to-expiry shrink (pure theta @ open-IV). actual straddle < theo = IV has
    # crushed / decay ahead of schedule = edge for the option seller. Reuses bs_option.
    bs = _bs()
    iv0 = next((p["atm_iv"] for p in points if p.get("atm_iv")), None)
    exp_ep = _epoch_ist(expiry + " 15:30:00") if expiry else None
    if bs and iv0 and exp_ep:
        sig = iv0 if iv0 < 1 else iv0 / 100.0   # accept fraction or percent IV
        yr = 365.25 * 24 * 3600.0
        for p in points:
            T = max(exp_ep - p["t"], 0) / yr
            try:
                theo = (bs.bs_price(p["spot"], p["atm"], T, sig, opt="CE")
                        + bs.bs_price(p["spot"], p["atm"], T, sig, opt="PE"))
                p["straddle_theo"] = round(theo, 2)
            except Exception:
                pass

    return {"ok": True, "underlying": u, "expiry": expiry, "expiries": exps, "points": points}


def available_dates(u):
    """All dates with a stored option-chain CSV for this underlying (sorted)."""
    out = set()
    for d in _lake_dirs(u):
        if os.path.isdir(d):
            for f in os.listdir(d):
                if f.startswith(u + "_") and f.endswith(".csv"):
                    out.add(f[len(u) + 1:-4])
    return sorted(out)


def curves_multi(u, end_date, days):
    """Concatenate the last `days` available option-chain days (<= end_date) into one
    continuous series — the SAME per-day points as curves(), just across days (each day
    auto-picks its own nearest expiry). Overnight gaps are natural on the time axis."""
    ds = [d for d in available_dates(u) if d <= end_date][-int(days):]
    pts, used, exps = [], [], []
    for d in ds:
        r = curves(u, d)
        if r.get("points"):
            pts.extend(r["points"])
            used.append(d)
            if r.get("expiry") and r["expiry"] not in exps:
                exps.append(r["expiry"])
    return {"ok": bool(pts), "underlying": u, "points": pts, "days_used": used,
            "multi": True, "expiries": sorted(exps), "expiry": None}


def strike_series(u, date, expiry, strike=None, opt_type=None):
    """Per-minute premium series for ONE strike+type (for the /curves right-click
    'Load strike chart'). Also returns the list of strikes available that day so the
    picker can offer them. Display-only."""
    _, rows = _load_rows(u, date)
    if not rows:
        return {"ok": False, "expiry": expiry, "strikes": [], "points": []}

    exps = sorted({r.get("expiry") for r in rows if r.get("expiry")})
    if expiry not in exps:
        expiry = exps[0] if exps else None

    strikes = sorted({_f(r.get("strike")) for r in rows
                      if r.get("expiry") == expiry and _f(r.get("strike")) is not None})

    pts = []
    want = _f(strike)
    ot = (opt_type or "").upper()
    if want is not None and ot in ("CE", "PE"):
        for r in rows:
            if r.get("expiry") != expiry or r.get("opt_type") != ot:
                continue
            if _f(r.get("strike")) != want:
                continue
            ep = _epoch_ist(r.get("datetime"))
            ltp = _f(r.get("ltp"))
            if ep is None or ltp is None:
                continue
            pts.append({
                "t": ep,
                "ltp": round(ltp, 2),
                "oi": _f(r.get("oi")) or 0.0,
                "iv": _f(r.get("iv")),
                "spot": _f(r.get("spot")),
            })
        pts.sort(key=lambda x: x["t"])

    return {
        "ok": True, "underlying": u, "expiry": expiry, "strikes": strikes,
        "strike": want, "opt_type": ot, "points": pts,
    }
