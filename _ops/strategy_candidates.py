"""
strategy_candidates.py — "system khud chun kar bataye kaunsi strategy basket me aani chahiye".

Read-only scanner. Har us strategy ko dekhta hai jiska ek Lab run maujood hai
(`runs/index.json`), uske apne recorded numbers pe **wahi deploy-gate** lagata hai jo
is repo me pehle se documented hai, aur teen dhero me baant deta hai:

  ✅ eligible   — gate paas; basket me aa sakti hai (agar paper hai to "live karne layak")
  ⚠️ weak       — kuch gate paas, kuch nahi (borderline; forward-paper karo)
  ❌ rejected   — gate fail ya explicitly blocked (kabhi auto-basket me nahi)

Gate (project ka apna, koi naya standard nahi):
  1. Sharpe ≥ 1        — **real_cost** ke number pe (DOM slip + date-aware STT ke baad),
                         bs_full sirf tab jab real_cost recorded na ho (tab honestly flag)
  2. p < 0.05          — significance (random-entry null ke against)
  3. train aur OOS     — dono > 0, aur OOS < 40% of train ho to "decay" flag
  4. trades ≥ 100      — itna chhota sample ki ek streak sab decide kar de, wo nahi
  5. blocked list      — `data/roadmap_portfolio.json` ka `blocked` (jaise 11.01 lookahead)
  6. Sharpe > 4        — gate FAIL nahi, par **red-flag** (repo ka apna rule: itna high
                         Sharpe aksar data-artifact hota hai) — eligible par warning ke saath

⚠️ Ye sirf SUGGEST karta hai. Koi strategy khud-ba-khud live NAHI hoti — mode/active
flip hamesha alag, jaan-boojh ke kiya gaya kaam hai (goal_planner ke apply-rails dekho).

Reuse (Rule 6B): runs/index.json (Lab ka apna artifact) + strategy_registry (naam/identity)
+ registry_economics (per-lot capital) + roadmap_portfolio (membership/blocked/live-state).
"""
import os
import sys
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "scratch", "nifty_trend"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import _paths  # noqa: F401
except Exception:
    pass

import roadmap_portfolio as _rp          # noqa: E402
try:
    import registry_economics as _econ    # noqa: E402
except Exception:
    _econ = None
try:
    import strategy_registry as _reg      # noqa: E402
except Exception:
    _reg = None

RUNS_INDEX = os.path.join(_ROOT, "scratch", "nifty_trend", "runs", "index.json")

MIN_SHARPE = 1.0
MAX_P = 0.05
MIN_TRADES = 100
DECAY_RATIO = 0.40        # OOS < 40% of train = decay flag
RED_FLAG_SHARPE = 4.0     # itna high = data-artifact ka shak (repo ka apna rule)
DEFAULT_CAPACITY = 20     # jab tak koi naapa hua capacity number na ho


def _runs():
    try:
        with open(RUNS_INDEX, encoding="utf-8") as fh:
            return json.load(fh) or []
    except Exception:
        return []


def _reg_by_slug():
    """{slug: (id, name)} — registry se, taaki naam wahi dikhe jo baaki app me."""
    out = {}
    try:
        with open(os.path.join(_ROOT, "strategy_registry.json"), encoding="utf-8") as fh:
            for sid, s in (json.load(fh).get("strategies") or {}).items():
                if s.get("slug"):
                    out[s["slug"]] = (sid, s.get("name") or sid, s.get("config_key"))
    except Exception:
        pass
    return out


def _num(d, *path, default=None):
    cur = d
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur if isinstance(cur, (int, float)) else default


