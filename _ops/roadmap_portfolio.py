"""
roadmap_portfolio.py — PORTFOLIO-level forward projection (display-only, no order/risk path).

Ek hi sawaal ka jawab: "aaj se <date> tak kitna paisa expect kar sakta hoon?"
— aur jawab HAMESHA ek band ke saath, kyunki ek akela number chhoti window me jhooth hai.

Kaise:
  • har member strategy ka REAL backtest per-trade net (runs/<slug>/results.js ka bs|full
    combo — real premium + date-aware Zerodha charges + DOM slip) uske apne lot-count se
    divide karke PER-LOT daily series banti hai (exit-date pe bucket, TRAP #141 shape),
  • portfolio ke liye saare members ka EK HI calendar-day matrix banta hai aur block-bootstrap
    us matrix ki ROWS pe hota hai → correlation apne aap aa jaati hai (teen short-vol
    strategies ek hi vol-spike me saath girti hain; independent-sum ye chhupa deta hai),
  • percentiles p5/p25/p50/p75/p95 + P(loss) + P(>=target) us distribution se.

Rule 6B reuse: backtest_calendar._load_results (mtime-cached results.js reader),
registry_economics.economics (per-lot capital / lots_in_run / lot_size),
roadmap.actual_equity (live actual, resolve-aware), strategy_registry (labels).

Rule 10 honesty: ye projection maanti hai ki backtest ka edge aage bhi rahega. Wo maan
lena hi sabse bada risk hai — isliye har payload me `caveats` jaata hai aur blocked
strategies (jinka backtest hi bharose layak nahi) projection se BAHAR rehti hain.
"""
import os
import sys
import json
import math
import random
import datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "scratch", "nifty_trend"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import _paths  # noqa: F401
except Exception:
    pass

import backtest_calendar as _bc            # noqa: E402  results.js reader (mtime-cached)

try:
    import registry_economics as _econ      # noqa: E402
except Exception:
    _econ = None
try:
    import strategy_registry as _reg        # noqa: E402
except Exception:
    _reg = None
try:
    import roadmap as _rm                   # noqa: E402  (per-strategy engine: actual_equity)
except Exception:
    _rm = None
try:
    from market_calendar import is_trading_day, ist_now
except Exception:                            # pragma: no cover - fallback only
    def ist_now():
        return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)

    def is_trading_day(d):
        return d.weekday() < 5

CFG_PATH = os.path.join(_ROOT, "data", "roadmap_portfolio.json")
NIFTY_CFG = os.path.join(_ROOT, "nifty_config.json")

BLOCK = 5          # bootstrap block = 5 calendar rows (streaks/vol-clusters survive)
PATHS = 4000       # bootstrap paths
_SEED = 11         # deterministic → same inputs, same numbers (page refresh pe na hile)

_SERIES_CACHE = {}   # slug -> (mtime, payload)


# ─────────────────────────────────────────────────────────── config / membership
def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def load_cfg():
    """data/roadmap_portfolio.json — membership + book + blocked list."""
    return _read_json(CFG_PATH, {"members": [], "blocked": {}})


def _nifty_cfg():
    return _read_json(NIFTY_CFG, {})


LOT_FIELDS = ("lots", "qty")


def _live_state(config_key, ncfg):
    """Us strategy ka ABHI ka runtime state config se: lots / mode / on."""
    c = ncfg.get(config_key) or {}
    lots, field = None, None
    for f in LOT_FIELDS:
        if isinstance(c.get(f), (int, float)):
            lots, field = int(c[f]), f
            break
    mode = str(c.get("mode") or "paper").lower()
    on = bool(c.get("active", c.get("enabled", False)))
    return {"lots": lots, "lots_field": field, "mode": mode, "on": on,
            "configured": bool(c)}


def members(include_blocked=True):
    """Har member ka merged view: config + registry label + live runtime state."""
    cfg = load_cfg()
    ncfg = _nifty_cfg()
    blocked = cfg.get("blocked") or {}
    out = []
    for m in (cfg.get("members") or []):
        sid = m.get("id")
        block_reason = blocked.get(sid) or blocked.get(m.get("config_key") or "")
        if block_reason and not include_blocked:
            continue
        label = m.get("label")
        if not label and _reg is not None:
            try:
                label = _reg.label(m.get("config_key") or sid)
            except Exception:
                label = None
        row = dict(m)
        row["label"] = label or sid
        row["live"] = _live_state(m.get("config_key") or "", ncfg)
        row["blocked"] = block_reason or None
        row["enabled"] = bool(m.get("enabled", True)) and not block_reason
        out.append(row)
    return out


