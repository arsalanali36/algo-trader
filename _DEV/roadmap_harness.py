"""
roadmap_harness.py — /roadmap page ka standalone render test (dev only).

REAL template + REAL engines (roadmap_portfolio / goal_planner / roadmap_daily) —
par apna chhota Flask app, taaki live dashboard/order-path ko haath na lage aur
login-gate ke bina browser me check ho sake.

APPLY yahan JAAN-BOOJH KE BLOCKED hai (config write kabhi harness se nahi).

Run: python -X utf8 _DEV/roadmap_harness.py   → http://127.0.0.1:5399/roadmap
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "_ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _paths  # noqa: F401,E402

from flask import Flask, jsonify, render_template, request  # noqa: E402
import roadmap_portfolio as rp   # noqa: E402
import goal_planner as gp        # noqa: E402
import roadmap_daily as rd       # noqa: E402

app = Flask(__name__, template_folder=os.path.join(_ROOT, "templates"),
            static_folder=os.path.join(_ROOT, "static"))


@app.route("/roadmap")
def page():
    return render_template("roadmap.html")


@app.route("/api/roadmap/portfolio")
def pf():
    return jsonify({"ok": True, "data": rp.build(
        request.args.get("to"), request.args.get("lane", "all"),
        request.args.get("lots_mode", "live"))})


@app.route("/api/roadmap/daily")
def daily():
    return jsonify({"ok": True, "data": rd.build()})


@app.route("/api/roadmap/goal", methods=["POST"])
def goal():
    b = request.get_json(force=True) or {}
    if b.get("scenarios"):
        ms0 = b.get("max_share")
        return jsonify({"ok": True, "scenarios": gp.scenarios(
            float(b.get("target") or 0), b.get("to_date"),
            float(b.get("dd_budget") or 0), b.get("scope", "all"),
            weights=b.get("weights") or None,
            max_share=(float(ms0) if ms0 else None))})
    ms = b.get("max_share")
    plan = gp.solve(float(b.get("target") or 0), b.get("to_date"),
                    float(b.get("dd_budget") or 0), b.get("scope", "all"),
                    p_goal=float(b.get("p_goal") or 60.0),
                    weights=b.get("weights") or None,
                    max_share=(float(ms) if ms else None))
    if plan.get("ok") and b.get("funding"):
        import sys, types
        # harness me live broker creds nahi — asli VPS funds ke numbers se stub
        sys.modules["risk_gate"] = types.SimpleNamespace(
            cash_headroom=lambda bn=None: {"ok": True, "cash_equiv": 463389.73,
                "capacity": 926779.46, "used": 462982.14, "headroom": 463797.32,
                "avail": 1023584.97},
            get_broker_balance=lambda bn: {"live_cash": 318543.93,
                "liquid_collateral": 144845.8, "collateral": 1081377.91},
            default_broker=lambda: "kite")
        plan["funding"] = gp.funding_check(plan)
    return jsonify({"ok": True, "plan": plan})


@app.route("/api/roadmap/candidates")
def cands():
    import strategy_candidates as sc
    return jsonify({"ok": True, "summary": sc.summary(), "candidates": sc.scan()})


@app.route("/api/roadmap/plan")
def plan():
    return jsonify({"ok": True, "active": gp.active_plan(), "history": []})


@app.route("/api/roadmap/plan/preview", methods=["POST"])
def preview():
    b = request.get_json(force=True) or {}
    return jsonify({"ok": True, "preview": gp.preview_apply(b.get("plan") or {})})


@app.route("/api/roadmap/plan/apply", methods=["POST"])
def apply_blocked():
    # harness kabhi config nahi likhta
    return jsonify({"ok": False, "reason": "HARNESS: apply disabled (config write blocked)"})


@app.route("/api/roadmap/plan/rollback", methods=["POST"])
def rb_blocked():
    return jsonify({"ok": False, "reason": "HARNESS: rollback disabled"})


@app.route("/api/roadmap")
def per_strategy():
    import roadmap as _rm
    sid = request.args.get("strategy")
    strategies = _rm.list_strategies()
    if not sid and strategies:
        sid = strategies[0]["id"]
    return jsonify({"ok": True, "strategies": strategies,
                    "data": _rm.build(sid) if sid else None})


if __name__ == "__main__":
    print("harness → http://127.0.0.1:5399/roadmap", flush=True)
    app.run(port=5399, debug=False, use_reloader=False)
