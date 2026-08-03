"""daily_report.py — one-scroll EOD "Daily Report" data builder. DISPLAY-ONLY.

Reuses (Rule 6B):
  - order_store.trades_for_range  → range-net + exit-date bucket (same as
    /api/orders/calendar-summary, TRAP #141 positional-safe netting)
  - charges.option_charges        → date-aware single-source charges/tax
  - strategy_registry.label       → canonical strategy name

No order / risk / Dhan path. Builds a ready-to-render dict for a given day
(or range, for the Weekly/Monthly toggle): KPIs, target table, stat table,
per-strategy + per-trade breakdowns, distribution chart arrays.

Trade dict from order_store._complete has:
  sym, entry (BUY/SELL), qty, entry_price, exit_price, entry_time, entry_date,
  exit_date, exit_time, pnl (GROSS), id, source, strategy, mode, symbol, sec_id,
  broker, tags, exit_reason
"""
import os as _o
import sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import _paths  # noqa: F401  (root + _core/_data on sys.path)
# charges lives in scratch/nifty_trend (single source, date-aware)
_s.path.insert(0, _o.path.join(
    _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), "scratch", "nifty_trend"))

from datetime import datetime, timedelta

import order_store

try:
    import charges as _charges
except Exception:                       # pragma: no cover
    _charges = None
try:
    import strategy_registry as _reg
except Exception:                       # pragma: no cover
    _reg = None
try:
    import dhan_master as _dm
except Exception:                       # pragma: no cover
    _dm = None

ROOT = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_CFG_PATH = _o.path.join(ROOT, "data", "report_config.json")     # {capital: N}
_TGT_PATH = _o.path.join(ROOT, "data", "report_targets.json")    # {strategy: expected_rs}


# ---------------------------------------------------------------- helpers ----
def _label(sid):
    try:
        return _reg.label(sid) if _reg else (sid or "—")
    except Exception:
        return sid or "—"


def _minus_days(dstr, n):
    try:
        return (datetime.strptime(dstr, "%Y-%m-%d") - timedelta(days=n)).strftime("%Y-%m-%d")
    except Exception:
        return dstr


def _points(t):
    """Premium points, sign from the REAL entry side (BUY/SELL) — matches pnl
    sign (pnl = (xp-ep)*q if BUY else (ep-xp)*q). Not TRAP #135 (that was the
    backtest side= option-direction; here `entry` is the actual order side)."""
    ep = t.get("entry_price") or 0.0
    xp = t.get("exit_price") or 0.0
    return round((xp - ep) if (t.get("entry") == "BUY") else (ep - xp), 2)


def _tax(t):
    if not _charges:
        return 0.0
    try:
        return round(_charges.option_charges(
            t.get("entry_price") or 0.0, t.get("exit_price") or 0.0,
            t.get("qty") or 0, entry_side=(t.get("entry") or "BUY"),
            when=t.get("exit_date")), 2)
    except Exception:
        return 0.0


def _lot_size(t):
    try:
        if _dm and t.get("sec_id"):
            lot = _dm.get_lot_size_by_sec_id(t.get("sec_id"))
            return int(lot) if lot else None
    except Exception:
        pass
    return None


