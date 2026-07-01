"""
counterfactual.py — "Man in the loop" alternate history analysis.

Architecture (two separate broker accounts):
  - Dhan (order_store) = algo trades exclusively
  - Zerodha / Kite     = user's manual trades exclusively (all "panic")

Actual P&L  = Dhan algo + Zerodha manual combined
Algo-only   = Dhan only (what would have happened without panic)
Panic cost  = Zerodha manual P&L (how much the interventions cost)
"""

from __future__ import annotations
import json
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ist_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


# ── Kite (Zerodha) fetch + cache ──────────────────────────────────────────────

def _fetch_kite_fills(date: str) -> list[dict]:
    """Raw Kite fills for a date. Live fetch for today; cache for past dates."""
    cache = DATA_DIR / f"kite_trades_{date}.json"
    is_today = (date == _ist_today())

    if not is_today and cache.exists():
        return json.loads(cache.read_text())

    import sys
    sys.path.insert(0, str(BASE_DIR))
    from brokers.kite_broker import _load_kite
    kite  = _load_kite()
    fills = kite.trades()
    result = []
    for f in fills:
        ts = str(f.get("fill_timestamp") or f.get("exchange_timestamp") or "")
        if not ts or ts[:10] != date:
            continue
        result.append({
            "trade_id": str(f.get("trade_id") or ""),
            "order_id": str(f.get("order_id") or ""),
            "time":     ts[11:16] if len(ts) >= 16 else ts,
            "type":     str(f.get("transaction_type") or ""),
            "sym":      str(f.get("tradingsymbol") or ""),
            "qty":      int(f.get("quantity") or 0),
            "px":       float(f.get("average_price") or 0),
        })
    DATA_DIR.mkdir(exist_ok=True)
    cache.write_text(json.dumps(result))
    return result


# ── Zerodha CSV import (offline cache) ───────────────────────────────────────

def load_kite_csv(csv_path: str, date: str) -> None:
    """Parse a Zerodha tradebook CSV and save as kite_trades_YYYY-MM-DD.json cache."""
    import csv as _csv
    rows = []
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        for row in _csv.DictReader(fh):
            fill_time = row.get("Fill time", "").strip()
            if not fill_time.startswith(date):
                continue
            ts = fill_time[11:16] if len(fill_time) >= 16 else ""
            rows.append({
                "trade_id": row.get("Trade ID", "").strip(),
                "order_id": row.get("Order ID", "").strip(),
                "time":     ts,
                "type":     row.get("Type", "").strip().upper(),
                "sym":      row.get("Instrument", "").strip(),
                "qty":      int(row.get("Qty.", "0").replace(",", "") or 0),
                "px":       float(row.get("Avg. Price", "0").replace(",", "") or 0),
            })
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / f"kite_trades_{date}.json"
    cache.write_text(json.dumps(rows))


# ── FIFO matching — fills → round-trip trades ─────────────────────────────────

