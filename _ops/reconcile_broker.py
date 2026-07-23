"""
reconcile_broker.py — AUTHORITATIVE live reconciliation (WIP, read-only planner).

The broker's own trade book is the single source of truth for LIVE fills. No netting
heuristics, no fill-signature matching, no "fill already used" guessing — that whole
inference approach is exactly what kept breaking (arschain phantom, paper-ate-the-fill,
duplicate exits, "record nahi ho raha"). Here we MIRROR the broker instead of GUESSING.

Anchor: every order the APP places stores its broker order_id (order_store.broker_order_id),
and the broker gives every fill a unique (order_id, trade_id). So:
  - a broker order_id the app has a row for  = KNOWN   (already recorded)
  - a broker order_id the app has NO row for  = EXTERNAL (manual close/entry the app
    never recorded) → must be recorded once, keyed by order_id.
Idempotent by construction. LIVE only — PAPER is a separate simulated ledger, never here.

THIS FILE IS READ-ONLY (plan/verify). It writes NOTHING. It exists so the reconciliation
can be PROVEN on real data + the pre-incident backup before any write path or live cutover.

    python -X utf8 _ops/reconcile_broker.py [--date YYYY-MM-DD] [--db path]
"""
import os
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import _paths  # noqa
except Exception:
    pass


def _sign(side):
    return 1 if str(side).upper() in ("BUY", "B") else -1


def broker_orders(broker):
    """{order_id: {sym, side, qty, avg, trade_ids[], contract}} from the broker trade book."""
    out = {}
    agg = defaultdict(lambda: {"sym": None, "side": None, "qty": 0, "notional": 0.0, "tids": []})
    for t in broker.trades() or []:
        oid = str(t.get("order_id") or "")
        if not oid:
            continue
        q = int(t.get("quantity") or 0)
        px = float(t.get("average_price") or 0)
        a = agg[oid]
        a["sym"] = t.get("tradingsymbol") or t.get("trading_symbol")
        a["side"] = t.get("transaction_type")
        a["qty"] += q
        a["notional"] += q * px
        tid = str(t.get("trade_id") or "")
        if tid:
            a["tids"].append(tid)
    for oid, a in agg.items():
        out[oid] = {"order_id": oid, "sym": a["sym"], "side": a["side"], "qty": a["qty"],
                    "avg": round(a["notional"] / a["qty"], 2) if a["qty"] else 0.0,
                    "trade_ids": a["tids"]}
    return out


def app_live_rows(con, date, broker_name):
    """order_store LIVE rows for date/broker: broker_order_id, correlation_id, net per contract."""
    con.row_factory = __import__("sqlite3").Row
    rows = con.execute(
        "select id,strategy,side,qty,price,trad_sym,sec_id,status,correlation_id,broker_order_id "
        "from orders where date=? and mode='live' and broker=? and status in ('filled','open')",
        (date, broker_name)).fetchall()
    known_oids = set()
    known_tids = set()
    net = defaultdict(int)   # contract -> signed qty
    for r in rows:
        d = dict(r)
        if d.get("broker_order_id"):
            known_oids.add(str(d["broker_order_id"]))
        cid = str(d.get("correlation_id") or "")
        if cid.isdigit():
            known_tids.add(cid)
        net[d.get("trad_sym")] += _sign(d.get("side")) * int(d.get("qty") or 0)
    return known_oids, known_tids, net


def plan(date, broker_name="kite", db_path=None, log=print):
    """READ-ONLY. Returns external broker orders the app never recorded + a per-contract
    broker-net vs app-net comparison. Writes nothing."""
    import sqlite3
    from brokers import get_broker
    db_path = db_path or os.path.join(_ROOT, "data", "trades.db")
    broker = get_broker(broker_name)

    b_orders = broker_orders(broker)
    con = sqlite3.connect(db_path)
    known_oids, known_tids, app_net = app_live_rows(con, date, broker_name)
    con.close()

    # broker net per contract (resolve kite sym -> app trad_sym)
    broker_net = defaultdict(int)
    external = []
    for oid, o in b_orders.items():
        sec_id, trad_sym, lot = broker.resolve_dhan(o["sym"]) if o["sym"] else (None, None, None)
        contract = trad_sym or o["sym"]
        broker_net[contract] += _sign(o["side"]) * int(o["qty"])
        # KNOWN if app has this order_id, or any of its fills' trade_ids
        is_known = (oid in known_oids) or any(t in known_tids for t in o["trade_ids"])
        if not is_known:
            external.append({**o, "trad_sym": trad_sym, "sec_id": sec_id})

    # per-contract comparison
    contracts = sorted(set(broker_net) | set(app_net))
    mismatch = []
    for c in contracts:
        bn, an = broker_net.get(c, 0), app_net.get(c, 0)
        if bn != an:
            mismatch.append({"contract": c, "broker_net": bn, "app_net": an, "gap": an - bn})
    return {"external_orders": external, "mismatch": mismatch,
            "broker_net": dict(broker_net), "app_net": dict(app_net)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--db")
    ap.add_argument("--broker", default="kite")
    a = ap.parse_args()
    if not a.date:
        from datetime import datetime, timezone, timedelta
        a.date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    r = plan(a.date, a.broker, a.db)
    print(f"=== reconcile plan {a.date} ({a.broker}) — DB {a.db or 'live'} ===")
    print(f"EXTERNAL orders the app never recorded: {len(r['external_orders'])}")
    for o in r["external_orders"]:
        print(f"  order {o['order_id']}  {o['side']} {o['qty']} {o.get('trad_sym') or o['sym']} @ {o['avg']}  trade_ids={o['trade_ids']}")
    print(f"CONTRACT net mismatches (app vs broker): {len(r['mismatch'])}")
    for m in r["mismatch"]:
        print(f"  {m['contract']}: app_net={m['app_net']}  broker_net={m['broker_net']}  gap={m['gap']}")
    if not r["external_orders"] and not r["mismatch"]:
        print("  ✅ app LIVE ledger exactly mirrors the broker (nothing to reconcile).")