def scan():
    """Har run ka candidate-card + gate verdict."""
    cfg = _rp.load_cfg()
    blocked = cfg.get("blocked") or {}
    member_ids = {m.get("id") for m in (cfg.get("members") or [])}
    ncfg = _rp._nifty_cfg()
    rbs = _reg_by_slug()

    out = []
    for r in _runs():
        slug = r.get("slug")
        if not slug:
            continue
        sid, name, reg_ck = rbs.get(slug, (None, r.get("title") or slug, None))
        ck = r.get("deploy_key") or r.get("deployed") or reg_ck
        live = _rp._live_state(ck or "", ncfg)

        rc = r.get("real_cost") or {}
        sharpe = _num(rc, "full", "sharpe")
        cost_basis = "real-cost (slip + STT)"
        if sharpe is None:
            sharpe = _num(r, "bs_full", "sharpe")
            cost_basis = "BS-modelled (real-cost recost nahi hua)"
        train = _num(rc, "train", "sharpe", default=_num(r, "train_sharpe"))
        oos = _num(rc, "oos", "sharpe", default=_num(r, "oos_sharpe"))
        p = _num(r, "p_value")
        trades = _num(r, "bs_full", "trades", default=0) or 0
        maxdd = _num(rc, "full", "maxdd", default=_num(r, "bs_full", "maxdd"))
        net_pct = _num(rc, "full", "net_pct", default=_num(r, "bs_full", "net_pct"))

        fails, flags = [], []
        block_reason = blocked.get(sid or "") or blocked.get(ck or "")
        if block_reason:
            fails.append("blocked: " + str(block_reason)[:110])
        if sharpe is None:
            fails.append("koi Sharpe recorded nahi")
        elif sharpe < MIN_SHARPE:
            fails.append(f"Sharpe {sharpe:.2f} < {MIN_SHARPE}")
        if p is None:
            # "test hua hi nahi" ko "test paas" nahi maanenge — auto-basket se bahar
            fails.append("significance test recorded nahi (p missing)")
        elif p >= MAX_P:
            fails.append(f"significance fail (p={p:.3f})")
        if trades < MIN_TRADES:
            fails.append(f"sirf {int(trades)} trades (<{MIN_TRADES})")
        if train is not None and oos is not None:
            if oos <= 0 or train <= 0:
                fails.append("train ya OOS me se ek negative")
            elif oos < DECAY_RATIO * train:
                flags.append(f"OOS decay (train {train:.2f} → OOS {oos:.2f})")
        else:
            flags.append("train/OOS split recorded nahi")
        if sharpe is not None and sharpe > RED_FLAG_SHARPE:
            # ⚠️ Ye jaan-boojh ke FAIL hai, flag nahi. Auto-picker risk-adjusted return pe
            # sizing karta hai → jiska backtest sabse SMOOTH (yaani sabse shaqi) hoga, usi
            # ko sabse zyada lots milenge. Repo ka apna rule: Sharpe > 4 = data-artifact ka
            # shak (held-strike fallback, lookahead, thin sample). Aise number pe apne aap
            # paisa size karna theek ulta kaam hai — pehle verify, phir haath se add karo.
            fails.append(f"Sharpe {sharpe:.2f} > {RED_FLAG_SHARPE} — auto-basket se bahar "
                         f"(data-artifact ka shak; verify karke manually add karo)")
        if cost_basis.startswith("BS"):
            flags.append("real-cost recost nahi — number thoda optimistic ho sakta hai")
        if r.get("caveat"):
            flags.append(str(r["caveat"])[:110])

        verdict = "rejected" if fails else ("weak" if flags else "eligible")
        # sirf flags ho (fail na ho) to bhi eligible — par warning ke saath
        if not fails and flags:
            verdict = "eligible"

        cap_per_lot = None
        if _econ is not None:
            try:
                cap_per_lot = (_econ.economics(slug) or {}).get("capital_per_lot")
            except Exception:
                cap_per_lot = None

        wired = bool(ck and live.get("configured"))
        if not wired:
            status = "no-live-file"      # research-only: pehle live trader banana padega
        elif live.get("mode") == "live":
            status = "live"
        else:
            status = "paper"

        out.append({
            "id": sid, "label": name, "slug": slug, "config_key": ck,
            "in_portfolio": bool(sid and sid in member_ids),
            "status": status, "wired": wired,
            "cur_lots": live.get("lots") or 0, "on": live.get("on"),
            "sharpe": round(sharpe, 2) if sharpe is not None else None,
            "cost_basis": cost_basis,
            "p_value": p, "trades": int(trades),
            "train_sharpe": round(train, 2) if train is not None else None,
            "oos_sharpe": round(oos, 2) if oos is not None else None,
            "maxdd_pct": round(maxdd, 2) if maxdd is not None else None,
            "net_pct": round(net_pct, 1) if net_pct is not None else None,
            "capital_per_lot": cap_per_lot,
            "window": r.get("window"), "tf": r.get("tf"),
            "verdict": verdict, "fails": fails, "flags": flags,
            "capacity_lots": DEFAULT_CAPACITY,
        })

    order = {"eligible": 0, "weak": 1, "rejected": 2}
    out.sort(key=lambda c: (order.get(c["verdict"], 3), -(c["sharpe"] or -99)))
    return out


def eligible_members(include_unwired=False):
    """
    Solver ke liye member-shaped list — sirf gate-paas candidates.

    `include_unwired=False` (default): jinke paas live trader/config wiring hi nahi hai
    unhe chhod deta hai — unpe lots suggest karna jhooth hota (koi unhe chala hi nahi
    sakta jab tak live file na bane). Wo `scan()` me alag se "no-live-file" dikhti hain.
    """
    out = []
    for c in scan():
        if c["verdict"] != "eligible":
            continue
        if not include_unwired and not c["wired"]:
            continue
        out.append({
            "id": c["id"] or c["slug"], "label": c["label"], "slug": c["slug"],
            "config_key": c["config_key"], "run_lots": None,
            "capacity_lots": c["capacity_lots"], "mode": c["status"],
            "cur_lots": c["cur_lots"], "book": 0,
            "candidate": True, "in_portfolio": c["in_portfolio"],
        })
    return out


def summary():
    cs = scan()
    el = [c for c in cs if c["verdict"] == "eligible"]
    return {
        "total": len(cs),
        "eligible": len(el),
        "eligible_live": len([c for c in el if c["status"] == "live"]),
        "eligible_paper": len([c for c in el if c["status"] == "paper"]),
        "eligible_unwired": len([c for c in el if not c["wired"]]),
        "rejected": len([c for c in cs if c["verdict"] == "rejected"]),
    }


if __name__ == "__main__":
    cs = scan()
    print(json.dumps(summary(), indent=1))
    for c in cs:
        mark = {"eligible": "OK ", "weak": "~~ ", "rejected": "XX "}[c["verdict"]]
        print(f"{mark}{(c['id'] or '--'):<9} {c['label'][:44]:<44} "
              f"Sh {str(c['sharpe']):>5} p {str(c['p_value']):>6} "
              f"tr {c['trades']:>5} {c['status']:<12} "
              + ("; ".join(c["fails"]) or "; ".join(c["flags"]))[:80])
