"""
roadmap.py — per-strategy LIVE growth tracker (display-only, no order/risk path).

For a deployed strategy it:
  • reads the strategy's ACTUAL net P&L (order_store — same net as the dashboard:
    gross − date-aware charges, exit-date bucketed, resolve-aware),
  • overlays it on the stored Monte-Carlo corridor (p5/p25/median/p75/p95),
  • auto-computes: on-track / ahead / behind, whether any ACTION is needed, when
    to add the next lot (equity threshold + expected date), and the capital plan.

One glance → "kya chal raha, aage kya, peeche kya". Every strategy that passes the
gate gets its own config row + corridor file → its own page, same engine.

Reuses (Rule 6B): order_store.trades_for_range, charges.option_charges,
strategy_registry.resolve/label. Config: data/roadmap_config.json.
Corridor: data/roadmap/<corridor>.json (produced by scratch/weekly_ironfly/roadmap_mc.py).
"""
import os, sys, json, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    import _paths  # noqa
except Exception:
    pass

import order_store
import strategy_registry as _reg
try:
    import charges as _charges
except Exception:
    _charges = None
try:
    from market_calendar import ist_now
except Exception:
    def ist_now():
        return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)

CFG_PATH = os.path.join(ROOT, "data", "roadmap_config.json")
CORR_DIR = os.path.join(ROOT, "data", "roadmap")
DAYS_PER_MONTH = 30.44


# ───────────────────────────────────────────────────────── config / corridor
def _load_cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def list_strategies():
    """[(sid, label)] for the picker — configured + deployed order."""
    cfg = _load_cfg()
    out = []
    for sid, c in cfg.items():
        try:
            lbl = c.get("label") or _reg.label(sid)
        except Exception:
            lbl = sid
        out.append({"id": sid, "label": lbl})
    return out


