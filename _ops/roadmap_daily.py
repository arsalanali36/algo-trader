"""
roadmap_daily.py — active plan ka ROZ ka report card (display-only, no order/risk path).

Ek line ka kaam: "aaj expected kitna tha, actual kitna hua, aur ab tak ka safar corridor
ke andar hai ya nahi" — taaki roz ke P&L ko dekh ke ghabrahat na ho aur asli problem
(edge decay) chhupe bhi nahi.

Corridor kaise: plan ke apne lots pe wahi block-bootstrap (roadmap_portfolio) chalta hai
jo projection dikhata hai; har path ka DIN-WAAR cumulative rakha jaata hai, to har din ke
liye ek poori distribution mil jaati hai. Aaj ka actual us distribution me kis percentile
pe baitha — wahi "corridor me kahan ho" hai.

Status ke 3 hi rang (jaan-boojh ke kam):
  🟢 SMOOTH  — cum actual 25–75 percentile ke beech. Kuch mat karo.
  🟡 PEECHE  — median se neeche par p5 ke upar. Normal bad-luck stretch — rule pe raho.
  🔴 ALERT   — p5 se neeche, YA lagatar N din 20th percentile se neeche.
               Ye "aur lot daalo" ka signal NAHI hai — engine re-validate karne ka hai.

Reuse (Rule 6B): roadmap_portfolio.simulate_per_lot (wahi corridor jo page dikhata hai),
goal_planner.active_plan, order_store.trades_for_range + charges (wahi net jo dashboard
dikhata hai — exit-date bucket, 400-din netting, TRAP #141).
"""
import os
import sys
import json
import datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import _paths  # noqa: F401
except Exception:
    pass

import roadmap_portfolio as _rp          # noqa: E402
import goal_planner as _gp               # noqa: E402

try:
    import order_store                    # noqa: E402
except Exception:
    order_store = None
try:
    import charges as _charges            # noqa: E402
except Exception:
    _charges = None
try:
    import roadmap as _rm                 # noqa: E402  (_matcher: resolve-aware identity)
except Exception:
    _rm = None
try:
    from market_calendar import is_trading_day, ist_now
except Exception:                          # pragma: no cover
    def ist_now():
        return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)

    def is_trading_day(d):
        return d.weekday() < 5

LOG_PATH = os.path.join(_ROOT, "data", "roadmap_daily.json")
STREAK_N = 10          # itne din lagatar 20th %ile se neeche = 🔴
LOW_PCT = 20.0


# ─────────────────────────────────────────────────────────── actual P&L
def _tax(t):
    if _charges is None:
        return 0.0
    try:
        ep = float(t.get("entry_price") or t.get("avg_price") or 0)
        xp = float(t.get("exit_price") or 0)
        qty = int(float(t.get("qty") or 0))
        side = "BUY" if str(t.get("entry", "")).upper() == "BUY" else "SELL"
        when = t.get("exit_date") or t.get("entry_date")
        return round(_charges.option_charges(ep, xp, qty, entry_side=side, when=when), 2)
    except Exception:
        return 0.0


def actual_by_day(config_keys, start_date, end_date=None):
    """
    {iso: net} — plan ke members ka combined REAL net, exit-date pe bucket.
    Wahi net jo dashboard dikhata hai: gross − date-aware charges, 400-din lookback se
    netted (positional legs sahi pair hon — TRAP #141).
    """
    if order_store is None:
        return {}, {}
    end_date = end_date or ist_now().date().isoformat()
    lo = (dt.date.fromisoformat(start_date) - dt.timedelta(days=400)).isoformat()
    try:
        rng = order_store.trades_for_range(lo, end_date)
    except Exception:
        return {}, {}
    matchers = []
    for ck in config_keys:
        if not ck:
            continue
        try:
            matchers.append((ck, _rm._matcher(ck) if _rm else (lambda s, k=ck: s == k)))
        except Exception:
            matchers.append((ck, lambda s, k=ck: str(s) == k))
    by_day, by_member = {}, {}
    for t in (rng.get("details") or []):
        xd = t.get("exit_date")
        if not xd or xd < start_date or xd > end_date:
            continue
        raw = t.get("strategy")
        hit = next((ck for ck, m in matchers if m(raw)), None)
        if hit is None:
            continue
        net = round((t.get("pnl") or 0.0) - _tax(t), 2)
        by_day[xd] = round(by_day.get(xd, 0.0) + net, 2)
        d = by_member.setdefault(hit, {})
        d[xd] = round(d.get(xd, 0.0) + net, 2)
    return by_day, by_member


# ─────────────────────────────────────────────────────────── corridor
def _corridor(plan, n_days, seed=_rp._SEED):
    """Har din ke liye cumulative distribution (sorted) — plan ke apne lots pe."""
    lots_map = plan.get("lots") or {}
    ck_map = plan.get("config_keys") or {}
    specs = []
    for m in _rp.members(include_blocked=False):
        L = int(lots_map.get(m["id"]) or 0)
        if L <= 0:
            continue
        specs.append({"id": m["id"], "label": m["label"], "slug": m["slug"],
                      "run_lots": m.get("run_lots"), "lots": L,
                      "config_key": ck_map.get(m["id"]) or m.get("config_key")})
    if not specs or n_days <= 0:
        return None, specs
    sim = _rp.simulate_per_lot(specs, n_days, seed=seed)
    if not sim:
        return None, specs
    dly, paths = sim["daily"], sim["paths"]
    lots = [s["lots"] for s in specs]
    # cum[d] = us din tak ka cumulative, saare paths ka sorted list
    cum = [[0.0] * paths for _ in range(n_days)]
    for p in range(paths):
        run = 0.0
        for d in range(n_days):
            step = 0.0
            for j in range(len(specs)):
                step += dly[j][p][d] * lots[j]
            run += step
            cum[d][p] = run
    for d in range(n_days):
        cum[d].sort()
    return cum, specs