# ─────────────────────────────────────────────────────────── per-lot trade series
def per_lot_series(slug, run_lots=None):
    """
    {dates: [iso], nets: [per-lot net Rs], meta: {...}} — backtest ke apne trades se,
    EXIT-date pe bucket (multi-day hold sahi din pe bethe, TRAP #141 shape).

    Per-lot = trade ka net / us run ke lots. NOTE: run ka net me brokerage FLAT hissa
    (Rs20/order) bhi shamil hai, jo lots ke saath scale nahi karta — isliye per-lot
    number thoda CONSERVATIVE hai (zyada lots pe asli net per-lot isse behtar hota hai).
    Conservative direction jaan-boojh ke chuni hai.
    """
    try:
        mt = _bc._results_mtime(slug) if hasattr(_bc, "_results_mtime") else None
    except Exception:
        mt = None
    if mt is None:
        try:
            mt = os.path.getmtime(os.path.join(
                _ROOT, "scratch", "nifty_trend", "runs", slug, "results.js"))
        except Exception:
            mt = 0
    hit = _SERIES_CACHE.get(slug)
    if hit and hit[0] == mt:
        return hit[1]

    res = _bc._load_results(slug) or {}
    combos = res.get("combos") or {}
    key = "bs|full" if "bs|full" in combos else next(
        (k for k in combos if k.startswith("bs")), None) or next(iter(combos), None)
    trades = ((combos.get(key) or {}).get("all_trades")) or [] if key else []

    lots = run_lots
    if not lots and _econ is not None:
        try:
            lots = (_econ.economics(slug) or {}).get("lots_in_run")
        except Exception:
            lots = None
    lots = int(lots or 1) or 1

    dates, nets = [], []
    for t in trades:
        xd = str(t.get("exit_dt") or t.get("entry_dt") or "")[:10]
        if not xd:
            continue
        try:
            net = float(t.get("pnl") or 0.0) / lots
        except Exception:
            continue
        dates.append(xd)
        nets.append(net)

    span_days = 0
    if dates:
        try:
            d0 = dt.date.fromisoformat(min(dates))
            d1 = dt.date.fromisoformat(max(dates))
            span_days = max(1, (d1 - d0).days)
        except Exception:
            span_days = 1

    cap = None
    lot_size = None
    if _econ is not None:
        try:
            e = _econ.economics(slug) or {}
            cap = e.get("capital_per_lot")
            lot_size = e.get("lot_size")
        except Exception:
            pass

    n = len(nets)
    payload = {
        "dates": dates, "nets": nets,
        "meta": {
            "slug": slug, "combo": key, "run_lots": lots, "trades": n,
            "span_days": span_days,
            "first": min(dates) if dates else None, "last": max(dates) if dates else None,
            "avg_per_lot": round(sum(nets) / n, 2) if n else 0.0,
            "trades_per_day": (n / span_days) if span_days else 0.0,
            "worst_trade": round(min(nets), 2) if n else 0.0,
            "capital_per_lot": cap, "lot_size": lot_size,
        },
    }
    _SERIES_CACHE[slug] = (mt, payload)
    return payload


def _daily_map(slug, run_lots=None):
    """{iso date: per-lot net} — ek din ke multiple trades jud jaate hain."""
    s = per_lot_series(slug, run_lots)
    d = {}
    for iso, net in zip(s["dates"], s["nets"]):
        d[iso] = d.get(iso, 0.0) + net
    return d, s["meta"]


