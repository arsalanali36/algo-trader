"""
goal_planner.py — "mujhe ₹X chahiye Y date tak" → lots ka basket, aur usko system pe apply.

Do hisse:
  1) SOLVER (pure, read-only): target + deadline + drawdown-budget → per-strategy integer
     lots. Sizing DRAWDOWN-BUDGET se hoti hai, margin se NAHI — margin dekh ke size karo
     to ₹4L pe 30+ lot "fit" ho jaate hain aur ek bura mahina account aadha kar deta hai
     (yahi galti memory [[project_code3b_hedged_strangle_family]] me darj hai).
  2) APPLY (config write, money-ADJACENT): chuna hua plan `nifty_config` me likhta hai —
     sirf `lots` aur per-strategy `capital_rs`. Mode/active KABHI nahi.

APPLY ke hard rails (jaan-boojh ke, user-agreed):
  • `mode` (paper/live) aur `active`/`enabled` flag kabhi nahi chhuta → planner ka koi
    click paper strategy ko live nahi kar sakta.
  • Jis strategy ki position ABHI khuli hai uske lots beech me nahi badalte → wo change
    `pending` me jaata hai (agli fresh entry ke liye), taaki ek aadhi-khuli position ke
    do alag size na ho jaayein.
  • Har write se pehle timestamped backup + atomic replace + readback verify + audit line.
  • Koi bhi live-mode strategy plan me ho to typed confirm (`APPLY REAL`) chahiye.
  • Rollback = backup se wapas (ek call).

Reuse (Rule 6B): roadmap_portfolio.simulate_per_lot/evaluate (WAHI bootstrap jo page
dikhata hai — solver aur display ka number kabhi diverge nahi karega), order_store
(khuli position), strategy_registry (identity).
"""
import os
import sys
import json
import math
import shutil
import tempfile
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

import roadmap_portfolio as _rp             # noqa: E402

try:
    import order_store                       # noqa: E402
except Exception:
    order_store = None
try:
    import strategy_registry as _reg         # noqa: E402
except Exception:
    _reg = None
try:
    from market_calendar import ist_now
except Exception:                            # pragma: no cover
    def ist_now():
        return dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)

NIFTY_CFG = os.path.join(_ROOT, "nifty_config.json")
PLAN_PATH = os.path.join(_ROOT, "data", "roadmap_plan.json")
AUDIT_PATH = os.path.join(_ROOT, "data", "roadmap_plan_audit.jsonl")
BACKUP_DIR = os.path.join(_ROOT, "_config_backups")

CONFIRM_TOKEN = "APPLY REAL"
CAPITAL_BUFFER = 1.25      # cap = plan lots ka real margin × buffer (roll/slip ke liye)
MAX_STEPS = 400            # greedy safety stop


# ═══════════════════════════════════════════════════════════════ SOLVER
def _candidates(scope="all", include_ids=None):
    """
    Basket ke liye upalabdh strategies.

    scope="auto" → sirf configured members nahi, balki **saari** strategies jinka Lab run
    hai aur jo deploy-gate paas karti hain (`strategy_candidates`) — yahi "system khud
    chun kar bataye" wala mode. Paper strategies bhi aati hain: planner unhe lots suggest
    kar sakta hai, par live KABHI khud nahi karta (mode/active apply se bahar hain).
    """
    if scope == "auto":
        try:
            import strategy_candidates as _sc
            out = []
            for c in _sc.eligible_members():
                out.append({
                    "id": c["id"], "label": c["label"], "slug": c["slug"],
                    "config_key": c["config_key"], "run_lots": c.get("run_lots"),
                    "capacity_lots": int(c.get("capacity_lots") or 20),
                    "mode": c.get("mode"), "cur_lots": int(c.get("cur_lots") or 0),
                    "book": c.get("book") or 0,
                })
            if include_ids:
                out = [c for c in out if c["id"] in include_ids]
            return out
        except Exception as e:
            print("[goal_planner] auto-scope fail, falling back to members:", e, flush=True)

    out = []
    for m in _rp.members(include_blocked=False):
        if not m.get("enabled"):
            continue
        if include_ids and m["id"] not in include_ids:
            continue
        if scope == "real" and (m["live"].get("mode") != "live"):
            continue
        out.append({
            "id": m["id"], "label": m["label"], "slug": m["slug"],
            "config_key": m.get("config_key"), "run_lots": m.get("run_lots"),
            "capacity_lots": int(m.get("capacity_lots") or 40),
            "mode": m["live"].get("mode"), "cur_lots": int(m["live"].get("lots") or 0),
            "book": m.get("book") or 0,
        })
    return out


