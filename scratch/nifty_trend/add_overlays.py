"""Inject per-trade INDICATOR OVERLAYS into a run's results.js so the trade-chart
shows the SAME levels the strategy used to decide — opening range, breakout trigger,
stop, target, entry window — not just entry/exit arrows. Goal: dekh ke samajh aaye
signal bana KYUN.

Data model written to results.js:
  meta.overlay_spec = {"window": {"h0": <int>, "h1": <int>}}            # entry-window shade
  meta.overlays["YYYY-MM-DD HH:MM"] = {                                 # keyed by trade entry_dt
      "band":  {"lo":.., "hi":.., "label":"Opening Range", "color":".."},
      "lines": [{"v":.., "label":"..", "color":"..", "dash":"..", "op":..}, ...],
      "atr":   <float> }
The chart (dashboard_intraday.html candleSVG) renders band + lines + window generically —
any strategy that emits these draws automatically. Add a <design>_overlays for new families.

Usage:    python add_overlays.py [run_slug]      (default: mid_orb_nifty)
Reusable: run_hunt.py calls build_overlays() directly for fresh hunts.
"""
import os
import sys
import json
import datetime as dt

import numpy as np
import pandas as pd

import intraday_engine as ie      # resample + load_1m (single source — Rule 6B)
import engine                     # engine.atr (canonical Wilder RMA)

HERE = os.path.dirname(os.path.abspath(__file__))

ORB_FAMILY = {"orb", "tod_orb", "orb_st"}


def _orb_kind(design):
    """'full' for plain ATM-ORB families (directional, ATR SL/target ARE the exits),
    'signal' for ORB-triggered OPTION STRUCTURES (straddle/vertical/backspread — they
    exit on premium tp_frac/sl_frac, so draw the opening range + break trigger only,
    no ATR SL/target line which would misrepresent the real exit), None otherwise.
    Compound design keys look like '<structure>/orb_break' or '.../tod_orb_break'."""
    d = str(design or "").lower()
    if d in ORB_FAMILY:
        return "full"
    if "orb_break" in d or "tod_orb" in d or d.split("/")[-1] in ORB_FAMILY:
        return "signal"
    return None


def _orb_overlay(d, atr, dts, p, side, entry_dt, signal_only=False):
    """Opening-range box + the breakout trigger that fired (+ SL/target bracket unless
    signal_only) for one ORB-triggered trade. Levels use the SAME formulas as
    intraday_engine (OR±k·ATR, entry ∓ atr_sl·ATR at the signal bar) so the chart
    matches the backtest by construction. `side`: long/short (directional) or a
    non-directional vol label (e.g. long_vol) → both triggers drawn."""
    orm = int(p.get("or_min", 30)); k = float(p.get("orb_k", 1.0))
    atr_sl = float(p.get("atr_sl", 2.0)); rr = float(p.get("rr", 2.0))
    edt = np.datetime64(pd.to_datetime(entry_dt))
    ix = np.where(dts == edt)[0]
    if len(ix) == 0:
        return None
    i = int(ix[0])                       # entry bar (fill = O[i]); signal bar = i-1
    sig = max(0, i - 1)
    day = pd.to_datetime(entry_dt).date()

    or_end = (dt.datetime.combine(day, dt.time(9, 15)) + dt.timedelta(minutes=orm)).time()
    day_mask = (d["day"].values == day)
    tt = pd.to_datetime(d["Datetime"]).dt.time.values
    or_mask = day_mask & (tt < or_end)
    if not or_mask.any():
        return None
    or_hi = float(d["High"].values[or_mask].max())
    or_lo = float(d["Low"].values[or_mask].min())

    av = float(atr[sig]) if atr[sig] > 0 else max(1.0, or_hi * 0.001)
    up_trig = or_hi + k * av
    lo_trig = or_lo - k * av
    entry = float(d["Open"].values[i])
    sd = atr_sl * av
    s = str(side or "").lower()
    is_long = s in ("long", "buy", "up")
    directional = is_long or s in ("short", "sell", "down")

    band = {"lo": round(or_lo, 1), "hi": round(or_hi, 1),
            "label": "Opening Range", "color": "#6b7684"}
    if not directional:
        # non-directional vol structure (straddle/strangle) — enters on a break EITHER way
        lines = [
            {"v": round(up_trig, 1), "label": "▲ Break ↑", "color": "#e3a008", "dash": "6 3", "w": 1.4},
            {"v": round(lo_trig, 1), "label": "▼ Break ↓", "color": "#e3a008", "dash": "6 3", "w": 1.4},
        ]
        return {"band": band, "lines": lines, "atr": round(av, 1)}

    if is_long:
        stop, target, fired, other = entry - sd, entry + rr * sd, up_trig, lo_trig
        arrow = "▲"
    else:
        stop, target, fired, other = entry + sd, entry - rr * sd, lo_trig, up_trig
        arrow = "▼"
    lines = [
        {"v": round(fired, 1), "label": arrow + " Break trigger",
         "color": "#e3a008", "dash": "6 3", "w": 1.6},
        {"v": round(other, 1), "label": "", "color": "#e3a008", "dash": "2 5", "op": 0.3},
    ]
    if not signal_only:                  # premium-exit structures: OR + trigger only
        lines += [
            {"v": round(stop, 1), "label": "SL", "color": "#f85149", "dash": "5 4"},
            {"v": round(target, 1), "label": "Target", "color": "#3fb950", "dash": "5 4"},
        ]
    return {"band": band, "lines": lines, "atr": round(av, 1)}