def _fifo_match(fills: list[dict]) -> list[dict]:
    """Match BUY/SELL fills per symbol using FIFO → realized P&L per round-trip."""
    fills_sorted = sorted(fills, key=lambda f: f["time"])
    queues: dict[str, deque] = defaultdict(deque)
    trades: list[dict] = []

    for f in fills_sorted:
        sym, typ, qty, px, t = f["sym"], f["type"], f["qty"], f["px"], f["time"]
        sign = 1 if typ == "BUY" else -1
        q = queues[sym]

        if not q or (q[0]["qty"] > 0) == (sign > 0):
            q.append({"qty": qty * sign, "px": px, "time": t,
                      "order_id": f.get("order_id", ""),
                      "trade_id": f.get("trade_id", "")})
        else:
            remaining = qty
            while remaining > 0 and q:
                head     = q[0]
                head_abs = abs(head["qty"])
                matched  = min(remaining, head_abs)
                pnl = ((px - head["px"]) * matched if head["qty"] > 0
                       else (head["px"] - px) * matched)
                trades.append({
                    "sym":            sym,
                    "entry":          "BUY" if head["qty"] > 0 else "SELL",
                    "entry_time":     head["time"],
                    "entry_px":       head["px"],
                    "exit_time":      t,
                    "exit_px":        px,
                    "qty":            matched,
                    "pnl":            round(pnl, 2),
                    "cf_role":        "panic",
                })
                remaining -= matched
                if matched == head_abs:
                    q.popleft()
                else:
                    head["qty"] = (head["qty"] // abs(head["qty"])) * (head_abs - matched)
            if remaining > 0:
                q.append({"qty": remaining * sign, "px": px, "time": t,
                          "order_id": f.get("order_id", ""),
                          "trade_id": f.get("trade_id", "")})

    return trades


# ── Timeline & marker builders ────────────────────────────────────────────────

def _build_timeline(trades: list[dict]) -> list[list]:
    """[time, cum_pnl, trail_peak, all_time_peak] — starts at 09:15 with 0."""
    ts = sorted([t for t in trades if t.get("exit_time") and t.get("pnl") is not None],
                key=lambda t: t["exit_time"])
    pts = [["09:15", 0.0, 0.0, 0.0]]
    cum = trail = peak = 0.0
    for t in ts:
        cum   += float(t["pnl"] or 0)
        trail  = max(trail, cum)
        peak   = max(peak, cum)
        pts.append([t["exit_time"], round(cum, 2), round(trail, 2), round(peak, 2)])
    if pts[-1][0] < "15:30":
        last = pts[-1]
        pts.append(["15:30", last[1], last[2], last[3]])
    return pts


def _entry_markers(trades: list[dict]) -> list[list]:
    """[entry_time, cum_pnl_at_that_point, sym] — for drawing ▲ on graph."""
    ts = sorted([t for t in trades if t.get("exit_time") and t.get("pnl") is not None],
                key=lambda t: t["exit_time"])
    events = sorted({(t["entry_time"], t.get("sym", "")) for t in ts if t.get("entry_time")},
                    key=lambda x: x[0])
    markers, cum, ei = [], 0.0, 0
    for t in ts:
        while ei < len(events) and events[ei][0] <= t["exit_time"]:
            markers.append([events[ei][0], round(cum, 2), events[ei][1]])
            ei += 1
        cum += float(t["pnl"] or 0)
    while ei < len(events):
        markers.append([events[ei][0], round(cum, 2), events[ei][1]])
        ei += 1
    return markers


# ── Main ──────────────────────────────────────────────────────────────────────

def analyze(date: str, order_store_mod=None, risk_cfg: dict | None = None) -> dict:
    """
    Two-broker counterfactual analysis:
      algo_trades   = Dhan order_store (algorithm placed)
      panic_trades  = Zerodha Kite     (user manually placed)
      actual        = both combined    (what really happened across both accounts)
    """
    if order_store_mod is None:
        import order_store as order_store_mod  # type: ignore

    # ── Dhan algo trades (order_store) ───────────────────────────────────────
    os_data    = order_store_mod.trades_for(date)
    os_details = os_data.get("details") or []
    algo_trades: list[dict] = []
    for t in os_details:
        if t.get("mode") == "paper":
            continue   # paper trades = not real money, exclude from counterfactual
        row = dict(t)
        row["cf_role"]    = "algo"
        row["sym"]        = row.get("trad_sym") or row.get("sym") or ""
        row["entry_time"] = (row.get("entry_time") or "")[:5]
        row["exit_time"]  = (row.get("exit_time")  or "")[:5]
        row["pnl"]        = float(row.get("pnl") or 0)
        algo_trades.append(row)

    # ── Zerodha panic trades (Kite) ──────────────────────────────────────────
    kite_error   = None
    panic_trades : list[dict] = []
    kite_ok      = False
    try:
        fills        = _fetch_kite_fills(date)
        panic_trades = _fifo_match(fills)
        kite_ok      = True
    except Exception as e:
        kite_error = str(e)

    # Combined (actual) = Dhan + Zerodha
    all_trades = sorted(algo_trades + panic_trades,
                        key=lambda t: t.get("exit_time") or "")

    actual_timeline = _build_timeline(all_trades)
    algo_timeline   = _build_timeline(algo_trades)

    actual_entries  = _entry_markers(all_trades)
    algo_entries    = _entry_markers(algo_trades)

    def _total(lst):
        return round(sum(float(t.get("pnl") or 0) for t in lst), 2)

    actual_pnl = _total(all_trades)
    algo_pnl   = _total(algo_trades)
    panic_pnl  = _total(panic_trades)

    algo_peak   = max((p[3] for p in algo_timeline),   default=0.0)
    actual_peak = max((p[3] for p in actual_timeline), default=0.0)

    return {
        "source":             "kite" if kite_ok else "order_store_only",
        "kite_error":         kite_error,
        "actual_timeline":    actual_timeline,
        "algo_timeline":      algo_timeline,
        "actual_entries":     actual_entries,
        "algo_entries":       algo_entries,
        "all_trades":         all_trades,
        "panic_trades":       panic_trades,
        "algo_trades":        algo_trades,
        "summary": {
            "actual_pnl":         actual_pnl,
            "algo_pnl":           algo_pnl,
            "panic_pnl":          panic_pnl,
            "intervention_cost":  round(algo_pnl - actual_pnl, 2),  # positive = algo was better
            "panic_count":        len(panic_trades),
            "algo_count":         len(algo_trades),
            "algo_peak":          round(algo_peak, 2),
            "actual_peak":        round(actual_peak, 2),
            "data_source":        "kite_api" if kite_ok else "order_store",
        },
    }