def solve(target_rs, to_date, dd_budget_rs, scope="all", include_ids=None,
          risk_mult=1.0, seed=_rp._SEED, p_goal=60.0, weights=None, max_share=None):
    """
    Greedy integer-lot allocation:
      har step pe woh lot add karo jiska (Δ goal-probability) ÷ (Δ risk) sabse accha ho,
      jahan risk = p5 loss (bad-luck 1-in-20 nuksan).
      Rukne ki 3 wajah: DD budget khatam · goal-probability p_goal tak pahunch gayi ·
      capacity cap.

    `weights` = user ka bharosa (id → 0/0.5/1/2/3). 0 = is basket me bilkul mat lo
    (strategy BAND nahi hoti — sirf plan me nahi aati). Ye PREFERENCE hai, gate nahi:
    gate-fail strategy weight 3 pe bhi basket me nahi aa sakti (wo `_candidates` me hi
    nahi aati).

    `max_share` = ek strategy zyada se zyada kitna **expected** hissa le sakti hai
    (0.4–1.0). Cap CAPITAL pe nahi, EXPECTED pe hai — jaan-boojh ke: 04.03.02 ₹5,385/lot
    aur 02.10.01 ₹63,623/lot, to capital-share pe cap lagate hi solver deadlock ho jaata
    hai (pehla lot hamesha 100% share). Aur asli sawaal bhi yahi hai — kitna paisa EK
    edge ke sach hone pe tika hai, margin kitna block hua wo nahi.

    ⚠️ Objective PROBABILITY hai, median nahi — jaan-boojh ke. "Median = target" pe rukte
    to goal hit ka chance ~50% hota (median ki definition hi yahi hai) aur page ek aisa
    plan dikhata jo aadhe waqt target miss karta. p_goal default 60% = "target se thoda
    upar ka median" — 100% kabhi possible nahi, aur planner wo daawa karega bhi nahi.

    Risk hamesha JOINT distribution se aata hai (per-member risk jodte nahi) — teen
    short-vol strategies ka bura din ek saath aata hai, aur solver ko wahi dikhna chahiye.
    """
    cands = _candidates(scope, include_ids)
    today = ist_now().date()
    n_days = _rp.trading_days_between(today, to_date)
    if not cands or n_days <= 0:
        return {"ok": False, "reason": "koi eligible strategy nahi ya date aaj se pehle hai",
                "n_days": n_days, "members": []}

    sim = _rp.simulate_per_lot(cands, n_days, seed=seed)
    if not sim:
        return {"ok": False, "reason": "backtest data nahi mila", "n_days": n_days,
                "members": []}

    budget = float(dd_budget_rs or 0) * float(risk_mult or 1.0)
    target = float(target_rs or 0)
    nm = len(cands)
    lots = [0] * nm

    def score_of(vec):
        """
        (risk, goal-probability, median). risk = **1-in-20 max drawdown** (peak-to-trough
        path ke andar), terminal p5 nahi — DD budget ka wahi asli matlab hai. Terminal p5
        hamesha chhota aata hai (path beech me isse zyada neeche ja chuka hota hai), aur
        usse size karna risk ko systematically kam aankna hota.
        """
        ev = _rp.evaluate(sim, vec, target=target if target > 0 else None)
        risk = float(ev.get("maxdd_p95") or max(0.0, -float(ev["p5"])))
        pg = float(ev.get("p_target", 0.0)) if target > 0 else 0.0
        return risk, pg, float(ev["p50"])

    def top_share(ev):
        """Sabse bade contributor ka expected-hissa (0..1)."""
        ms = [m for m in (ev or {}).get("members", []) if (m.get("p50") or 0) > 0]
        tot = sum(m["p50"] for m in ms)
        return (max(m["p50"] for m in ms) / tot) if (ms and tot > 0) else 0.0

    W = {str(k): float(v) for k, v in (weights or {}).items()}
    cap_share = float(max_share) if max_share and float(max_share) < 1.0 else None
    # cap tabhi binding jab basket itna bada ho ki wo satisfy ho SAKE — warna pehla hi
    # lot 100% share hota hai aur solver 0 lots pe atak jaata (measured: cap-50 pe
    # 1 lot ka bekaar plan)
    min_lots_for_cap = int(math.ceil(1.0 / cap_share)) if cap_share else 0
    cap_relaxed = False

    cur_risk, cur_p, cur_med = score_of(lots)
    steps = 0
    stop = "goal-reached"
    while steps < MAX_STEPS:
        steps += 1
        if target > 0 and cur_p >= p_goal:
            stop = "goal-reached"
            break
        if target <= 0 and cur_med > 0 and steps > 1:
            stop = "no-target"
            break
        best = best_over_cap = None
        for j in range(nm):
            if lots[j] >= cands[j]["capacity_lots"]:
                continue
            w = W.get(str(cands[j]["id"]), 1.0)
            if w <= 0:                                # user ne "Off" kiya
                continue
            trial = list(lots)
            trial[j] += 1
            ev = _rp.evaluate(sim, trial, target=target if target > 0 else None)
            r = float(ev.get("maxdd_p95") or max(0.0, -float(ev["p5"])))
            pg = float(ev.get("p_target", 0.0)) if target > 0 else 0.0
            med = float(ev["p50"])
            if r > budget:                            # DD budget = hard wall
                continue
            gain = (pg - cur_p) if target > 0 else (med - cur_med)
            if gain <= 0:                             # ulta jaa raha? mat lo
                continue
            d_risk = max(1.0, r - cur_risk)           # 1 = divide-by-zero guard
            score = gain * w / d_risk                 # user ka bharosa yahan jhukta hai
            cand = (score, j, r, pg, med)
            over_cap = bool(cap_share and sum(trial) >= min_lots_for_cap
                            and top_share(ev) > cap_share)
            if over_cap:
                if best_over_cap is None or score > best_over_cap[0]:
                    best_over_cap = cand
                continue
            if best is None or score > best[0]:
                best = cand
        if best is None and best_over_cap is not None:
            # SOFT fallback: cap ke andar kuch add nahi ho pa raha. Chupchaap 1-lot ka
            # bekaar plan dene se behtar hai cap tod ke aage badhna AUR user ko batana.
            best, cap_relaxed = best_over_cap, True
        if best is None:
            stop = "dd-budget-full" if any(
                lots[j] < cands[j]["capacity_lots"] for j in range(nm)) else "capacity-cap"
            break
        _, j, cur_risk, cur_p, cur_med = best
        lots[j] += 1
    else:
        stop = "step-cap"

    ev = _rp.evaluate(sim, lots, target=target if target else None)
    total_lots = sum(lots)
    caps = []
    capital = 0
    for j, c in enumerate(cands):
        meta = sim["metas"][j]
        cpl = meta.get("capital_per_lot") or 0
        cap_rs = int(round(cpl * lots[j] * CAPITAL_BUFFER)) if lots[j] else 0
        capital += (cpl or 0) * lots[j]
        mem = next((m for m in (ev or {}).get("members", []) if m["id"] == c["id"]), None)
        caps.append({
            "id": c["id"], "label": c["label"], "slug": c["slug"],
            "config_key": c["config_key"], "mode": c["mode"],
            "cur_lots": c["cur_lots"], "lots": lots[j],
            "delta": lots[j] - c["cur_lots"],
            "capital_per_lot": cpl, "capital": int(round((cpl or 0) * lots[j])),
            "capital_cap_rs": cap_rs,
            "capacity_lots": c["capacity_lots"],
            "expected": (mem or {}).get("p50"),
            "worst_case": (mem or {}).get("p5"),
            "trades": (mem or {}).get("trades") or sim["metas"][j]["trades"],
            "dropped_reason": None if lots[j] else "per-₹-risk kam — budget kisi aur pe behtar laga",
        })

    top = top_share(ev)
    p_hit = float((ev or {}).get("p_target") or 0.0)
    feasible = bool(target <= 0 or p_hit >= p_goal)
    if total_lots == 0:
        verdict = ("Is drawdown budget me ek lot bhi fit nahi hota — "
                   "DD budget badhaiye ya target ghataiye.")
    elif feasible:
        verdict = f"Ho sakta hai — par {p_hit:.0f}% chance hai, guarantee nahi."
    elif stop == "dd-budget-full":
        verdict = (f"Is DD budget (₹{budget:,.0f}) me sirf {p_hit:.0f}% chance banta hai. "
                   f"Ya target ghataiye, ya DD budget badhaiye — lots aur nahi badha sakte.")
    else:
        verdict = (f"Capacity cap tak pahunch gaye — {p_hit:.0f}% chance se aage "
                   f"in strategies pe nahi ja sakte.")

    return {
        "ok": True,
        "feasible": feasible,
        "p_hit": round(p_hit, 1),
        "p_goal": p_goal,
        "verdict": verdict,
        "maxdd_p95": (ev or {}).get("maxdd_p95") or 0,
        "maxdd_median": (ev or {}).get("maxdd_median") or 0,
        "top_share": round(100.0 * top, 1),
        "max_share": (round(100.0 * cap_share) if cap_share else None),
        "cap_relaxed": cap_relaxed,
        "weights": W or None,
        "stop_reason": stop,
        "target": round(target), "to_date": to_date, "n_days": n_days,
        "dd_budget": round(float(dd_budget_rs or 0)),
        "dd_budget_used": round(cur_risk),
        "risk_mult": risk_mult,
        "scope": scope,
        "total_lots": total_lots,
        "capital": int(round(capital)),
        "projection": ev,
        "members": caps,
        "generated": ist_now().strftime("%Y-%m-%d %H:%M"),
    }