# ─────────────────────────────────────────────────────────── projection core
def trading_days_between(d0, d1):
    """Aaj (exclusive) se target date (inclusive) tak ke asli trading din (NSE calendar)."""
    if isinstance(d0, str):
        d0 = dt.date.fromisoformat(d0)
    if isinstance(d1, str):
        d1 = dt.date.fromisoformat(d1)
    n, cur = 0, d0 + dt.timedelta(days=1)
    while cur <= d1:
        try:
            if is_trading_day(cur):
                n += 1
        except Exception:
            if cur.weekday() < 5:
                n += 1
        cur += dt.timedelta(days=1)
    return n


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    i = int(p * (len(sorted_vals) - 1))
    return sorted_vals[i]


def simulate_per_lot(specs, n_days, paths=PATHS, seed=_SEED):
    """
    Bootstrap ek baar — har member ka PER-LOT path-total.
    Returns {"sims": [[float]*paths per member], "metas": [...], "window": [lo,hi],
             "independent_window": bool}

    Ye alag isliye hai ki goal-planner ko ek hi simulation pe SAIKDON lot-combos
    evaluate karne hote hain: kisi bhi lot vector ka distribution = Σ lots[j]*sims[j].
    Ek hi bootstrap = solver fast AUR projection se bilkul consistent (do alag
    "sach" nahi bante — TRAP #98 ka sabak).
    """
    specs = [s for s in specs if s.get("slug")]
    if not specs or n_days <= 0:
        return None

    maps, metas = [], []
    for s in specs:
        m, meta = _daily_map(s["slug"], s.get("run_lots"))
        maps.append(m)
        metas.append(meta)

    # common window = jahan SAB members ka data ho (warna ek member ka gap doosre ko
    # zero-return din de dega = jhooti smoothness)
    firsts = [m["first"] for m in metas if m.get("first")]
    lasts = [m["last"] for m in metas if m.get("last")]
    if not firsts or not lasts:
        return None
    lo, hi = max(firsts), min(lasts)
    if lo >= hi:
        # overlap nahi — har member apni poori history pe INDEPENDENT (band chaudi rakho)
        lo, hi = min(firsts), max(lasts)
        independent = True
    else:
        independent = False

    days = []
    cur, end = dt.date.fromisoformat(lo), dt.date.fromisoformat(hi)
    while cur <= end:
        try:
            trading = is_trading_day(cur)
        except Exception:
            trading = cur.weekday() < 5
        if trading:
            days.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    if len(days) <= BLOCK + 1:
        return None

    matrix = [[m.get(d, 0.0) for m in maps] for d in days]
    nrow = len(matrix)
    nm = len(specs)

    rng = random.Random(seed)
    # daily[j][p][d] = member j ka per-lot return, path p, din d.
    # Per-DIN store karna zaroori hai (sirf total nahi) — kyunki max-drawdown path ke
    # andar ka number hai; terminal p5 se DD naapna use SYSTEMATICALLY kam dikhata hai.
    daily = [[None] * paths for _ in range(nm)]
    sims = [[0.0] * paths for _ in range(nm)]
    for p in range(paths):
        seq = [[0.0] * n_days for _ in range(nm)]
        k = 0
        while k < n_days:
            st = rng.randrange(0, nrow - BLOCK)
            for row in matrix[st:st + BLOCK]:
                if k >= n_days:
                    break
                for j in range(nm):
                    seq[j][k] = row[j]
                k += 1
        for j in range(nm):
            daily[j][p] = seq[j]
            sims[j][p] = sum(seq[j])

    return {"sims": sims, "daily": daily, "metas": metas, "specs": specs,
            "n_days": n_days, "paths": paths, "window": [lo, hi],
            "independent_window": independent}