def _load_corridor(name):
    try:
        with open(os.path.join(CORR_DIR, name + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ───────────────────────────────────────────────────────── actual P&L
def _tax(t):
    if _charges is None:
        return round(abs(t.get("pnl", 0)) * 0.0, 2)
    try:
        ep = float(t.get("entry_price") or t.get("avg_price") or 0)
        xp = float(t.get("exit_price") or 0)
        qty = int(float(t.get("qty") or 0))
        side = "BUY" if str(t.get("entry", "")).upper() == "BUY" else "SELL"
        when = t.get("exit_date") or t.get("entry_date")
        return round(_charges.option_charges(ep, xp, qty, entry_side=side, when=when), 2)
    except Exception:
        return 0.0


def actual_equity(sid, start_date, book):
    """Cumulative equity curve (book + running net) from start_date → today.
    Same net as the dashboard: gross(pnl) − date-aware charges, exit-date
    bucketed, netted over a 400-day lookback so positional legs pair (TRAP #141).
    Returns dict: points=[{date,equity,day_net}], equity, net, trades, wins, last_date."""
    today = ist_now().date().isoformat()
    lo = (dt.date.fromisoformat(start_date) - dt.timedelta(days=400)).isoformat()
    try:
        rng = order_store.trades_for_range(lo, today)
    except Exception:
        rng = {"details": []}
    match = _matcher(sid)
    by_day = {}
    trades = wins = 0
    for t in (rng.get("details") or []):
        xd = t.get("exit_date")
        if not xd or xd < start_date or xd > today:
            continue
        if not match(t.get("strategy")):
            continue
        net = round((t.get("pnl") or 0.0) - _tax(t), 2)
        by_day[xd] = round(by_day.get(xd, 0.0) + net, 2)
        trades += 1
        wins += 1 if net > 0 else 0
    pts, eq = [], float(book)
    for d in sorted(by_day):
        eq = round(eq + by_day[d], 2)
        pts.append({"date": d, "equity": eq, "day_net": by_day[d]})
    return {"points": pts, "equity": eq, "net": round(eq - book, 2),
            "trades": trades, "wins": wins,
            "last_date": pts[-1]["date"] if pts else start_date}


def _matcher(want):
    """resolve-aware match (same identity as Stats)."""
    try:
        wid = _reg.resolve(want)
    except Exception:
        wid = None
    wl = str(want or "").lower()
    cache = {}

    def m(raw):
        h = cache.get(raw)
        if h is None:
            rs = str(raw or "")
            h = (rs == want) or (rs.lower() == wl)
            if not h and wid is not None:
                try:
                    h = _reg.resolve(rs) == wid
                except Exception:
                    h = False
            cache[raw] = h
        return h
    return m


# ───────────────────────────────────────────────────────── sizing / interp
def lot_step(cfg):
    util = float(cfg.get("util_pct", 30)) / 100.0
    return cfg.get("margin_per_lot", 64400) / util if util else 1e12


def lots_at(equity, cfg):
    book = cfg.get("book", 1400000)
    step = lot_step(cfg)
    start = cfg.get("start_lots", 5)
    worst = cfg.get("worst_per_lot", 7129)
    cap = cfg.get("capacity_lots", 40)
    risk = float(cfg.get("risk_pct", 8)) / 100.0
    by_step = start + int((equity - book) // step)
    by_risk = int(equity * risk / worst) if worst else cap
    return max(1, min(by_step, by_risk, cap))


def next_lot_equity(equity, cfg):
    """Equity at which lots go current → current+1."""
    book = cfg.get("book", 1400000)
    step = lot_step(cfg)
    start = cfg.get("start_lots", 5)
    cur = lots_at(equity, cfg)
    # equity that makes by_step == cur+1
    return round(book + (cur - start + 1) * step)


def _interp(arr, midx):
    if not arr:
        return None
    if midx <= 0:
        return arr[0]
    if midx >= len(arr) - 1:
        return arr[-1]
    lo = int(midx)
    frac = midx - lo
    return arr[lo] + (arr[lo + 1] - arr[lo]) * frac


def month_idx(start_date, on=None):
    d0 = dt.date.fromisoformat(start_date)
    d1 = (on or ist_now().date())
    if isinstance(d1, str):
        d1 = dt.date.fromisoformat(d1)
    return max(0.0, (d1 - d0).days / DAYS_PER_MONTH)


def _month_median_crosses(corr, target_L, from_midx):
    """First fractional month (≥ from_midx) where the MEDIAN reaches target
    (in ₹L). Returns month or None."""
    p50 = corr.get("p50") or []
    for m in range(len(p50)):
        if m < from_midx:
            continue
        if p50[m] >= target_L:
            return m
    return None


# ───────────────────────────────────────────────────────── status engine
def status(sid, cfg, corr, act):
    L = 1e5
    book = cfg.get("book", 1400000)
    start = cfg.get("start_date")
    midx = month_idx(start)
    eq = act["equity"]
    eqL = eq / L

    med = _interp(corr.get("p50"), midx)
    p5 = _interp(corr.get("p5"), midx)
    p95 = _interp(corr.get("p95"), midx)
    # p25/p75 midpoints (same as the chart)
    p25 = med - (med - p5) * 0.5 if (med is not None and p5 is not None) else None
    p75 = med + (p95 - med) * 0.5 if (med is not None and p95 is not None) else None

    # band + verdict
    spread = (p75 - p25) if (p75 is not None and p25 is not None) else 0
    if act["trades"] == 0 or spread < 0.10:
        band, verdict, action = "on-track", "Abhi shuru hui — on track. Pehle trades ka intezaar.", "hold"
    elif p75 is not None and eqL >= p75:
        band, verdict, action = "ahead", "Aage ho — good-luck path pe. Kuch mat badlo.", "hold"
    elif p25 is not None and eqL >= p25:
        band, verdict, action = "on-track", "On track — kuch karne ki zaroorat nahi.", "hold"
    elif p5 is not None and eqL >= p5:
        band, verdict, action = "behind", "Median se neeche par bad-luck band ke andar — normal hai, rule pe raho.", "hold"
    else:
        band, verdict, action = "alert", "p5 (bad-luck floor) se neeche — bad-luck streak YA edge-decay. Engine re-validate karo, blindly add mat karo.", "review"

    # lot scaling
    cur_lots = lots_at(eq, cfg)
    nxt_eq = next_lot_equity(eq, cfg)
    cap = cfg.get("capacity_lots", 40)
    lot_msg, lot_date = None, None
    if cur_lots >= cap:
        lot_msg = f"Capacity cap ({cap} lots) — aur lot mat badhao."
    else:
        gap = nxt_eq - eq
        if gap <= 0:
            action = "add_lot" if action == "hold" else action
            lot_msg = f"ABHI +1 lot: equity ₹{nxt_eq:,.0f} cross ho chuki → {cur_lots+1} lots karo."
        else:
            # expected date when median reaches nxt_eq
            m = _month_median_crosses(corr, nxt_eq / L, midx)
            if m is not None:
                lot_date = (dt.date.fromisoformat(start) + dt.timedelta(days=int(m * DAYS_PER_MONTH))).isoformat()
            lot_msg = (f"Agla lot ({cur_lots}→{cur_lots+1}) equity ₹{nxt_eq:,.0f} pe "
                       f"(abhi se ₹{gap:,.0f} door)"
                       + (f" — median pe ~{lot_date} tak." if lot_date else "."))

    # capital plan
    sip = cfg.get("sip_per_month", 0)
    if sip and sip > 0:
        n_done = int(midx)
        nxt_sip_date = (dt.date.fromisoformat(start) + dt.timedelta(days=int((n_done + 1) * DAYS_PER_MONTH))).isoformat()
        contributed = book + sip * n_done
        cap_msg = (f"SIP ON: ₹{sip:,.0f}/mo. Ab tak ₹{contributed:,.0f} daala; "
                   f"agla ₹{sip:,.0f} ~{nxt_sip_date} tak.")
        cap_action = f"Add ₹{sip:,.0f} by {nxt_sip_date}"
    else:
        cap_msg = "Profit-only: koi capital daalne ki majboori nahi — sizing rule khud lots ghatata hai giravat me."
        cap_action = None

    return {
        "month_idx": round(midx, 2), "days": int(midx * DAYS_PER_MONTH),
        "equity": eq, "net": act["net"], "trades": act["trades"], "wins": act["wins"],
        "expected_median": round(med, 2) if med is not None else None,
        "p5": round(p5, 2) if p5 is not None else None,
        "p25": round(p25, 2) if p25 is not None else None,
        "p75": round(p75, 2) if p75 is not None else None,
        "p95": round(p95, 2) if p95 is not None else None,
        "vs_median": round(eqL - med, 2) if med is not None else None,
        "band": band, "verdict": verdict, "action": action,
        "cur_lots": cur_lots, "next_lot_equity": nxt_eq, "next_lot_date": lot_date,
        "lot_msg": lot_msg, "cap_msg": cap_msg, "cap_action": cap_action,
    }


def _active_track(cfg):
    r = int(cfg.get("risk_pct", 8))
    sip = cfg.get("sip_per_month", 0)
    return f"r{r}_{'sip' if sip and sip > 0 else 'profit'}"


def build(sid):
    cfg = _load_cfg().get(sid)
    if not cfg:
        return {"error": "not configured", "id": sid}
    try:
        lbl = cfg.get("label") or _reg.label(sid)
    except Exception:
        lbl = sid
    corr_all = _load_corridor(cfg.get("corridor", sid))
    if not corr_all:
        return {"error": "no corridor", "id": sid, "label": lbl}
    track = _active_track(cfg)
    corr = (corr_all.get("runs") or {}).get(track)
    if not corr:
        return {"error": f"track {track} missing", "id": sid, "label": lbl}
    corr = {k: corr[k] for k in ("p5", "p50", "p95", "lots50", "cagr", "lot_step",
                                 "maxdd_median", "maxdd_p5") if k in corr}
    act = actual_equity(sid, cfg["start_date"], cfg.get("book", 1400000))
    st = status(sid, cfg, corr, act)
    return {
        "id": sid, "label": lbl, "track": track,
        "cfg": {k: cfg.get(k) for k in ("start_date", "book", "start_lots", "risk_pct",
                                        "margin_per_lot", "capacity_lots", "worst_per_lot",
                                        "sip_per_month", "util_pct")},
        "meta": corr_all.get("source") or {},
        "per_lot": corr_all.get("per_lot") or {},
        "corridor": corr, "actual": act, "status": st,
        "lot_step": round(lot_step(cfg)),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", default="weekly_ironfly_v1")
    a = ap.parse_args()
    print(json.dumps(build(a.id), indent=1, default=str))
