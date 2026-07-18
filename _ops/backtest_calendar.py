"""backtest_calendar.py — surface backtest run results in the Stats calendar.

DISPLAY-ONLY. Reads the same `runs/<slug>/results.js` files the /lab hub uses
(`window.RESULTS = {...}`) and re-shapes ONE combo's `all_trades[]` into the
EXACT `{summary, trades, filters}` shape that `/api/orders/calendar-summary`
returns for live/paper trades — so the Stats page's calendar grid, summary
table, points table and equity curve render backtest data unchanged.

No order path, no risk path, no writes. Just parse + bucket-by-day.

Combo selection: key = "<pass>|<period>" (pass ∈ instrument/rms/bs,
period ∈ full/train/oos). `bs` is the deployable option-premium truth. Falls
back to a legacy single-axis key, then any available combo, if the exact one
is absent. See scratch/nifty_trend/RESULTS_SCHEMA.md.
"""
import json
import math
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS_DIR = os.path.join(_ROOT, "scratch", "nifty_trend", "runs")

# Small (slug -> (mtime, parsed RESULTS)) cache so switching pass/period or
# narrowing the date range doesn't re-parse the multi-MB results.js each time.
_CACHE = {}
_CACHE_MAX = 3

# Backtest data is IMMUTABLE — once a run's (slug, pass, period) is mapped we
# never need to touch its multi-MB results.js again. This holds the lightweight
# MAPPED result (trade dicts + per-day summary + metrics, NOT the 15MB parsed
# RESULTS), keyed by (slug, pass, period) + file mtime. First access parses;
# every later select/range/combine is instant. Big cap = safe to hold every run.
_RESULT_CACHE = {}
_RESULT_CACHE_MAX = 200