def evaluate(sim, lots, target=None, with_dd=True, with_curve=False):
    """
    Kisi bhi lot-vector ka distribution — SAME bootstrap se (dobara simulate nahi).
    with_dd=True pe har path ka **max drawdown** (peak-to-trough) bhi nikalta hai —
    yahi asli "kitna neeche ja sakta hai" hai; terminal p5 usse hamesha chhota hota hai.
    """
    if not sim:
        return None
    sims, paths = sim["sims"], sim["paths"]
    nm = len(sims)
    lots = [int(lots[j] or 0) for j in range(nm)]
    active = [j for j in range(nm) if lots[j]]
    totals = [0.0] * paths
    mdds = [0.0] * paths if with_dd else None

    curve_cum = None
    if (with_dd or with_curve) and active and sim.get("daily"):
        nd = sim["n_days"]
        dly = sim["daily"]
        if with_curve:
            curve_cum = [[0.0] * paths for _ in range(nd)]
        for p in range(paths):
            eq = peak = mdd = 0.0
            for d in range(nd):
                step = 0.0
                for j in active:
                    step += dly[j][p][d] * lots[j]
                eq += step
                if curve_cum is not None:
                    curve_cum[d][p] = eq
                if eq > peak:
                    peak = eq
                dd = peak - eq
                if dd > mdd:
                    mdd = dd
            totals[p] = eq
            mdds[p] = mdd
    else:
        for j in active:
            L, col = lots[j], sims[j]
            for p in range(paths):
                totals[p] += col[p] * L

    st = sorted(totals)
    out = {
        "n_days": sim["n_days"], "paths": paths,
        "p5": round(_pct(st, .05)), "p25": round(_pct(st, .25)),
        "p50": round(_pct(st, .50)), "p75": round(_pct(st, .75)),
        "p95": round(_pct(st, .95)),
        "mean": round(sum(st) / len(st)),
        "p_loss": round(100.0 * sum(1 for v in st if v < 0) / len(st), 1),
        "independent_window": sim["independent_window"], "window": sim["window"],
        "members": [],
    }
    if curve_cum is not None:
        # per-din corridor — chart isi se banta hai (koi interpolation/fabrication nahi)
        cur = {"p5": [], "p25": [], "p50": [], "p75": [], "p95": []}
        for d in range(sim["n_days"]):
            col = sorted(curve_cum[d])
            cur["p5"].append(round(_pct(col, .05)))
            cur["p25"].append(round(_pct(col, .25)))
            cur["p50"].append(round(_pct(col, .50)))
            cur["p75"].append(round(_pct(col, .75)))
            cur["p95"].append(round(_pct(col, .95)))
        out["curve"] = cur
    if mdds is not None and active:
        sd = sorted(mdds)
        out["maxdd_median"] = round(_pct(sd, .50))
        out["maxdd_p95"] = round(_pct(sd, .95))   # 1-in-20 bura drawdown = risk budget
        out["maxdd_worst"] = round(sd[-1])
    if target is not None:
        out["target"] = round(float(target))
        out["p_target"] = round(
            100.0 * sum(1 for v in st if v >= float(target)) / len(st), 1)
    for j, s in enumerate(sim["specs"]):
        if not lots[j]:
            continue
        col = sorted(v * lots[j] for v in sims[j])
        meta = sim["metas"][j]
        out["members"].append({
            "id": s.get("id"), "label": s.get("label"), "slug": s["slug"],
            "lots": lots[j],
            "p5": round(_pct(col, .05)), "p25": round(_pct(col, .25)),
            "p50": round(_pct(col, .50)), "p75": round(_pct(col, .75)),
            "p95": round(_pct(col, .95)),
            "p_loss": round(100.0 * sum(1 for v in col if v < 0) / len(col), 1),
            "trades": meta["trades"],
            "worst_trade_per_lot": meta["worst_trade"],
            "capital_per_lot": meta["capital_per_lot"],
            "backtest_window": [meta["first"], meta["last"]],
        })
    out["sum_of_medians"] = round(sum(m["p50"] for m in out["members"]))
    return out


def project(specs, n_days, paths=PATHS, seed=_SEED, target=None, with_curve=True):
    """specs me har member ka `lots` — simulate + evaluate ka thin wrapper."""
    specs = [s for s in specs if s.get("lots")]
    sim = simulate_per_lot(specs, n_days, paths, seed)
    if not sim:
        return None
    return evaluate(sim, [s["lots"] for s in specs], target=target, with_curve=with_curve)


# ─────────────────────────────────────────────────────────── page payload
def _actual_for(member):
    """Live actual P&L (jo sach me hua) — roadmap.actual_equity reuse."""
    if _rm is None:
        return None
    ck = member.get("config_key")
    start = member.get("start_date")
    if not ck or not start:
        return None
    try:
        act = _rm.actual_equity(ck, start, 0.0)
        return {"net": act.get("net"), "trades": act.get("trades"),
                "wins": act.get("wins"), "last_date": act.get("last_date"),
                "points": act.get("points") or []}
    except Exception:
        return None


