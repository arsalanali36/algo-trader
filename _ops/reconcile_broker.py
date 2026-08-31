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
    """order_store LIVE rows for date/broker: known broker_order_ids / trade_ids (from
    ANY status) + net per contract (from filled/open only).

    known_* is collected from EVERY status on purpose: a broker order the app has
    ALREADY recorded — even one whose row was later marked externally_closed /
    cancelled — must never be seen as 'external' and re-recorded. Excluding
    externally_closed here is exactly what let the reconciler re-adopt a manually-
    closed entry's OWN broker order as a phantom open position (KOTAKBANK 2026-07-28:
    entry row externally_closed → its order_id dropped from known → the broker's
    original BUY looked external → recorded again → app_net +6000 vs broker 0)."""
    con.row_factory = __import__("sqlite3").Row
    rows = con.execute(
        "select id,strategy,side,qty,price,trad_sym,sec_id,status,correlation_id,broker_order_id "
        "from orders where date=? and mode='live' and broker=?",
        (date, broker_name)).fetchall()
    known_oids = set()
    known_tids = set()
    net = defaultdict(int)   # contract -> signed qty (filled/open only)
    for r in rows:
        d = dict(r)
        if d.get("broker_order_id"):
            known_oids.add(str(d["broker_order_id"]))       # any status → known
        cid = str(d.get("correlation_id") or "")
        if cid.isdigit():
            known_tids.add(cid)                             # any status → known
        if d.get("status") in ("filled", "open"):
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


def _open_live_strategy(con, date, broker_name, trad_sym):
    """The single open LIVE strategy holding this contract (to attribute an external
    close). Returns the strategy if exactly one has net!=0 on it, else None → 'manual'."""
    con.row_factory = __import__("sqlite3").Row
    rows = con.execute(
        "select strategy, side, qty from orders where date=? and mode='live' and broker=? "
        "and trad_sym=? and status in ('filled','open')",
        (date, broker_name, trad_sym)).fetchall()
    net = defaultdict(int)
    for r in rows:
        net[r["strategy"]] += _sign(r["side"]) * int(r["qty"] or 0)
    live = [s for s, n in net.items() if n != 0]
    return live[0] if len(live) == 1 else None


def apply(date, broker_name="kite", dry_run=True, log=print):
    """Make the app's LIVE ledger match the broker trade book, authoritatively.

    Records each EXTERNAL broker order (one the app never recorded) EXACTLY ONCE,
    aggregated per order_id → ONE row of matched qty (netting-safe: the whack-a-mole
    came from split 65+65 fills a single SELL 130 couldn't pair; one order = one row
    fixes that), attributed to the contract's single open live strategy (else 'manual').
    Keyed by broker_order_id → idempotent (a re-run records nothing). LIVE only; PAPER
    is never touched. dry_run=True (default) plans only, writes nothing.

    Any net mismatch that REMAINS after recording externals (an app entry the broker
    has no record of) is REPORTED, not auto-closed — that's a separate reviewed step.
    """
    import sqlite3
    import order_store
    db_path = str(order_store.DB_PATH)
    p = plan(date, broker_name, db_path)
    con = sqlite3.connect(db_path)
    actions = []
    for o in p["external_orders"]:
        trad_sym = o.get("trad_sym")
        if not trad_sym:
            actions.append({"type": "skip", "reason": "unresolved-symbol", "order_id": o["order_id"]})
            continue
        strat = _open_live_strategy(con, date, broker_name, trad_sym) or "manual"
        actions.append({"type": "record", "order_id": o["order_id"], "side": o["side"],
                        "qty": o["qty"], "price": o["avg"], "trad_sym": trad_sym,
                        "sec_id": o.get("sec_id"), "strategy": strat})
        # SAME-time manual legs → ONE group (user rule 2026-07-30): a manual strangle/
        # pair placed together on the broker (mirrored here in the same cycle) shares a
        # symbol + IST-minute group_id so payoff / close-all treat it as one structure.
        # Only for 'manual' externals — strategy-attributed ones keep their own grouping.
        _rgid = ""
        if strat == "manual":
            from datetime import datetime as _dtm, timezone as _tzm, timedelta as _tdm
            _rmin = (_dtm.now(_tzm.utc) + _tdm(hours=5, minutes=30)).strftime("%Y%m%d%H%M")
            _rgid = "MANUAL_" + str(trad_sym).split("-")[0] + "_" + _rmin
        actions[-1]["group_id"] = _rgid
        if not dry_run:
            order_store.record(o["side"], o["qty"], o["avg"], source="broker_reconcile",
                               strategy=strat, mode="live", broker=broker_name,
                               trad_sym=trad_sym, sec_id=str(o.get("sec_id") or ""),
                               correlation_id=str(o["order_id"]), broker_order_id=str(o["order_id"]),
                               status="filled", tags=["EXTERNALLY_RECORDED", "BROKER_MIRROR"],
                               group_id=_rgid)
    con.close()
    after = plan(date, broker_name, db_path)   # re-check (post-write if applied)
    return {"actions": actions, "residual_mismatch": after["mismatch"], "dry_run": dry_run}