def _dur(t):
    """Minutes between entry_time and exit_time (same-day)."""
    try:
        e = datetime.strptime(t.get("entry_time"), "%H:%M")
        x = datetime.strptime(t.get("exit_time"), "%H:%M")
        m = int((x - e).total_seconds() // 60)
        return m if m >= 0 else None
    except Exception:
        return None


def _fmt_dur(m):
    if m is None:
        return "—"
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h{m % 60:02d}"


def _read_json(path, default):
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---------------------------------------------------------------- collect ----
def _collect(date_from, date_to, mode=None, source=None, broker=None, strategy=None):
    """Completed round-trips whose EXIT date falls in [date_from, date_to],
    netted across a 400-day lookback so positional legs pair correctly."""
    filt = {}
    if mode:
        filt["mode"] = mode
    if source:
        filt["source"] = source
    if broker:
        filt["broker"] = broker
    if strategy:
        filt["strategy"] = strategy

    net_lo = _minus_days(date_from, 400)
    out = []
    try:
        rng = order_store.trades_for_range(net_lo, date_to, **filt)
    except Exception as e:
        print("[daily_report] trades_for_range fail:", e, flush=True)
        return out

    _lot_cache = {}
    for t in (rng.get("details") or []):
        xd = t.get("exit_date")
        if not xd or not (date_from <= xd <= date_to):
            continue
        gross = t.get("pnl") or 0.0
        tax = _tax(t)
        sid = t.get("strategy")
        sec = t.get("sec_id")
        if sec not in _lot_cache:
            _lot_cache[sec] = _lot_size(t)
        d = dict(t)
        d["gross"] = round(gross, 2)
        d["tax"] = tax
        d["net"] = round(gross - tax, 2)
        d["points"] = _points(t)
        d["label"] = _label(sid)
        d["lot_size"] = _lot_cache[sec]
        d["dur_min"] = _dur(t)
        d["dur"] = _fmt_dur(d["dur_min"])
        d["wl"] = "W" if d["net"] > 0 else "L"
        out.append(d)
    # chronological by ENTRY time → T1/T2/... numbering follows the order trades were
    # taken (user request), and the equity journey walks in that same order
    out.sort(key=lambda r: (r.get("entry_date") or "", r.get("entry_time") or ""))
    return out


# ------------------------------------------------------------- aggregates ----
def _kpis(trades, capital):
    net = round(sum(t["net"] for t in trades), 2)
    gross = round(sum(t["gross"] for t in trades), 2)
    charges = round(sum(t["tax"] for t in trades), 2)
    pts = round(sum(t["points"] for t in trades), 2)
    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    gp = sum(t["net"] for t in wins)
    gl = -sum(t["net"] for t in losses)
    pf = round(gp / gl, 2) if gl > 0 else (None if gp == 0 else float("inf"))
    n = len(trades)
    return {
        "net": net, "gross": gross, "charges": charges, "points": pts,
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(100.0 * len(wins) / n, 1) if n else 0.0,
        "pf": pf if pf != float("inf") else None,
        "net_pct": round(100.0 * net / capital, 2) if capital else None,
    }


def _stat_rows(trades):
    wins = [t["net"] for t in trades if t["net"] > 0]
    losses = [t["net"] for t in trades if t["net"] <= 0]
    avg_win = round(sum(wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(sum(losses) / len(losses), 2) if losses else 0.0
    wl_ratio = round(avg_win / abs(avg_loss), 2) if avg_loss else None
    # intraday equity walk (exit-time ordered) → max run-up / max drawdown
    eq = 0.0
    peak = 0.0
    trough = 0.0
    max_runup = 0.0
    max_dd = 0.0
    for t in trades:
        eq += t["net"]
        peak = max(peak, eq)
        trough = min(trough, eq)
        max_runup = max(max_runup, eq - trough)   # rise from lowest so far
        max_dd = min(max_dd, eq - peak)            # fall from highest so far
    return {
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "wl_ratio": wl_ratio,
        "max_runup": round(max_runup, 2),
        "max_drawdown": round(max_dd, 2),
        "tax": round(sum(t["tax"] for t in trades), 2),
        "win_pct": round(100.0 * len(wins) / len(trades), 1) if trades else 0.0,
        "total_points": round(sum(t["points"] for t in trades), 2),
        "total_trades": len(trades),
    }


def _by_strategy(trades):
    g = {}
    for t in trades:
        sid = t.get("strategy") or ""
        row = g.setdefault(sid, {
            "strategy": sid, "label": t["label"],
            "points": 0.0, "net": 0.0, "gross": 0.0, "tax": 0.0,
            "wins": 0, "losses": 0, "lots": 0.0,
        })
        row["points"] += t["points"]
        row["net"] += t["net"]
        row["gross"] += t["gross"]
        row["tax"] += t["tax"]
        row["wins"] += 1 if t["net"] > 0 else 0
        row["losses"] += 0 if t["net"] > 0 else 1
        if t.get("lot_size"):
            row["lots"] += (t.get("qty") or 0) / t["lot_size"]
    rows = []
    for row in g.values():
        for k in ("points", "net", "gross", "tax"):
            row[k] = round(row[k], 2)
        row["lots"] = round(row["lots"], 1)
        rows.append(row)
    rows.sort(key=lambda r: r["net"], reverse=True)
    return rows


def _target_rows(by_strat, targets):
    """Target table: real result per strategy + editable Expected (from
    data/report_targets.json, keyed by strategy id) + Δ."""
    rows = []
    for s in by_strat:
        exp = targets.get(s["strategy"])
        rows.append({
            "strategy": s["strategy"],
            "label": s["label"],
            "lots": s["lots"],
            "expected": exp,                       # None = not set
            "real": s["net"],
            "delta": round(s["net"] - exp, 2) if exp is not None else None,
        })
    return rows


def _trade_rows(trades):
    rows = []
    for i, t in enumerate(trades, 1):
        rows.append({
            "n": i,
            "id": t.get("id"),
            "label": t["label"],
            "strategy": t.get("strategy"),
            "sym": t.get("sym"),
            "side": t.get("entry"),          # BUY/SELL — for trade-chart param
            "qty": t.get("qty"),
            "points": t["points"],
            "net": t["net"],
            "gross": t["gross"],
            "tax": t["tax"],
            "wl": t["wl"],
            "dur": t["dur"],
            "entry_time": t.get("entry_time"),
            "exit_time": t.get("exit_time"),
            "entry_date": t.get("entry_date"),
            "exit_date": t.get("exit_date"),
            "entry_price": t.get("entry_price"),
            "exit_price": t.get("exit_price"),
            "sec_id": t.get("sec_id"),
            "exit_reason": t.get("exit_reason"),
        })
    return rows


def _write_json(path, obj):
    import json
    os = __import__("os")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_settings():
    """Report settings for the ⚙ modal — capital (for net %) + per-strategy
    Expected ₹ (Target table)."""
    return {
        "capital": _read_json(_CFG_PATH, {}).get("capital"),
        "targets": _read_json(_TGT_PATH, {}),
    }


def save_settings(capital=None, targets=None):
    if capital is not None:
        cfg = _read_json(_CFG_PATH, {})
        cfg["capital"] = capital if capital != "" else None
        _write_json(_CFG_PATH, cfg)
    if targets is not None:
        # drop blank/None entries so unset strategies fall back to "set"
        clean = {k: v for k, v in (targets or {}).items()
                 if v not in (None, "", 0)}
        _write_json(_TGT_PATH, clean)
    return get_settings()


# ------------------------------------------------------- available dates ----
def available_dates(mode=None, source=None, broker=None, strategy=None, lookback_days=730):
    """Sorted list of dates (YYYY-MM-DD) that actually have completed-trade data,
    bucketed the SAME way build() does (by EXIT date, same mode/source/broker/
    strategy filters, same 400-day positional netting). The date arrows use this
    to skip empty days — landing only on days the report will actually render.

    lookback_days = how far back to scan for dates-with-data (default 2y)."""
    from datetime import datetime as _dtn
    today = _dtn.now().strftime("%Y-%m-%d")
    date_from = _minus_days(today, lookback_days)
    filt = {}
    if mode:
        filt["mode"] = mode
    if source:
        filt["source"] = source
    if broker:
        filt["broker"] = broker
    if strategy:
        filt["strategy"] = strategy
    net_lo = _minus_days(date_from, 400)
    dates = set()
    try:
        rng = order_store.trades_for_range(net_lo, today, **filt)
    except Exception as e:
        print("[daily_report] available_dates fail:", e, flush=True)
        return []
    for t in (rng.get("details") or []):
        xd = t.get("exit_date")
        if xd and (date_from <= xd <= today):
            dates.add(xd)
    return sorted(dates)


# ------------------------------------------------------------------ build ----
def build(date_from, date_to=None, mode=None, source=None, broker=None, strategy=None):
    date_to = date_to or date_from
    cfg = _read_json(_CFG_PATH, {})
    capital = cfg.get("capital")
    targets = _read_json(_TGT_PATH, {})

    trades = _collect(date_from, date_to, mode, source, broker, strategy)
    by_strat = _by_strategy(trades)

    return {
        "ok": True,
        "date_from": date_from,
        "date_to": date_to,
        "mode": mode,
        "capital": capital,
        "kpis": _kpis(trades, capital),
        "stat": _stat_rows(trades),
        "target": _target_rows(by_strat, targets),
        "by_strategy": by_strat,
        "trades": _trade_rows(trades),
        # distribution chart arrays (client can also derive from above)
        "chart_by_strategy": [
            {"label": s["label"], "point": s["points"], "amt": s["net"],
             "tax": s["tax"]} for s in by_strat],
        "chart_by_trade": [
            {"label": f"T{r['n']}", "sym": r["sym"], "point": r["points"],
             "amt": r["net"], "tax": r["tax"], "dur": r["dur"]}
            for r in _trade_rows(trades)],
    }


# -------------------------------------------------------------------- cli ----
if __name__ == "__main__":
    import argparse
    import json as _json
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--mode", default=None)
    ap.add_argument("--strategy", default=None)
    a = ap.parse_args()
    out = build(a.dfrom, a.dto, mode=a.mode, strategy=a.strategy)
    k = out["kpis"]
    print(f"Daily Report {out['date_from']}..{out['date_to']}  mode={a.mode or 'all'}")
    print(f"  Net ₹{k['net']}  Gross ₹{k['gross']}  Charges ₹{k['charges']}  "
          f"Pts {k['points']}  Trades {k['trades']} ({k['wins']}W/{k['losses']}L)  "
          f"Win% {k['win_rate']}  PF {k['pf']}")
    print(f"  Strategies: {len(out['by_strategy'])}  |  targets set: "
          f"{sum(1 for r in out['target'] if r['expected'] is not None)}")
    if "--verbose" in _s.argv:
        print(_json.dumps(out, indent=2, default=str))
