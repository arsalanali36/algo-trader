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

    out = []
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
        out.append({
            "k": k,
            "gex": round(ce_gex - pe_gex),
            "ce_gex": round(ce_gex),
            "pe_gex": round(pe_gex),
            "vce": v_ce,
            "vpe": v_pe,
            "oi_ce": oi_ce,
            "oi_pe": oi_pe,
        })
        tot_ce_oi += oi_ce
        tot_pe_oi += oi_pe

    # Max-Pain: settle P that minimises total intrinsic payout to option buyers.
    def _pain(P):
        return sum(max(0.0, P - s["k"]) * s["oi_ce"] + max(0.0, s["k"] - P) * s["oi_pe"] for s in out)
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
    return {
        "dt": dt,
        "spot": round(spot, 2),
        "vix": round(vix, 2) if vix is not None else None,
        "net": round(net),
        "mp": max_pain,
        "ag": abs_gex,
        "flip": flip,
        "pcr": round(tot_pe_oi / tot_ce_oi, 2) if tot_ce_oi else None,
        "strikes": out,
    }


def profile(u, date, expiry=None):
    """Return {ok, underlying, date, expiry, expiries[], snaps[]} for one day.
    snaps = every captured minute of the chosen expiry (oldest -> newest)."""
    _, rows = _oc._load_rows(u, date)
    if not rows:
        return {"ok": False, "underlying": u, "date": date, "expiry": expiry,
                "expiries": [], "snaps": []}

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

    return {"ok": bool(snaps), "underlying": u, "date": date, "expiry": expiry,
            "expiries": exps, "snaps": snaps}


def latest(u, date, expiry=None):
    """Just the most recent snapshot (live auto-refresh)."""
    p = profile(u, date, expiry)
    p_snaps = p.get("snaps") or []
    return {**{k: v for k, v in p.items() if k != "snaps"},
            "snap": p_snaps[-1] if p_snaps else None}