_last_mirror = 0.0
# 2026-09-01: 150s → 30s. At 150s a position placed by hand on Zerodha stayed
# invisible to the app for up to 2.5 min — and until it is in the ledger NO exit
# trigger can arm on it, so a manual trade sat unprotected for that whole window.
# Cost of the change, counted from the code: apply() makes 1-2 broker.trades()
# calls per run (resolve_dhan's kite.instruments("NFO") is day-cached, so it does
# not re-fetch) → ~120-240 Kite calls/hour vs Kite's ~3 req/s (10,800/hour) ceiling,
# ~2% of quota, and kite_rate_limiter gates it anyway. Kite read calls are not
# IP-whitelisted and share none of Dhan's budget. The "🔄 Sync from Broker" button
# stays the deterministic zero-latency path; this is the safety net behind it.
_MIRROR_INTERVAL = 30.0


def _in_window():
    """Market hours + a short tail (catch an end-of-day manual close). 09:15–15:50 IST."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= hm <= (15 * 60 + 50)


def mirror_if_due(broker_name="kite", log=print):
    """ROUTINE (called every pos_monitor tick, own ~2.5min cooldown): make the app's
    LIVE ledger match the broker automatically. Replaces the heuristic auto-reconcile
    that kept mis-recording manual closes. On each auto-reconcile it fires a bell notify
    (transparency: 'app detected your manual close'); anything ambiguous is flagged, not
    silently written. Fail-safe. LIVE only."""
    global _last_mirror
    import time
    if time.time() - _last_mirror < _MIRROR_INTERVAL:
        return
    _last_mirror = time.time()
    if not _in_window():
        return
    try:
        from datetime import datetime, timezone, timedelta
        date = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        r = apply(date, broker_name, dry_run=False, log=log)
        try:
            import notify
        except Exception:
            notify = None
        for a in r["actions"]:
            if a.get("type") == "record":
                log(f"[reconcile_broker] 🔁 auto-recorded external {a['side']} {a['qty']} "
                    f"{a['trad_sym']} @ {a['price']} → {a['strategy']} (order {a['order_id']})", flush=True)
                if notify:
                    notify.warn(f"🔁 Auto-reconcile: {a['strategy']} me {a['side']} {a['qty']} "
                                f"{a['trad_sym']} @ {a['price']} record kiya — ye Zerodha pe hua tha, "
                                f"ab app broker se match karta hai.",
                                key=f"recon_{a['order_id']}", source="reconcile")
            elif a.get("type") == "skip" and notify:
                notify.error(f"⚠️ Reconcile: broker order {a['order_id']} ka symbol resolve nahi hua — "
                             f"manual dekho.", key=f"recon_skip_{a['order_id']}", source="reconcile")
        for m in r.get("residual_mismatch", []):
            if notify:
                notify.error(f"⚠️ Reconcile: {m['contract']} app_net={m['app_net']} vs broker "
                             f"{m['broker_net']} (app-side phantom — broker ke paas ye nahi) — manual dekho.",
                             key=f"recon_resid_{m['contract']}", source="reconcile")
    except Exception as e:
        log(f"[reconcile_broker] mirror_if_due failed: {e}", flush=True)


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