def build(to_date=None, lane="real", lots_mode="live", lots_override=None, target=None):
    """
    Page payload.
      lane: "real" | "all"   (real = sirf mode==live members; all = real+paper)
      lots_mode: "live" (config ke abhi ke lots) | "plan" (active plan ke lots)
      lots_override: {id: lots} — planner ke preview ke liye
    """
    today = ist_now().date()
    if not to_date:
        to_date = (today + dt.timedelta(days=30)).isoformat()
    n_days = trading_days_between(today, to_date)

    ms = members(include_blocked=True)
    lanes = {}
    rows = []
    caveats = []

    plan_lots = {}
    if lots_mode == "plan":
        try:
            import goal_planner as _gp
            plan = _gp.active_plan()
            plan_lots = {k: v for k, v in ((plan or {}).get("lots") or {}).items()}
        except Exception:
            plan_lots = {}

    def lots_for(m):
        if lots_override and m["id"] in lots_override:
            return int(lots_override[m["id"]] or 0)
        if plan_lots and m["id"] in plan_lots:
            return int(plan_lots[m["id"]] or 0)
        return int((m["live"].get("lots") or 0))

    for m in ms:
        row = {
            "id": m["id"], "label": m["label"], "slug": m.get("slug"),
            "config_key": m.get("config_key"), "book": m.get("book"),
            "mode": m["live"].get("mode"), "on": m["live"].get("on"),
            "lots": lots_for(m), "blocked": m.get("blocked"),
            "actual": _actual_for(m),
        }
        try:
            meta = per_lot_series(m["slug"], m.get("run_lots"))["meta"]
            row["backtest"] = {
                "trades": meta["trades"], "window": [meta["first"], meta["last"]],
                "avg_per_lot": meta["avg_per_lot"],
                "worst_trade_per_lot": meta["worst_trade"],
                "capital_per_lot": meta["capital_per_lot"],
                "lot_size": meta["lot_size"],
            }
        except Exception:
            row["backtest"] = None
        rows.append(row)
        if m.get("blocked"):
            caveats.append(f"{m['id']} {m['label']}: {m['blocked']}")

    def specs_for(pred):
        out = []
        for m, r in zip(ms, rows):
            if m.get("blocked") or not m.get("enabled"):
                continue
            if not r["lots"]:
                continue
            if not pred(r):
                continue
            out.append({"id": m["id"], "label": m["label"], "slug": m["slug"],
                        "run_lots": m.get("run_lots"), "lots": r["lots"]})
        return out

    lanes["real"] = project(specs_for(lambda r: r["mode"] == "live"), n_days, target=target)
    lanes["all"] = project(specs_for(lambda r: True), n_days, target=target)

    active = lanes.get(lane) or lanes.get("all")
    if active:
        bymember = {x["id"]: x for x in active["members"]}
        for r in rows:
            r["proj"] = bymember.get(r["id"])

    book_real = sum((r.get("book") or 0) for r in rows
                    if r["mode"] == "live" and not r["blocked"] and r["lots"])
    book_all = sum((r.get("book") or 0) for r in rows
                   if not r["blocked"] and r["lots"])

    caveats.append("Projection maanti hai ki backtest ka edge aage bhi rahega — "
                   "ye assumption hi sabse bada risk hai.")
    if lanes.get("all") and lanes["all"].get("independent_window"):
        caveats.append("Members ka backtest window overlap nahi karta — band chaudi hai "
                       "(correlation naapi nahi ja saki).")
    caveats.append("Paper lane me broker rejection / slippage / fill-quality modelled nahi hai.")

    return {
        "today": today.isoformat(), "to_date": to_date, "n_days": n_days,
        "lane": lane, "lots_mode": lots_mode,
        "lanes": lanes, "rows": rows,
        "book": {"real": book_real, "all": book_all},
        "caveats": caveats,
        "generated": ist_now().strftime("%Y-%m-%d %H:%M"),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", default=None, help="target date YYYY-MM-DD")
    ap.add_argument("--lane", default="all", choices=("real", "all"))
    a = ap.parse_args()
    print(json.dumps(build(a.to, a.lane), indent=1, default=str))