def _fin(v):
    """Non-finite floats (NaN/Inf) -> None so the JSON stays browser-parseable."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def _results_path(slug):
    return os.path.join(RUNS_DIR, slug, "results.js")


def _load_results(slug):
    """Parse runs/<slug>/results.js -> dict, cached by file mtime."""
    path = _results_path(slug)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no results.js for run '{slug}'")
    mtime = os.path.getmtime(path)
    hit = _CACHE.get(slug)
    if hit and hit[0] == mtime:
        return hit[1]
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    # Strip the `window.RESULTS = ` prefix and trailing `;` — body is strict
    # JSON (json.dumps output). json.loads accepts NaN/Infinity by default.
    start = text.index("{")
    end = text.rindex("}")
    data = json.loads(text[start:end + 1])
    if len(_CACHE) >= _CACHE_MAX:
        # drop an arbitrary oldest-ish entry (dict preserves insertion order)
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[slug] = (mtime, data)
    return data


def _pick_combo(data, pass_, period):
    """Return (combo_dict, key_used). Graceful fallback if exact combo absent."""
    combos = data.get("combos", {}) or {}
    want = f"{pass_}|{period}"
    if want in combos:
        return combos[want], want
    # legacy single-axis key (old runs before the 3-pass split)
    if period in combos:
        return combos[period], period
    # any bs|* then any *|full then anything
    for k in (f"bs|{period}", f"{pass_}|full", "bs|full", "full"):
        if k in combos:
            return combos[k], k
    if combos:
        k = next(iter(combos))
        return combos[k], k
    return {}, ""


def list_runs():
    """All available backtest runs from runs/index.json, newest-config first.

    Each -> {slug, label, deployed, significant, tf, instrument, window, days}.
    label = human title (falls back to slug); the dropdown shows this.
    """
    idx_path = os.path.join(RUNS_DIR, "index.json")
    out = []
    try:
        with open(idx_path, "r", encoding="utf-8") as f:
            arr = json.load(f)
    except Exception:
        arr = []
    for e in arr:
        slug = e.get("slug")
        if not slug:
            continue
        # only list runs that actually have a results.js on disk
        if not os.path.isfile(_results_path(slug)):
            continue
        out.append({
            "slug": slug,
            "label": e.get("title") or e.get("design") or slug,
            "deployed": bool(e.get("deployed") or e.get("deploy_key")),
            "deploy_key": e.get("deploy_key") or "",
            "significant": bool(e.get("significant")),
            "tf": e.get("tf") or "",
            "instrument": e.get("instrument") or "",
            "window": e.get("window") or [],
            "days": e.get("days") or 0,
        })
    return out


def _map_trade(t, slug, instr, idx):
    """One results.js all_trades[] row -> a Stats-page trade object."""
    entry_dt = t.get("entry_dt") or ""
    exit_dt = t.get("exit_dt") or entry_dt
    side = t.get("side")
    strike = t.get("strike")
    opt_type = t.get("opt_type")
    # option-premium price if the pass has it (bs), else spot level
    entry_price = t.get("entry_prem")
    exit_price = t.get("exit_prem")
    if entry_price is None:
        entry_price = t.get("entry_spot")
    if exit_price is None:
        exit_price = t.get("exit_spot")
    # BUY/SELL for display + the Stats "Points" calc (pts = BUY? exit-entry :
    # entry-exit). results.js `side` = long(CE) / short(PE) — that's the OPTION
    # DIRECTION, NOT buy-vs-sell, so mapping short->SELL flipped the Points sign
    # for every bought-PE (~39% of trades: gross +ve, points showed -ve). Derive
    # the side from the already-correct gross vs the price move so Points always
    # agrees with Gross in sign (and the badge shows real buy/sell): a long
    # option's gross moves WITH premium (BUY); a sold option's gross moves
    # AGAINST it (SELL).
    gross = t.get("gross")
    prem_move = (exit_price or 0) - (entry_price or 0)
    if gross is not None and prem_move:
        entry_side = "BUY" if (gross * prem_move) >= 0 else "SELL"
    else:
        entry_side = "SELL" if side == "short" else "BUY"
    instr_short = (instr or "IDX").split()[0]
    if strike is not None and opt_type:
        try:
            sym = f"{instr_short} {int(round(float(strike)))}{opt_type}"
        except (TypeError, ValueError):
            sym = f"{instr_short} {strike}{opt_type}"
    else:
        sym = instr_short
    return {
        "id": f"bt-{slug}-{idx}",
        "sym": sym, "symbol": sym,
        "entry": entry_side,
        "qty": t.get("qty") or 0,
        "entry_price": entry_price or 0,
        "exit_price": exit_price or 0,
        "entry_date": entry_dt[:10], "entry_time": entry_dt[11:16],
        "exit_date": exit_dt[:10], "exit_time": exit_dt[11:16],
        "pnl": _fin(t.get("pnl")),
        "gross": _fin(t.get("gross")),
        "fee": _fin(t.get("fee")),
        "points": _fin(t.get("points")),
        "strategy": slug, "mode": "backtest", "source": "backtest",
        "broker": "backtest", "instrument": instr,
        "exit_reason": t.get("reason") or "",
        "tags": [],
        "strike": strike, "opt_type": opt_type,
        "bars": t.get("bars"),
    }


def _full_result(slug, pass_, period):
    """Mapped, UNFILTERED result for one run/pass/period — `{trades, summary,
    metrics, meta}`. Cached by file mtime (bt data is immutable → computed once,
    then reused instantly for every later select / range / combine)."""
    path = _results_path(slug)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"no results.js for run '{slug}'")
    mtime = os.path.getmtime(path)
    ck = (slug, pass_, period)
    hit = _RESULT_CACHE.get(ck)
    if hit and hit[0] == mtime:
        return hit[1]

    data = _load_results(slug)
    meta = data.get("meta", {}) or {}
    instr = meta.get("instrument") or ""
    combo, key_used = _pick_combo(data, pass_, period)
    all_trades = combo.get("all_trades") or []

    trades, summary = [], {}
    for i, t in enumerate(all_trades):
        row = _map_trade(t, slug, instr, i)
        d = row["entry_date"]
        if not d:
            continue
        trades.append(row)
        b = summary.setdefault(d, {"pnl": 0.0, "count": 0})
        b["pnl"] += (row["pnl"] or 0)
        b["count"] += 1
    for d in summary:
        summary[d]["pnl"] = round(summary[d]["pnl"], 2)

    m = combo.get("metrics", {}) or {}
    metrics = {
        "profit_factor": _fin(m.get("profit_factor")),
        "expectancy": _fin(m.get("expectancy")),
        "sharpe": _fin(m.get("sharpe")),
        "win_rate": _fin(m.get("win_rate")),
        "n_trades": m.get("trades"),
    }
    full = {
        "trades": trades, "summary": summary, "metrics": metrics,
        "meta": {
            "slug": slug,
            "label": meta.get("design") or slug,
            "instrument": instr,
            "tf": meta.get("tf") or "",
            "window": meta.get("window") or [],
            "days": meta.get("days") or 0,
            "pass": pass_, "period": period,
            "combo_key_used": key_used,
            "passes": meta.get("passes") or [],
            "periods": meta.get("periods") or [],
            "lot_size": meta.get("lot_size"), "lots": meta.get("lots"),
        },
    }
    if len(_RESULT_CACHE) >= _RESULT_CACHE_MAX:
        _RESULT_CACHE.pop(next(iter(_RESULT_CACHE)))
    _RESULT_CACHE[ck] = (mtime, full)
    return full


def calendar_summary(slug, pass_="bs", period="full", from_date=None, to_date=None):
    """`{summary, trades, filters, metrics, meta}` — same shape as the live
    calendar-summary route, built from one backtest combo's all_trades.

    summary  : { "YYYY-MM-DD": {pnl, count} }  bucketed by trade ENTRY date
    trades   : mapped trade objects (all fields the Stats tables read)
    metrics  : the run's OWN computed report card for this pass/period
    meta     : run label, window, tf, instrument, which combo key was used
    """
    full = _full_result(slug, pass_, period)
    if not (from_date or to_date):
        # whole run — shallow copies so a caller can't mutate the cache
        return {
            "summary": dict(full["summary"]),
            "trades": list(full["trades"]),
            "filters": {"strategy": [], "broker": []},
            "metrics": full["metrics"],
            "meta": full["meta"],
        }
    trades = [t for t in full["trades"]
              if (not from_date or t["entry_date"] >= from_date)
              and (not to_date or t["entry_date"] <= to_date)]
    summary = {}
    for t in trades:
        d = t["entry_date"]
        b = summary.setdefault(d, {"pnl": 0.0, "count": 0})
        b["pnl"] += (t["pnl"] or 0)
        b["count"] += 1
    for d in summary:
        summary[d]["pnl"] = round(summary[d]["pnl"], 2)
    return {
        "summary": summary,
        "trades": trades,
        "filters": {"strategy": [], "broker": []},
        "metrics": full["metrics"],
        "meta": full["meta"],
    }


def combined_summary(slugs, pass_="bs", period="full", from_date=None, to_date=None):
    """Portfolio-style COMBINE of multiple backtest runs. Unions each run's
    all_trades (each already tagged strategy=<slug> so Total Summary's Strategy
    mode breaks it down per-run) and buckets the per-day summary across all.
    Same {summary, trades, filters, metrics, meta} shape as a single run — so
    the Stats calendar renders a multi-run backtest exactly like one run.

    Metrics are computed from the combined trades (PF/expectancy/win-rate/N;
    Sharpe left None — per-run Sharpes don't sum, and a combined-daily Sharpe
    would need a shared calendar we don't assume here)."""
    slugs = [s for s in (slugs or []) if s]
    trades, summary, labels, missing = [], {}, [], []
    for slug in slugs:
        try:
            d = calendar_summary(slug, pass_=pass_, period=period,
                                 from_date=from_date, to_date=to_date)
        except Exception as e:
            missing.append(slug)
            print("[backtest combined] skip", slug, e, flush=True)
            continue
        labels.append(d["meta"].get("label") or slug)
        trades.extend(d["trades"])
        for dt, b in d["summary"].items():
            bb = summary.setdefault(dt, {"pnl": 0.0, "count": 0})
            bb["pnl"] += b["pnl"]; bb["count"] += b["count"]
    for dt in summary:
        summary[dt]["pnl"] = round(summary[dt]["pnl"], 2)

    g_win = g_loss = net = 0.0
    wins = n = 0
    for t in trades:
        p = t.get("pnl") or 0
        n += 1; net += p
        if p > 0:
            g_win += p; wins += 1
        elif p < 0:
            g_loss += -p
    metrics = {
        "profit_factor": round(g_win / g_loss, 3) if g_loss > 0 else None,
        "expectancy": round(net / n, 2) if n else None,
        "sharpe": None,
        "win_rate": round(wins / n * 100, 2) if n else None,
        "n_trades": n,
    }
    return {
        "summary": summary,
        "trades": trades,
        "filters": {"strategy": list(slugs), "broker": []},
        "metrics": metrics,
        "meta": {
            "combined": True, "slugs": list(slugs), "missing": missing,
            "label": f"{len(slugs)} runs combined",
            "pass": pass_, "period": period,
            "combo_key_used": f"{pass_}|{period}",
        },
    }
