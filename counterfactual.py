"""
counterfactual.py — "Man in the loop" alternate history analysis.

Answers: "What would have happened if the user hadn't intervened manually?"

v1 Logic:
  - PANIC trades  = src == "manual"  (user entered AND exited themselves)
  - ALGO trades   = src in ("strategy", "webhook")
  - Actual P&L    = ALL completed trades (algo + manual)
  - Algo-only P&L = only strategy/webhook trades

  Counterfactual v2 (future, when exit_reason tagging is live):
  - Panic exit   = algo-opened trade with exit_reason == "MANUAL_CLOSE"
  - Counterfactual exit = simulate SL/Target/3:15 using Dhan OHLC
  - This will show "algo's position, but user closed it early — algo would have..."
"""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any

# ── Helpers ──────────────────────────────────────────────────────────────────

ALGO_SOURCES  = {"strategy", "webhook"}
PANIC_SOURCES = {"manual"}

def _to_min(hm: str) -> int:
    try:
        h, m = hm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _build_timeline(trades: list[dict], end_hm: str = "15:30") -> list[list]:
    """Build cumulative P&L timeline from a list of completed trade dicts.
    Returns [[HH:MM, cum_pnl, trail_peak, peak_ever], ...] anchored at 09:15."""
    trades_sorted = sorted(
        [t for t in trades if t.get("exit_time") and t.get("pnl") is not None],
        key=lambda t: t["exit_time"]
    )
    pts = [["09:15", 0.0, 0.0, 0.0]]
    cum = trail = peak = 0.0
    for t in trades_sorted:
        cum   += float(t["pnl"] or 0)
        trail  = max(trail, cum)
        peak   = max(peak, cum)
        pts.append([t["exit_time"], round(cum, 2), round(trail, 2), round(peak, 2)])
    if pts[-1][0] < end_hm:
        last = pts[-1]
        pts.append([end_hm, last[1], last[2], last[3]])
    return pts


def _entry_markers(trades: list[dict]) -> list[list]:
    """Return [[entry_time, cum_pnl_before_this_entry, sym], ...] for graph markers."""
    trades_sorted = sorted(
        [t for t in trades if t.get("exit_time") and t.get("pnl") is not None],
        key=lambda t: t["exit_time"]
    )
    entry_events = sorted(
        {(t["entry_time"], t["sym"]) for t in trades_sorted if t.get("entry_time")},
        key=lambda x: x[0]
    )
    entries, cum, eidx = [], 0.0, 0
    for t in trades_sorted:
        while eidx < len(entry_events) and entry_events[eidx][0] <= t["exit_time"]:
            entries.append([entry_events[eidx][0], round(cum, 2), entry_events[eidx][1]])
            eidx += 1
        cum += float(t["pnl"] or 0)
    while eidx < len(entry_events):
        entries.append([entry_events[eidx][0], round(cum, 2), entry_events[eidx][1]])
        eidx += 1
    return entries


# ── v2 Placeholder: OHLC-based counterfactual exit simulation ────────────────
# When a user manually closed an algo position early, simulate what the algo
# would have gotten using actual price data.
# REQUIRES: exit_reason == "MANUAL_CLOSE" tagged on closing order (live from
# next deploy) + Dhan OHLC access. Returns None until that data is available.

def _simulate_algo_exit_v2(trade: dict, sl_pct: float | None, tp_pct: float | None,
                             base_dir: Path | None = None) -> dict | None:
    """
    (v2 — future) Fetch 1-min OHLC from actual_exit_time to 15:15 and
    simulate whether SL/Target would have been hit. Returns
    {exit_time, exit_px, pnl, reason} or None if data unavailable.
    """
    # Stub — implement when exit_reason tagging is stable and OHLC cache is ready.
    return None


# ── Main analysis function ────────────────────────────────────────────────────

def analyze(date: str, order_store_mod=None, risk_cfg: dict | None = None) -> dict:
    """
    Returns full counterfactual analysis for a date.

    Response shape:
    {
      "actual_timeline":  [[hm, cum, trail, peak], ...],  # all trades
      "algo_timeline":    [[hm, cum, trail, peak], ...],  # strategy/webhook only
      "actual_entries":   [[hm, cum, sym], ...],
      "algo_entries":     [[hm, cum, sym], ...],
      "all_trades":       [...],   # all completed trade dicts (enriched)
      "panic_trades":     [...],   # manual trades
      "algo_trades":      [...],   # strategy/webhook trades
      "panic_exit_trades":[...],   # v2: algo-opened but manually closed (requires MANUAL_CLOSE tag)
      "summary": {
        "actual_pnl", "algo_pnl", "panic_pnl",
        "intervention_cost",  # algo_pnl - actual_pnl  (positive = algo was better)
        "panic_count", "algo_count",
        "algo_peak", "actual_peak",
        "v2_available": bool   # True when exit_reason tagging has data
      }
    }
    """
    if order_store_mod is None:
        import order_store as order_store_mod  # type: ignore

    data    = order_store_mod.trades_for(date)
    details = data.get("details") or []

    # ── Categorize ────────────────────────────────────────────────────────────
    algo_trades   = [t for t in details if (t.get("source") or "").lower() in ALGO_SOURCES]
    panic_trades  = [t for t in details if (t.get("source") or "").lower() in PANIC_SOURCES]

    # v2: algo-opened but user-closed (exit_reason == MANUAL_CLOSE)
    panic_exit_trades = [
        t for t in algo_trades
        if (t.get("exit_reason") or "").startswith("MANUAL_CLOSE")
    ]
    v2_available = len(panic_exit_trades) > 0

    # ── Timelines ─────────────────────────────────────────────────────────────
    actual_timeline = _build_timeline(details)
    algo_timeline   = _build_timeline(algo_trades)

    actual_entries  = _entry_markers(details)
    algo_entries    = _entry_markers(algo_trades)

    # ── Summary ───────────────────────────────────────────────────────────────
    def _total(trades):
        return round(sum(float(t.get("pnl") or 0) for t in trades
                         if t.get("exit_time")), 2)

    actual_pnl = _total(details)
    algo_pnl   = _total(algo_trades)
    panic_pnl  = _total(panic_trades)

    algo_peak   = max((p[3] for p in algo_timeline), default=0)
    actual_peak = max((p[3] for p in actual_timeline), default=0)

    # Enrich each trade with a "role" label for the UI table
    def _enrich(trades, role):
        out = []
        for t in trades:
            d = dict(t)
            d["cf_role"] = role  # "algo" | "panic" | "panic_exit"
            out.append(d)
        return out

    all_enriched = (
        _enrich(algo_trades,  "algo") +
        _enrich(panic_trades, "panic")
    )
    all_enriched.sort(key=lambda t: t.get("exit_time") or "")

    return {
        "actual_timeline":   actual_timeline,
        "algo_timeline":     algo_timeline,
        "actual_entries":    actual_entries,
        "algo_entries":      algo_entries,
        "all_trades":        all_enriched,
        "panic_trades":      _enrich(panic_trades, "panic"),
        "algo_trades":       _enrich(algo_trades,  "algo"),
        "panic_exit_trades": _enrich(panic_exit_trades, "panic_exit"),
        "summary": {
            "actual_pnl":        actual_pnl,
            "algo_pnl":          algo_pnl,
            "panic_pnl":         panic_pnl,
            "intervention_cost": round(algo_pnl - actual_pnl, 2),
            "panic_count":       len(panic_trades),
            "algo_count":        len(algo_trades),
            "algo_peak":         round(algo_peak, 2),
            "actual_peak":       round(actual_peak, 2),
            "v2_available":      v2_available,
        }
    }