def build_overlays(design, params, tf, trades, csv="nifty_1min.csv"):
    """Return (overlays_dict, overlay_spec) for a run. `trades` = iterable of trade dicts
    (needs 'side' + 'entry_dt'). Dedupes by entry_dt across passes/periods."""
    kind = _orb_kind(design)
    if not kind:
        return {}, None                          # no overlay renderer for this family yet
    signal_only = (kind == "signal")
    d = ie.resample(ie.load_1m(csv), tf)
    atr = engine.atr(d, int(params.get("atr_period", 14))).values
    dts = pd.to_datetime(d["Datetime"]).values
    overlays = {}
    for t in trades:
        key = t["entry_dt"]
        if key in overlays:
            continue
        ov = _orb_overlay(d, atr, dts, params, t.get("side"), key, signal_only=signal_only)
        if ov:
            overlays[key] = ov
    spec = {"window": {"h0": int(params.get("h0", 9)), "h1": int(params.get("h1", 15))}}
    return overlays, spec


def patch(run_slug):
    p = os.path.join(HERE, "runs", run_slug, "results.js")
    txt = open(p, encoding="utf-8").read()
    obj = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    meta = obj["meta"]
    design = meta.get("design_key") or meta.get("design")
    params = dict(meta.get("dna") or {})
    tf = meta.get("tf", "15m")
    # union of trades across all combos/passes (levels are on the spot, identical per entry_dt)
    seen, trades = set(), []
    for c in obj.get("combos", {}).values():
        for t in c.get("all_trades", []):
            if t["entry_dt"] not in seen:
                seen.add(t["entry_dt"]); trades.append(t)
    overlays, spec = build_overlays(design, params, tf, trades)
    meta["overlays"] = overlays
    if spec:
        meta["overlay_spec"] = spec
    open(p, "w", encoding="utf-8").write("window.RESULTS = " + json.dumps(obj, default=float) + ";")
    print(f"patched runs/{run_slug}/results.js — design={design} "
          f"{len(overlays)} trade-overlays, window {spec['window'] if spec else '-'}, "
          f"{os.path.getsize(p)//1024} KB")


if __name__ == "__main__":
    patch(sys.argv[1] if len(sys.argv) > 1 else "mid_orb_nifty")
