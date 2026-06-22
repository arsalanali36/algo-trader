#!/usr/bin/env python3
"""
order_store.py — persistent trade database (SQLite) for CODE3B.

Har order (webhook / manual / strategy, paper / live, Dhan / Kite) ek row me
record hota hai → future me kabhi bhi query/filter kar sakte hain. Logs as-is
rehte hain (parse_pnl backward-compat); ye DB tagged/filterable source-of-truth.

One row = one order leg (entry ya exit). `trades_for()` entry/exit ko net karke
completed trades + open positions banata hai (parse_pnl jaisa, par per-trade
source/strategy/mode/broker/tags ke saath).

Stdlib sqlite3 — koi ORM nahi. WAL mode → multiple strategy processes + dashboard
ek saath likh sakte hain.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "trades.db"
_lock = threading.Lock()

_COLS = ("ts", "date", "source", "strategy", "mode", "broker", "symbol",
         "instrument", "trad_sym", "sec_id", "segment", "side", "qty", "price",
         "correlation_id", "broker_order_id", "status", "tags")


def ist_now_str():
    n = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    return n.strftime("%Y-%m-%d %H:%M:%S")


def _conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _lock, _conn() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, date TEXT, source TEXT, strategy TEXT, mode TEXT, broker TEXT,
            symbol TEXT, instrument TEXT, trad_sym TEXT, sec_id TEXT, segment TEXT,
            side TEXT, qty INTEGER, price REAL, correlation_id TEXT,
            broker_order_id TEXT, status TEXT, tags TEXT)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_orders_date ON orders(date)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_orders_src ON orders(source)")


def record(side, qty, price, *, source="", strategy="", mode="paper", broker="dhan",
           symbol="", instrument="", trad_sym="", sec_id="", segment="",
           correlation_id="", broker_order_id="", status="paper", tags=None, ts=None):
    """Insert one order leg. Best-effort — never raises into the caller."""
    try:
        now = ts or ist_now_str()
        row = {
            "ts": now, "date": now[:10], "source": source, "strategy": strategy,
            "mode": mode, "broker": broker, "symbol": symbol, "instrument": instrument,
            "trad_sym": trad_sym, "sec_id": str(sec_id or ""), "segment": segment,
            "side": side, "qty": int(qty or 0), "price": float(price or 0),
            "correlation_id": correlation_id, "broker_order_id": broker_order_id or "",
            "status": status, "tags": json.dumps(tags or []),
        }
        with _lock, _conn() as c:
            c.execute(
                "INSERT INTO orders (" + ",".join(_COLS) + ") VALUES (" +
                ",".join("?" * len(_COLS)) + ")",
                tuple(row[k] for k in _COLS))
    except Exception as e:
        print("[order_store] record fail:", e, flush=True)


def query(date=None, source=None, mode=None, broker=None, strategy=None,
          instrument=None, tag=None, limit=5000):
    where, args = [], []
    for col, val in (("date", date), ("source", source), ("mode", mode),
                     ("broker", broker), ("strategy", strategy), ("instrument", instrument)):
        if val:
            where.append(f"{col}=?")
            args.append(val)
    sql = "SELECT * FROM orders"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id ASC LIMIT ?"
    args.append(limit)
    try:
        with _lock, _conn() as c:
            rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    except Exception as e:
        print("[order_store] query fail:", e, flush=True)
        return []
    if tag:
        rows = [r for r in rows if tag in _tags(r)]
    return rows


def _tags(row):
    try:
        return json.loads(row.get("tags") or "[]")
    except Exception:
        return []


def trades_for(date, **filters):
    """Net entry/exit legs into completed trades + open positions for a date.
    Pairing key = (source, strategy, trad_sym). Returns {details, open, count}."""
    rows = query(date=date, **filters)
    open_pos, details, opens = {}, [], []

    def _meta(r):
        return {"source": r["source"], "strategy": r["strategy"], "mode": r["mode"],
                "broker": r["broker"], "instrument": r["instrument"],
                "symbol": r["symbol"], "tags": _tags(r)}

    for r in rows:
        key = (r["source"], r["strategy"], r["trad_sym"])
        prev = open_pos.get(key)
        if prev and prev["side"] != r["side"]:
            ep, xp, q = prev["price"], r["price"], prev["qty"]
            pnl = (xp - ep) * q if prev["side"] == "BUY" else (ep - xp) * q
            d = {"sym": r["trad_sym"], "entry": prev["side"], "qty": q,
                 "entry_price": ep, "entry_time": prev["ts"][11:16],
                 "exit_price": xp, "exit_time": r["ts"][11:16], "pnl": round(pnl, 2)}
            d.update(_meta(r))
            details.append(d)
            open_pos.pop(key, None)
        else:
            open_pos[key] = r

    for r in open_pos.values():
        o = {"sym": r["trad_sym"], "entry": r["side"], "qty": r["qty"],
             "entry_price": r["price"], "entry_time": r["ts"][11:16],
             "exit_price": None, "exit_time": "—", "pnl": None}
        o.update(_meta(r))
        opens.append(o)

    return {"details": details, "open": opens, "count": len(details)}


def distinct(col, date=None):
    """Distinct values for a column (for filter dropdowns)."""
    if col not in ("source", "mode", "broker", "strategy", "instrument", "symbol"):
        return []
    sql = f"SELECT DISTINCT {col} FROM orders"
    args = []
    if date:
        sql += " WHERE date=?"
        args.append(date)
    try:
        with _lock, _conn() as c:
            return [r[0] for r in c.execute(sql, args).fetchall() if r[0]]
    except Exception:
        return []
