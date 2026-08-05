"""Standalone harness for /backtest-lab — real engine + real template + real static,
local lake only (no Dhan, no full dashboard, no session guard). For browser-pane verify."""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa
from flask import Flask, request, jsonify, render_template
import backtest_lab as bl

app = Flask(__name__,
            template_folder=os.path.join(ROOT, "templates"),
            static_folder=os.path.join(ROOT, "static"))


def _legs(raw):
    out = []
    for lg in (raw or []):
        try:
            out.append({"side": str(lg.get("side", "SELL")).upper(),
                        "opt": str(lg.get("opt", "CE")).upper(),
                        "off": int(lg.get("off", 0)), "lots": max(1, int(lg.get("lots") or 1)),
                        "sl_rs": (float(lg["sl_rs"]) if lg.get("sl_rs") not in (None, "", 0, "0") else None),
                        "tp_rs": (float(lg["tp_rs"]) if lg.get("tp_rs") not in (None, "", 0, "0") else None),
                        "trail_arm": (float(lg["trail_arm"]) if lg.get("trail_arm") not in (None, "", 0, "0") else None),
                        "trail_gap": (float(lg["trail_gap"]) if lg.get("trail_gap") not in (None, "", 0, "0") else None)})
        except Exception:
            continue
    return out


@app.route("/backtest-lab")
def page():
    return render_template("backtest_lab.html")


@app.route("/api/backtest-lab", methods=["POST"])
def run():
    b = request.get_json(force=True) or {}
    legs = _legs(b.get("legs"))
    if not legs:
        return jsonify({"ok": False, "reason": "no legs"})
    wd = b.get("weekdays")
    return jsonify(bl.run(str(b.get("underlying", "BANKNIFTY")).upper(), legs,
                          str(b.get("entry") or "09:20")[:5], str(b.get("exit") or "15:15")[:5],
                          str(b.get("from")), str(b.get("to")),
                          strat_sl=(float(b["strat_sl"]) if b.get("strat_sl") not in (None, "", 0, "0") else None),
                          strat_tp=(float(b["strat_tp"]) if b.get("strat_tp") not in (None, "", 0, "0") else None),
                          sqoff=str(b.get("sqoff") or "all"),
                          weekdays=set(int(x) for x in wd) if wd else None))


@app.route("/api/backtest-lab/intraday", methods=["POST"])
def intraday():
    b = request.get_json(force=True) or {}
    legs = _legs(b.get("legs"))
    if not legs:
        return jsonify({"ok": False, "reason": "no legs"})
    return jsonify(bl.intraday(str(b.get("underlying", "BANKNIFTY")).upper(), legs,
                               str(b.get("entry") or "09:20")[:5], str(b.get("exit") or "15:15")[:5],
                               str(b.get("date")),
                               strat_sl=(float(b["strat_sl"]) if b.get("strat_sl") not in (None, "", 0, "0") else None),
                               strat_tp=(float(b["strat_tp"]) if b.get("strat_tp") not in (None, "", 0, "0") else None),
                               sqoff=str(b.get("sqoff") or "all")))


if __name__ == "__main__":
    app.run(port=5311, debug=False, use_reloader=False)