SCENARIO_SPEC = {
    # name        risk_mult  p_goal   kya matlab
    "safe":       (0.5,      50.0),   # aadha DD budget, coin-flip se thoda behtar
    "balanced":   (1.0,      60.0),   # poora budget, 60% chance
    "aggressive": (1.5,      75.0),   # budget se 1.5x (jaan-boojh ke over), 75% chance
}


def scenarios(target_rs, to_date, dd_budget_rs, scope="all", include_ids=None,
              weights=None, max_share=None):
    """
    Safe / Balanced / Aggressive — teeno me DONO cheezein badalti hain: kitna risk lena
    (risk_mult) aur kitna chance chahiye (p_goal). Sirf budget badalne se teeno ek jaise
    aa jaate the (goal budget se pehle hi pura ho jaata tha) — jo user ko choice ke naam
    pe teen same option dikhata.
    aggressive jaan-boojh ke budget se BAHAR ja sakta hai — page usko ⚠ flag karta hai.
    """
    out = {}
    for name, (mult, pg) in SCENARIO_SPEC.items():
        s = solve(target_rs, to_date, dd_budget_rs, scope, include_ids,
                  risk_mult=mult, p_goal=pg, weights=weights, max_share=max_share)
        s["name"] = name
        s["over_budget"] = bool(s.get("maxdd_p95", 0) > (dd_budget_rs or 0))
        out[name] = s
    return out


