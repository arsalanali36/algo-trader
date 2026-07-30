"""
gex_profile.py — Gamma-Exposure (GEX) profile per strike, from the on-disk
option-chain snapshots. Display-only (no order/risk/live path).

SOURCE: `_TRADING_DATA/OptionChain/<U>/<U>_<date>.csv`, written every minute by
`_ops/option_chain_collector.py` — per strike: oi + volume + REAL Dhan greeks
(gamma) + spot + India-VIX. Same lake `option_curves.py` reads.

WHAT IT COMPUTES (per minute, for a chosen expiry) — a QuantTradingApp-style
GEX profile:
  - Per-strike Net GEX = (gammaCE·OI_CE − gammaPE·OI_PE) × spot² × 0.01
      (dealer-long-calls / short-puts convention; positive = vol-suppressing,
       negative = vol-amplifying). OI is already in underlying UNITS in the
       Dhan feed (OI × lot), so NO extra lot multiply — verified against real
       gamma/OI values (2026-07-25).
  - Per-strike CE / PE traded volume (the call-wall / put-wall mountains).
  - Spot, India-VIX, PCR (total PE OI / total CE OI), net GEX (regime sign).
  - Max-Pain strike (settle P minimising total option-buyer payout).
  - Abs-GEX strike (single largest |GEX| wall) + zero-gamma flip (cumulative
      net GEX sign change).

Returns EVERY minute's snapshot for the day so the frontend can scrub / play
the profile and watch spot / max-pain / flip move — plus the latest one for
live auto-refresh. mtime-cached; the CSV loader is REUSED from option_curves
(Rule 6B — one lake reader, not two).

RULE 10 note: GEX's dealer-sign convention is a US/SPX-derived structural map.
For NIFTY it's a context/level tool (walls, flip, max-pain pin), NOT a
backtested signal — this module only DISPLAYS, it never gates or orders.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)  # _ops/ -> project root
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# Reuse option_curves' lake reader + float parse (single source, Rule 6B).
import option_curves as _oc  # noqa: E402


def _f(x):
    return _oc._f(x)


# GEX scale constant: 1% move => gamma * OI * spot^2 * 0.01. OI already in units.
_GEX_K = 0.01


# ---------------------------------------------------------------- smoothing (display-only)
# The jumpy quantities (γ-flip, abs-GEX wall, max-pain) are winner-take-all — a zero-crossing /
# argmax / argmin over a near-flat, per-minute-recomputed landscape — so a tiny change flips the
# "winner" to an adjacent strike and the line leaps 100-150pt. This is display noise, not new
# information. We calm it WITHOUT lying: per-strike GEX bars get a time-EMA (α), and the scalar
# levels get a rolling MEDIAN (spike-proof) + a confirm-window HYSTERESIS — the exact arm/gap/
# confirm pattern the kill-floor uses (LESSONS TRAP #80-81: the fix for whipsaw is time-
# confirmation, not granularity). A level only moves once a NEW strike persists `confirm` samples.
_SMOOTH = {
    "off":  None,
    "low":  {"win": 7,  "confirm": 2, "alpha": 0.40},   # responsive — only single-tick spikes gone
    "med":  {"win": 15, "confirm": 3, "alpha": 0.25},   # default — flicker gone, real shift ~3-4 min
    "high": {"win": 30, "confirm": 5, "alpha": 0.15},   # calm — only big structural shifts move
}
_STRIKE_KEYS = ("flip", "mp", "ag", "cw", "pw")   # snap to real strike + median + hysteresis
_EM_KEYS = ("emh", "eml")                          # continuous prices → median only
_CONT_KEYS = ("net", "pcr", "iv", "vix")           # continuous KPIs → short trailing mean


def _median(vals):
    v = sorted(x for x in vals if x is not None)
    n = len(v)
    if not n:
        return None
    m = n // 2
    return v[m] if n % 2 else (v[m - 1] + v[m]) / 2.0


def _strike_step(snaps):
    """Median gap between adjacent strikes (the natural hysteresis unit for this underlying)."""
    for s in snaps:
        ks = sorted({st["k"] for st in s.get("strikes", []) if st.get("k") is not None})
        diffs = sorted(b - a for a, b in zip(ks, ks[1:]) if b > a)
        if diffs:
            return diffs[len(diffs) // 2]
    return 50.0


def _smooth_snaps(snaps, level="med"):
    """Return a smoothed copy of the ordered snaps (oldest->newest). Level in _SMOOTH.
    Display-only — never touches OI/max-pain math, just calms what gets DRAWN."""
    cfg = _SMOOTH.get(level)
    if not cfg or len(snaps) < 3:
        return snaps
    win, confirm, alpha = cfg["win"], cfg["confirm"], cfg["alpha"]
    step = _strike_step(snaps)
    thr = step * 0.5                       # deadband = half a strike → any real strike change needs confirm
    n = len(snaps)
    out = [dict(s) for s in snaps]         # shallow copy; strikes replaced below

    # 1) per-strike GEX bars: time-EMA per strike k (align by k; new strikes start fresh)
    ema = {}
    for idx, s in enumerate(snaps):
        rows = []
        for st in s.get("strikes", []):
            k = st["k"]
            prev = ema.get(k)
            e = st["gex"] if prev is None else alpha * st["gex"] + (1 - alpha) * prev
            ema[k] = e
            rows.append({"k": k, "gex": round(e), "vce": st["vce"], "vpe": st["vpe"]})
        out[idx]["strikes"] = rows

    ks_of = [sorted({st["k"] for st in s.get("strikes", []) if st.get("k") is not None}) for s in snaps]

    def _snap_to_strike(val, i):
        ks = ks_of[i]
        if val is None or not ks:
            return val
        return min(ks, key=lambda k: abs(k - val))

    # 2) winner-take-all strike levels: snap-to-strike -> confirm-window hysteresis.
    # NO median here — the confirm window alone is the flicker filter: pure alternation
    # (24450<->24600) never gets `confirm` in a row so it never commits, while a GENUINE
    # sustained shift commits in exactly `confirm` samples (~3 min at med). A median would
    # only add ~half-its-window of lag on real shifts for no extra flicker protection.
    for key in _STRIKE_KEYS:
        raw = [s.get(key) for s in snaps]
        med = [_snap_to_strike(v, i) for i, v in enumerate(raw)]
        disp = next((m for m in med if m is not None), None)
        pend, ct = None, 0
        for i in range(n):
            m = med[i]
            if m is None or disp is None:
                disp = m if disp is None else disp
            elif abs(m - disp) < thr:       # same strike / inside deadband
                pend, ct = None, 0
            else:                            # a genuinely different strike — must persist `confirm` samples
                if pend is not None and abs(m - pend) < thr:
                    ct += 1
                else:
                    pend, ct = m, 1
                if ct >= confirm:
                    disp, pend, ct = pend, None, 0
            out[i][key] = disp

    # 3) continuous ±1σ band: rolling median (moves smoothly with spot, no hysteresis needed)
    for key in _EM_KEYS:
        raw = [s.get(key) for s in snaps]
        for i in range(n):
            m = _median(raw[max(0, i - win + 1): i + 1])
            out[i][key] = round(m, 1) if m is not None else raw[i]

    # 4) continuous KPIs (net/pcr/iv/vix): short trailing mean; net kept integer for regime sign
    cwin = max(3, win // 3)
    for key in _CONT_KEYS:
        raw = [s.get(key) for s in snaps]
        for i in range(n):
            w = [v for v in raw[max(0, i - cwin + 1): i + 1] if v is not None]
            if not w:
                out[i][key] = raw[i]
            elif key == "net":
                out[i][key] = round(sum(w) / len(w))
            else:
                out[i][key] = round(sum(w) / len(w), 2)
    return out


def _snapshot(legs):
    """One minute -> {dt, spot, vix, net, mp, ag, flip, pcr, strikes[]}.
    `legs` = all CSV rows for one (expiry, datetime). Returns None on bad data."""
    spot = _f(legs[0].get("spot"))
    if spot is None:
        return None
    vix = _f(legs[0].get("vix"))
    dt = legs[0].get("datetime")

    strikes = sorted({_f(l.get("strike")) for l in legs if _f(l.get("strike")) is not None})
    if not strikes:
        return None

    # `out` = the LEAN per-strike payload the chart actually draws (k / gex / vce / vpe);
    # OI is kept only in `pain_rows` for the max-pain calc, never serialised (keeps the
    # per-day JSON ~half the size — the strikes array dominates it).
    out = []
    pain_rows = []           # (k, oi_ce, oi_pe) — max-pain only
    tot_ce_oi = tot_pe_oi = 0.0
    for k in strikes:
        ce = next((l for l in legs if _f(l.get("strike")) == k and l.get("opt_type") == "CE"), None)
        pe = next((l for l in legs if _f(l.get("strike")) == k and l.get("opt_type") == "PE"), None)
        g_ce = (_f(ce.get("gamma")) or 0.0) if ce else 0.0
        g_pe = (_f(pe.get("gamma")) or 0.0) if pe else 0.0
        oi_ce = (_f(ce.get("oi")) or 0.0) if ce else 0.0
        oi_pe = (_f(pe.get("oi")) or 0.0) if pe else 0.0
        v_ce = (_f(ce.get("volume")) or 0.0) if ce else 0.0
        v_pe = (_f(pe.get("volume")) or 0.0) if pe else 0.0
        ce_gex = g_ce * oi_ce * spot * spot * _GEX_K
        pe_gex = g_pe * oi_pe * spot * spot * _GEX_K
        out.append({"k": k, "gex": round(ce_gex - pe_gex), "vce": round(v_ce), "vpe": round(v_pe)})
        pain_rows.append((k, oi_ce, oi_pe))
        tot_ce_oi += oi_ce
        tot_pe_oi += oi_pe

    # Max-Pain: settle P that minimises total intrinsic payout to option buyers.
    def _pain(P):
        return sum(max(0.0, P - kk) * oc + max(0.0, kk - P) * op for (kk, oc, op) in pain_rows)
    max_pain = min(strikes, key=_pain)

    # Abs-GEX wall (largest |GEX|) + zero-gamma flip (cumulative net sign change).
    abs_gex = max(out, key=lambda s: abs(s["gex"]))["k"]
    flip = None
    cum = 0.0
    prev_cum = 0.0
    prev_k = None
    for s in out:
        prev_cum = cum
        cum += s["gex"]
        if prev_k is not None and ((prev_cum <= 0 <= cum) or (prev_cum >= 0 >= cum)):
            # crossover between prev_k and s["k"] — pick the nearer side by |cum|
            flip = prev_k if abs(prev_cum) <= abs(cum) else s["k"]
            break
        prev_k = s["k"]
    if flip is None:
        flip = abs_gex

    net = sum(s["gex"] for s in out)

    # Trade-engine levels (Phase 2 candle overlay): OI walls + ±1σ expected-move band.
    call_wall = max(pain_rows, key=lambda r: r[1])[0] if pain_rows else None   # highest CE OI
    put_wall = max(pain_rows, key=lambda r: r[2])[0] if pain_rows else None    # highest PE OI
    em_hi = em_lo = None
    atm = min(strikes, key=lambda k: abs(k - spot))
    ce_a = next((l for l in legs if _f(l.get("strike")) == atm and l.get("opt_type") == "CE"), None)
    pe_a = next((l for l in legs if _f(l.get("strike")) == atm and l.get("opt_type") == "PE"), None)
    ivs = [v for v in ((_f(ce_a.get("iv")) if ce_a else None), (_f(pe_a.get("iv")) if pe_a else None)) if v]
    atm_iv = (sum(ivs) / len(ivs)) if ivs else None
    if atm_iv:
        try:
            from datetime import date as _date
            ey, em_, ed = (int(x) for x in (legs[0].get("expiry") or "").split("-"))
            dy, dm, dd = (int(x) for x in (dt or "")[:10].split("-"))
            dte = (_date(ey, em_, ed) - _date(dy, dm, dd)).days
        except Exception:
            dte = 1
        dte = max(dte, 0) + 0.3   # floor so an expiry-day band isn't literally zero-width
        move = spot * (atm_iv / 100.0) * (dte / 365.0) ** 0.5
        em_hi = round(spot + move, 1)
        em_lo = round(spot - move, 1)

    return {
        "dt": dt,
        "spot": round(spot, 2),
        "vix": round(vix, 2) if vix is not None else None,
        "net": round(net),
        "mp": max_pain,
        "ag": abs_gex,
        "flip": flip,
        "cw": call_wall,       # call wall (highest CE OI — resistance)
        "pw": put_wall,        # put wall (highest PE OI — support)
        "emh": em_hi,          # +1σ expected-move (ATM IV to expiry)
        "eml": em_lo,          # -1σ expected-move
        "iv": round(atm_iv, 2) if atm_iv else None,
        "pcr": round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi else None,
        "strikes": out,
    }


# ---------------------------------------------------------------- historical (lake, OI-only)
import datetime as _dt
_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
_LAKE_CACHE = {}   # (u, date) -> snaps  (historical data is immutable)


def _lake_root(u):
    for base in (os.path.join(os.path.dirname(PROJECT), "._TRADING DATA"),
                 os.path.join(PROJECT, "_TRADING_DATA")):
        p = os.path.join(base, "OptChainLake_1m", u, "WEEK")
        if os.path.isdir(p):
            return p
    return None


def _lake_profile(u, date):
    """Historical profile from the expired-options lake (OptChainLake_1m). The lake
    has REAL per-strike OI + volume + spot but NO greeks/IV — so the bars are Net OI
    (OI_CE − OI_PE = call-heavy / put-heavy), NOT gamma-GEX; walls / max-pain / PCR
    are the real OI ones; EM / IV / VIX are absent (None). Nothing is BS-derived."""
    root = _lake_root(u)
    if not root:
        return None
    key = (u, date)
    if key in _LAKE_CACHE:
        return _LAKE_CACHE[key]
    import pandas as pd
    try:
        d0 = _dt.datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=_IST)
    except Exception:
        return None
    start = int(d0.timestamp()); end = start + 86400
    bymin = {}    # ts -> {"spot":x, "ce":{strike:(oi,vol)}, "pe":{strike:(oi,vol)}}
    for side in ("CE", "PE"):
        for off in range(-10, 11):
            fn = f"{side}_ATM.csv" if off == 0 else f"{side}_ATM{'p' if off > 0 else 'm'}{abs(off)}.csv"
            p = os.path.join(root, fn)
            if not os.path.exists(p):
                continue
            try:
                df = pd.read_csv(p, usecols=["timestamp", "volume", "oi", "strike", "spot"])
                df = df[(df["timestamp"] >= start) & (df["timestamp"] < end)]
            except Exception:
                continue
            sk = "ce" if side == "CE" else "pe"
            for ts, vol, oi, strike, spot in zip(df["timestamp"], df["volume"], df["oi"], df["strike"], df["spot"]):
                m = bymin.setdefault(int(ts), {"spot": None, "ce": {}, "pe": {}})
                if spot == spot:
                    m["spot"] = float(spot)
                m[sk][float(strike)] = (float(oi) if oi == oi else 0.0, float(vol) if vol == vol else 0.0)
    if not bymin:
        return None

    snaps = []
    for ts in sorted(bymin):
        m = bymin[ts]
        spot = m["spot"]
        strikes = sorted(set(m["ce"]) | set(m["pe"]))
        if not strikes or spot is None:
            continue
        out, pain_rows, tce, tpe = [], [], 0.0, 0.0
        for k in strikes:
            oi_ce, v_ce = m["ce"].get(k, (0.0, 0.0))
            oi_pe, v_pe = m["pe"].get(k, (0.0, 0.0))
            out.append({"k": k, "gex": round(oi_ce - oi_pe), "vce": round(v_ce), "vpe": round(v_pe)})
            pain_rows.append((k, oi_ce, oi_pe)); tce += oi_ce; tpe += oi_pe
        max_pain = min(strikes, key=lambda P: sum(max(0.0, P - kk) * oc + max(0.0, kk - P) * op for kk, oc, op in pain_rows))
        call_wall = max(pain_rows, key=lambda r: r[1])[0]
        put_wall = max(pain_rows, key=lambda r: r[2])[0]
        abs_g = max(out, key=lambda s: abs(s["gex"]))["k"]
        flip, cum, prev, prevk = None, 0.0, 0.0, None
        for s in out:
            prev = cum; cum += s["gex"]
            if prevk is not None and ((prev <= 0 <= cum) or (prev >= 0 >= cum)):
                flip = prevk if abs(prev) <= abs(cum) else s["k"]; break
            prevk = s["k"]
        if flip is None:
            flip = abs_g
        snaps.append({
            "dt": _dt.datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S"),
            "spot": round(spot, 2), "vix": None, "net": round(sum(s["gex"] for s in out)),
            "mp": max_pain, "ag": abs_g, "flip": flip, "cw": call_wall, "pw": put_wall,
            "emh": None, "eml": None, "iv": None,
            "pcr": round(tpe / tce, 2) if tce else None, "strikes": out,
        })
    if len(_LAKE_CACHE) > 8:
        _LAKE_CACHE.clear()
    _LAKE_CACHE[key] = snaps
    return snaps


def profile(u, date, expiry=None, smooth="med"):
    """Return {ok, underlying, date, expiry, expiries[], snaps[], source, smooth} for one day.
    snaps = every captured minute (oldest -> newest). source='collector' (recent, real
    greeks → gamma-GEX) or 'lake' (historical, real OI → Net-OI bars, no greeks).
    `smooth` (off/low/med/high) calms the winner-take-all levels + bars — display-only."""
    _, rows = _oc._load_rows(u, date)
    if rows:
        exps = []
        for r in rows:
            e = r.get("expiry")
            if e and e not in exps:
                exps.append(e)
        exps = sorted(exps)
        if expiry not in exps:
            expiry = exps[0] if exps else None
        bydt = {}
        for r in rows:
            if r.get("expiry") != expiry:
                continue
            bydt.setdefault(r.get("datetime"), []).append(r)
        snaps = []
        for dt in sorted(k for k in bydt.keys() if k):
            snap = _snapshot(bydt[dt])
            if snap:
                snaps.append(snap)
        if snaps:
            return {"ok": True, "underlying": u, "date": date, "expiry": expiry,
                    "expiries": exps, "snaps": _smooth_snaps(snaps, smooth),
                    "source": "collector", "smooth": smooth}

    # collector empty (older than the ~2-week live window) → historical lake, OI-based
    snaps = _lake_profile(u, date)
    if snaps:
        return {"ok": True, "underlying": u, "date": date, "expiry": "weekly",
                "expiries": ["weekly"], "snaps": _smooth_snaps(snaps, smooth),
                "source": "lake", "smooth": smooth}
    return {"ok": False, "underlying": u, "date": date, "expiry": expiry,
            "expiries": [], "snaps": [], "source": None, "smooth": smooth}


def available_dates(u):
    """Sorted (oldest->newest) list of dates that have a captured chain CSV for
    this underlying. Lets the UI default to the most recent day WITH data instead
    of blindly today (blank on weekends / pre-market)."""
    import glob
    seen = set()
    for d in _oc._lake_dirs(u):
        for p in glob.glob(os.path.join(d, f"{u}_*.csv")):
            b = os.path.basename(p)
            dt = b[len(u) + 1:].replace(".csv", "")
            if len(dt) == 10 and dt[4] == "-":
                seen.add(dt)
    return sorted(seen)


def latest_date(u):
    ds = available_dates(u)
    return ds[-1] if ds else None


def latest(u, date, expiry=None, smooth="med"):
    """Just the most recent snapshot (live auto-refresh)."""
    p = profile(u, date, expiry, smooth=smooth)
    p_snaps = p.get("snaps") or []
    return {**{k: v for k, v in p.items() if k != "snaps"},
            "snap": p_snaps[-1] if p_snaps else None}