def _pctile_of(sorted_vals, x):
    """x is distribution me kis percentile pe (0-100)."""
    if not sorted_vals:
        return None
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return round(100.0 * lo / len(sorted_vals), 1)


def _q(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    return sorted_vals[int(p * (len(sorted_vals) - 1))]


# ─────────────────────────────────────────────────────────── build
def build(plan=None, upto=None):
    """Active plan ka daily log payload."""
    plan = plan or _gp.active_plan()
    if not plan:
        return {"ok": False, "reason": "koi active plan nahi — Goal Planner se ek plan "
                                       "apply karein, phir roz ka log yahan banega"}
    start = str(plan.get("applied_at") or "")[:10] or ist_now().date().isoformat()
    today = upto or ist_now().date().isoformat()
    to_date = plan.get("to_date") or today

    # plan ke kul trading din (corridor ki lambai) + ab tak beete trading din
    horizon = max(1, _rp.trading_days_between(
        dt.date.fromisoformat(start) - dt.timedelta(days=1), to_date))
    cum, specs = _corridor(plan, horizon)
    if cum is None:
        return {"ok": False, "reason": "plan ke lots pe corridor nahi ban paya "
                                       "(backtest data missing?)"}

    cks = [s.get("config_key") for s in specs]
    by_day, by_member = actual_by_day(cks, start, today)

    # asli trading din, start se aaj tak
    days = []
    cur = dt.date.fromisoformat(start)
    endd = dt.date.fromisoformat(today)
    while cur <= endd:
        try:
            trading = is_trading_day(cur)
        except Exception:
            trading = cur.weekday() < 5
        if trading:
            days.append(cur.isoformat())
        cur += dt.timedelta(days=1)

    rows = []
    cum_actual = 0.0
    low_streak = 0
    for i, d in enumerate(days):
        if i >= horizon:
            break
        act = float(by_day.get(d, 0.0))
        cum_actual += act
        col = cum[i]
        med = _q(col, .50)
        prev_med = _q(cum[i - 1], .50) if i else 0.0
        exp_day = med - prev_med
        pct = _pctile_of(col, cum_actual)
        p5, p25, p75 = _q(col, .05), _q(col, .25), _q(col, .75)
        if pct is not None and pct < LOW_PCT:
            low_streak += 1
        else:
            low_streak = 0
        if cum_actual < p5 or low_streak >= STREAK_N:
            status, label = "alert", "Alert"
        elif cum_actual < p25:
            status, label = "behind", "Peeche"
        else:
            status, label = "smooth", "Smooth"
        rows.append({
            "date": d,
            "expected": round(exp_day), "actual": round(act),
            "diff": round(act - exp_day),
            "cum_expected": round(med), "cum_actual": round(cum_actual),
            "cum_p5": round(p5), "cum_p25": round(p25), "cum_p75": round(p75),
            "pctile": pct, "status": status, "status_label": label,
            "trades": None,
        })

    last = rows[-1] if rows else None
    # goal pe rehne ka chance: aaj ke actual se aage ka bacha hua safar
    p_goal_now = None
    if last and plan.get("target"):
        left = horizon - len(rows)
        if left > 0:
            sim_left = _rp.simulate_per_lot(specs, left)
            ev = _rp.evaluate(sim_left, [s["lots"] for s in specs],
                              target=float(plan["target"]) - last["cum_actual"])
            p_goal_now = ev.get("p_target") if ev else None
        else:
            p_goal_now = 100.0 if last["cum_actual"] >= float(plan["target"]) else 0.0

    return {
        "ok": True,
        "plan": {"id": plan.get("id"), "name": plan.get("name"),
                 "target": plan.get("target"), "to_date": plan.get("to_date"),
                 "applied_at": plan.get("applied_at"),
                 "lots": plan.get("lots"), "capital": plan.get("capital")},
        "members": [{"id": s["id"], "label": s["label"], "lots": s["lots"]} for s in specs],
        "horizon_days": horizon, "days_done": len(rows),
        "today": last, "rows": list(reversed(rows)),
        "p_goal_now": p_goal_now,
        "generated": ist_now().strftime("%Y-%m-%d %H:%M"),
    }


def snapshot(payload=None):
    """Roz ka snapshot disk pe (audit ke liye) — timer isse call karta hai."""
    payload = payload or build()
    if not payload.get("ok"):
        return payload
    store = {}
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            store = json.load(fh)
    except Exception:
        store = {}
    day = (payload.get("today") or {}).get("date")
    if day:
        store.setdefault("days", {})[day] = payload["today"]
        store["plan"] = payload["plan"]
        store["updated"] = payload["generated"]
        tmp = LOG_PATH + ".tmp"
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, LOG_PATH)
    return payload


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="disk pe roz ka snapshot likho")
    a = ap.parse_args()
    out = snapshot() if a.snapshot else build()
    print(json.dumps(out, indent=1, default=str))