# ═══════════════════════════════════════════════════════════════ PLAN STORE
def _read(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _atomic_write(path, obj):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _plan_store():
    return _read(PLAN_PATH, {"active": None, "history": []})


def active_plan():
    return (_plan_store() or {}).get("active")


def _audit(event, payload):
    try:
        os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": ist_now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "event": event, **payload}, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════ APPLY
def _open_legs_by_strategy():
    """{config_key(lower): open leg count} — 400-din lookback (positional bhi pakde)."""
    if order_store is None:
        return {}
    try:
        today = ist_now().date()
        lo = (today - dt.timedelta(days=400)).isoformat()
        rng = order_store.trades_for_range(lo, today.isoformat())
    except Exception:
        return {}
    out = {}
    for o in (rng.get("open") or []):
        s = str(o.get("strategy") or "")
        if not s:
            continue
        key = s
        if _reg is not None:
            try:
                key = _reg.resolve(s) or s
            except Exception:
                key = s
        out[str(key).lower()] = out.get(str(key).lower(), 0) + 1
        out[s.lower()] = out.get(s.lower(), 0) + 1
    return out


def _resolve_key(config_key):
    if _reg is None or not config_key:
        return str(config_key or "").lower()
    try:
        return str(_reg.resolve(config_key) or config_key).lower()
    except Exception:
        return str(config_key).lower()


def preview_apply(plan):
    """
    Kya-kya badlega — koi write nahi. Har row: lots old→new, cap old→new, mode (untouched),
    aur kab lagega (turant / agli entry se, agar position khuli hai).
    """
    cfg = _read(NIFTY_CFG, {})
    risk = (cfg.get("_risk") or {}).get("per_strategy") or {}
    open_map = _open_legs_by_strategy()
    rows, needs_confirm = [], False
    for m in (plan.get("members") or []):
        ck = m.get("config_key")
        c = cfg.get(ck) or {}
        field = None
        for f in _rp.LOT_FIELDS:
            if isinstance(c.get(f), (int, float)):
                field = f
                break
        cur_lots = int(c.get(field) or 0) if field else None
        cur_cap = (risk.get(ck) or {}).get("capital_rs")
        live = str(c.get("mode") or "paper").lower() == "live"
        if live and int(m.get("lots") or 0) > 0:
            needs_confirm = True
        rk = _resolve_key(ck)
        open_n = open_map.get(rk, 0) or open_map.get(str(ck).lower(), 0)
        rows.append({
            "id": m.get("id"), "label": m.get("label"), "config_key": ck,
            "mode": c.get("mode") or "paper", "mode_changes": False,
            "lots_field": field, "lots_from": cur_lots, "lots_to": int(m.get("lots") or 0),
            "cap_from": cur_cap, "cap_to": m.get("capital_cap_rs"),
            "config_present": bool(c),
            "open_legs": open_n,
            "when": "agli entry se (position khuli hai)" if open_n else "turant",
            "queued": bool(open_n),
            "skip_reason": None if c else "config me ye key nahi mili — skip",
        })
    return {"rows": rows, "needs_confirm": needs_confirm,
            "confirm_token": CONFIRM_TOKEN if needs_confirm else None}


def apply_plan(plan, confirm=None, paper_only=False, note=""):
    """
    Plan ko nifty_config me likho. Sirf lots + capital_rs.
    Live-mode strategy shaamil ho to `confirm` == "APPLY REAL" zaroori.
    """
    pv = preview_apply(plan)
    if pv["needs_confirm"] and not paper_only and str(confirm or "").strip().upper() != CONFIRM_TOKEN:
        return {"ok": False, "reason": f"real-money strategy plan me hai — "
                                       f"'{CONFIRM_TOKEN}' type karke confirm karein",
                "preview": pv}

    cfg = _read(NIFTY_CFG, {})
    if not cfg:
        return {"ok": False, "reason": "nifty_config.json padha nahi ja saka", "preview": pv}

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = ist_now().strftime("%Y%m%d_%H%M%S")
    backup = os.path.join(BACKUP_DIR, f"nifty_config.json.bak.plan_{stamp}")
    try:
        shutil.copy2(NIFTY_CFG, backup)
    except Exception as e:
        return {"ok": False, "reason": f"backup nahi ban paya, kuch nahi likha: {e}"}

    cfg.setdefault("_risk", {}).setdefault("per_strategy", {})
    applied, queued, skipped = [], [], []

    for row in pv["rows"]:
        ck = row["config_key"]
        if row["skip_reason"] or not ck:
            skipped.append({**row, "why": row["skip_reason"] or "config_key nahi"})
            continue
        if paper_only and str(row["mode"]).lower() == "live":
            skipped.append({**row, "why": "paper-only apply chuna gaya"})
            continue
        if row["queued"]:
            queued.append(row)                     # lots abhi nahi badlenge
            continue
        field = row["lots_field"] or "lots"
        target_lots = int(row["lots_to"] or 0)
        if target_lots <= 0:
            skipped.append({**row, "why": "plan me 0 lots — strategy ko chhua nahi "
                                          "(band karna ho to alag se Stop)"})
            continue
        node = cfg.setdefault(ck, {})
        node[field] = target_lots                  # ← sirf lots
        ps = cfg["_risk"]["per_strategy"].setdefault(ck, {})
        if row["cap_to"]:
            ps["capital_rs"] = int(row["cap_to"])  # ← aur capital cap
        applied.append(row)

    # ── atomic write + readback verify
    try:
        _atomic_write(NIFTY_CFG, cfg)
    except Exception as e:
        try:
            shutil.copy2(backup, NIFTY_CFG)
        except Exception:
            pass
        return {"ok": False, "reason": f"write fail (backup se restore kiya): {e}"}

    back = _read(NIFTY_CFG, {})
    verify = []
    for row in applied:
        ck, field = row["config_key"], (row["lots_field"] or "lots")
        got = (back.get(ck) or {}).get(field)
        cap_got = ((back.get("_risk") or {}).get("per_strategy", {}).get(ck) or {}).get("capital_rs")
        ok = (int(got or 0) == int(row["lots_to"] or 0))
        verify.append({"id": row["id"], "config_key": ck, "lots_written": got,
                       "cap_written": cap_got, "ok": ok})
    all_ok = all(v["ok"] for v in verify) if verify else True
    if not all_ok:
        try:
            shutil.copy2(backup, NIFTY_CFG)
        except Exception:
            pass
        return {"ok": False, "reason": "readback verify FAIL — config backup se restore kiya",
                "verify": verify, "backup": backup}

    store = _plan_store()
    prev = store.get("active")
    record = {
        "id": stamp,
        "name": plan.get("name") or plan.get("scenario") or "custom",
        "target": plan.get("target"), "to_date": plan.get("to_date"),
        "dd_budget": plan.get("dd_budget"),
        "lots": {m["id"]: int(m.get("lots") or 0) for m in (plan.get("members") or [])},
        "config_keys": {m["id"]: m.get("config_key") for m in (plan.get("members") or [])},
        "projection": plan.get("projection"),
        "capital": plan.get("capital"),
        "applied_at": ist_now().strftime("%Y-%m-%d %H:%M:%S"),
        "applied": [r["id"] for r in applied],
        "queued": [r["id"] for r in queued],
        "skipped": [r["id"] for r in skipped],
        "backup": backup,
        "paper_only": bool(paper_only),
        "note": note,
    }
    store["active"] = record
    store.setdefault("history", []).insert(0, {k: record[k] for k in
                                               ("id", "name", "target", "to_date", "lots",
                                                "applied_at", "backup")})
    store["history"] = store["history"][:40]
    if prev:
        store["previous"] = prev
    _atomic_write(PLAN_PATH, store)
    _audit("apply", {"plan": record["id"], "name": record["name"],
                     "applied": record["applied"], "queued": record["queued"],
                     "skipped": record["skipped"], "backup": backup})

    return {"ok": True, "plan": record, "verify": verify,
            "applied": applied, "queued": queued, "skipped": skipped, "backup": backup}


def rollback():
    """Pichle apply ka config backup wapas — plan store bhi peeche."""
    store = _plan_store()
    act = store.get("active")
    if not act or not act.get("backup"):
        return {"ok": False, "reason": "koi applied plan nahi mila (ya uska backup gayab)"}
    b = act["backup"]
    if not os.path.exists(b):
        return {"ok": False, "reason": f"backup file nahi mili: {b}"}
    stamp = ist_now().strftime("%Y%m%d_%H%M%S")
    try:
        shutil.copy2(NIFTY_CFG, os.path.join(BACKUP_DIR, f"nifty_config.json.bak.prerollback_{stamp}"))
        shutil.copy2(b, NIFTY_CFG)
    except Exception as e:
        return {"ok": False, "reason": f"restore fail: {e}"}
    store["active"] = store.get("previous")
    store.pop("previous", None)
    _atomic_write(PLAN_PATH, store)
    _audit("rollback", {"restored_from": b, "plan": act.get("id")})
    return {"ok": True, "restored_from": b, "active": store.get("active")}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=30000)
    ap.add_argument("--to", default=None)
    ap.add_argument("--dd", type=float, default=60000)
    ap.add_argument("--scope", default="all")
    ap.add_argument("--scenarios", action="store_true")
    a = ap.parse_args()
    to = a.to or (ist_now().date() + dt.timedelta(days=32)).isoformat()
    if a.scenarios:
        print(json.dumps(scenarios(a.target, to, a.dd, a.scope), indent=1, default=str))
    else:
        s = solve(a.target, to, a.dd, a.scope)
        print(json.dumps(s, indent=1, default=str))


# ═══════════════════════════════════════════════════════════════ FUNDING (cash vs collateral)
#
# Zerodha ka rule, jo plan ko chup-chaap reject karwa sakta hai chahe "available margin"
# bahut dikh raha ho (TRAP #179, memory [[project_code3b_cash_margin_gate]]):
#   • Option BECHNE (short/margin) ke liye margin ka **>=50% CASH** chahiye — cash +
#     liquid-fund collateral. Pledged EQUITY sirf baaki 50% fund karta hai.
#     → writing capacity = 2 x cash-equivalent, chahe kitna bhi stock pledged ho.
#   • Option KHARIDNE ke liye **100% cash** — premium collateral se nahi de sakte.
# Isliye ek plan ke liye:
#     cash chahiye  = 0.5 x (short/margin capital) + (BUY premium)
#     total chahiye = short/margin capital + BUY premium   (<= capacity)

_BUY_BASES = ("buy",)          # registry_economics ka capital_basis
_CRYPTO_BASES = ("crypto",)    # Delta — Zerodha funding se bilkul alag khaata


def _capital_basis(slug):
    try:
        import registry_economics as _re
        return (_re.economics(slug) or {}).get("capital_basis")
    except Exception:
        return None


def funding_check(plan, broker_name=None):
    """
    Plan ko user ke ASLI broker funds ke against tolta hai.

    Returns: kitna cash chahiye vs hai, kitna collateral se chal jayega, aur agar
    kam pade to **kitna cash daalna hai** (pledge se ye gap nahi bharta — ye baat
    payload me `pledge_helps=False` ke saath jaati hai).

    Do alag jawab deta hai, kyunki dono sach hain aur dono matter karte hain:
      replace_view  = plan mojooda positions ki JAGAH le raha ho
      on_top_view   = plan unke UPAR chale (tab sirf bacha headroom kaam ka hai)
    """
    try:
        import risk_gate as rg
    except Exception as e:
        return {"ok": False, "reason": f"risk_gate import fail: {e}"}

    margin_rs = premium_rs = crypto_rs = 0.0
    rows = []
    for m in (plan.get("members") or []):
        lots = int(m.get("lots") or 0)
        if lots <= 0:
            continue
        cap = float(m.get("capital") or 0)
        basis = _capital_basis(m.get("slug")) or "hedged"
        kind = ("buy" if basis in _BUY_BASES else
                "crypto" if basis in _CRYPTO_BASES else "margin")
        if kind == "buy":
            premium_rs += cap
        elif kind == "crypto":
            crypto_rs += cap
        else:
            margin_rs += cap
        rows.append({"id": m.get("id"), "label": m.get("label"), "lots": lots,
                     "capital": round(cap), "kind": kind})

    cash_needed = 0.5 * margin_rs + premium_rs
    total_needed = margin_rs + premium_rs

    ch = rg.cash_headroom(broker_name)
    if not ch.get("ok"):
        return {"ok": False, "reason": "broker funds abhi nahi mile — funding check skip",
                "need": {"margin": round(margin_rs), "premium": round(premium_rs),
                         "cash": round(cash_needed), "total": round(total_needed)},
                "rows": rows, "crypto": round(crypto_rs)}

    bal = {}
    try:
        bal = rg.get_broker_balance(broker_name or rg.default_broker()) or {}
    except Exception:
        bal = {}
    cash_equiv = float(ch["cash_equiv"])
    capacity = float(ch["capacity"])
    used = float(ch["used"])
    headroom = float(ch["headroom"])
    pledged = None
    try:
        coll = bal.get("collateral")
        if coll is not None:
            pledged = round(float(coll) - cash_equiv)   # equity portion (cash-eq ke bahar)
    except Exception:
        pledged = None

    cash_gap = max(0.0, cash_needed - cash_equiv)
    replace_gap = max(0.0, total_needed - capacity)
    ontop_gap = max(0.0, total_needed - headroom)

    # ilaaj: har Rs.1 cash-equivalent capacity Rs.2 badhata hai
    add_cash = round(max(cash_gap, replace_gap / 2.0))

    # bina paisa daale: sabse bade margin-member ke kitne lot hatane se fit ho jaye
    drop = None
    if cash_gap > 0 or replace_gap > 0:
        mm = [m for m in (plan.get("members") or [])
              if int(m.get("lots") or 0) > 0
              and (_capital_basis(m.get("slug")) or "hedged") not in _BUY_BASES + _CRYPTO_BASES]
        def _need_lots(m):
            pl = float(m.get("capital") or 0) / max(1, int(m.get("lots") or 1))
            a = math.ceil(cash_gap / (0.5 * pl)) if (cash_gap > 0 and pl) else 0
            b = math.ceil(replace_gap / pl) if (replace_gap > 0 and pl) else 0
            return max(a, b)
        # aisi strategy chuno jo lots dene ke baad ZINDA bache — ek hi lot wali ko
        # zero karke "fit" dikhana asli suggestion nahi, wo strategy hata dena hai
        mm.sort(key=lambda m: (0 if _need_lots(m) < int(m.get("lots") or 0) else 1,
                               -(float(m.get("capital") or 0) / max(1, int(m.get("lots") or 1)))))
        if mm:
            top = mm[0]
            per_lot = float(top["capital"]) / max(1, int(top["lots"]))
            # ek lot hatane se cash requirement 0.5*per_lot girti hai
            n_cash = math.ceil(cash_gap / (0.5 * per_lot)) if cash_gap > 0 and per_lot else 0
            n_tot = math.ceil(replace_gap / per_lot) if replace_gap > 0 and per_lot else 0
            n = max(n_cash, n_tot)
            if n > 0:
                drop = {"id": top.get("id"), "label": top.get("label"),
                        "lots": min(n, int(top["lots"])),
                        "from_lots": int(top["lots"]), "per_lot": round(per_lot),
                        "frees_margin": round(min(n, int(top["lots"])) * per_lot)}

    fits_replace = (cash_gap <= 0) and (total_needed <= capacity)
    return {
        "ok": True,
        "need": {"margin": round(margin_rs), "premium": round(premium_rs),
                 "cash": round(cash_needed), "total": round(total_needed),
                 "collateral_ok": round(total_needed - cash_needed)},
        "have": {"live_cash": bal.get("live_cash"), "liquid_collateral": bal.get("liquid_collateral"),
                 "cash_equiv": round(cash_equiv), "pledged_equity": pledged,
                 "capacity": round(capacity), "used": round(used),
                 "headroom": round(headroom), "available": ch.get("avail")},
        "verdict": {
            "fits_replacing_current": fits_replace,
            "fits_on_top": (total_needed <= headroom) and (cash_gap <= 0),
            "cash_gap": round(cash_gap), "capacity_gap_replace": round(replace_gap),
            "capacity_gap_on_top": round(ontop_gap),
        },
        "fix": {
            "add_cash": add_cash,
            # ye line jaan-boojh ke hai: aur pledge karne se ye gap NAHI bharta, kyunki
            # binding limit total collateral nahi, 2 x cash-equivalent hai
            "pledge_helps": False,
            "shift_to_liquid": add_cash,   # pledged equity -> liquid fund = cash-equivalent
            "drop_lots": drop,
        },
        "rows": rows,
        "crypto_excluded": round(crypto_rs),
        "broker": broker_name or (rg.default_broker() if hasattr(rg, "default_broker") else None),
    }
