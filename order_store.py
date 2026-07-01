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
         "correlation_id", "broker_order_id", "status", "tags", "product_type")


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
        # Additive columns added after initial release — guarded so existing
        # DBs upgrade in place without losing data. Old rows get NULL, which
        # callers must treat as the documented default (see record()/usage).
        existing_cols = {r[1] for r in c.execute("PRAGMA table_info(orders)").fetchall()}
        for col, ddl in (("product_type", "TEXT"), ("group_id", "TEXT")):
            if col not in existing_cols:
                try:
                    c.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
                except Exception as e:
                    print(f"[order_store] add column {col} fail:", e, flush=True)


def record(side, qty, price, *, source="", strategy="", mode="paper", broker="dhan",
           symbol="", instrument="", trad_sym="", sec_id="", segment="",
           correlation_id="", broker_order_id="", status="paper", tags=None, ts=None,
           product_type="NRML", group_id=""):
    """Insert one order leg. Best-effort — never raises into the caller.

    product_type: "NRML" (default, intraday-style — gets 3:15 squareoff if
    options/index) or "CNC" (carry-forward — only meaningful for EQUITY,
    callers must force NRML for non-equity). Display/tracking only — not
    wired to the broker's actual order-placement productType param.
    group_id: links multiple legs (e.g. a sold option + its hedge) so the UI
    can show/close them together. Empty = standalone leg (current behavior).
    """
    try:
        now = ts or ist_now_str()
        # GUARD (LESSONS.md TRAP #1 — recurring ₹0-price phantom fill): a REAL
        # fill (paper/filled/live) at price<=0 is ALWAYS a bug — the premium
        # fetch failed and ₹0 fabricates P&L (it has tripped the RMS breaker and
        # force-squared-off real legs). Blocked/rejected rows legitimately carry
        # no price. We log LOUD rather than drop (dropping could desync a caller's
        # in-memory position) so ANY new code path that regresses is caught in the
        # logs immediately — this bug returned 4x in different files before this.
        if float(price or 0) <= 0 and str(status or "").lower() not in (
                "blocked", "rejected", "cancelled", "canceled", "failed", "expired"):
            # ASCII-only (no emoji) — a print that raises UnicodeEncodeError on a
            # non-UTF8 console would be caught below and SKIP the insert.
            print(f"[order_store] WARNING SUSPICIOUS 0-price {status} fill -- {side} {qty} "
                  f"{trad_sym or symbol} src={source} strat={strategy}. Premium fetch "
                  f"likely failed (DH-904). See LESSONS.md TRAP #1.", flush=True)
        row = {
            "ts": now, "date": now[:10], "source": source, "strategy": strategy,
            "mode": mode, "broker": broker, "symbol": symbol, "instrument": instrument,
            "trad_sym": trad_sym, "sec_id": str(sec_id or ""), "segment": segment,
            "side": side, "qty": int(qty or 0), "price": float(price or 0),
            "correlation_id": correlation_id, "broker_order_id": broker_order_id or "",
            "status": status, "tags": json.dumps(tags or []),
            "product_type": product_type or "NRML", "group_id": group_id or "",
        }
        with _lock, _conn() as c:
            cur = c.execute(
                "INSERT INTO orders (" + ",".join(_COLS) + ") VALUES (" +
                ",".join("?" * len(_COLS)) + ")",
                tuple(row[k] for k in _COLS))
            return cur.lastrowid
    except Exception as e:
        print("[order_store] record fail:", e, flush=True)
        return None


def update_fill(row_id, price=None, status=None, tags=None):
    """Update a previously-recorded row's price/status/tags in place.

    Used by smart_order.execute()'s live path (TRAP #58/#62 root fix) — a
    provisional row is written the moment the broker ACCEPTS an order
    (before the ~8s fill-confirm poll even starts), tagged UNCONFIRMED_FILL.
    Once the poll resolves, this updates that same row: confirmed TRADED ->
    correct price + clear the tag; confirmed REJECTED -> status='rejected'
    (excluded from all P&L via _dead_filtered, correctly). If the poll times
    out either way, the row is simply left as-is — already a real 'filled'
    leg, already protected by pos_monitor_loop, already reconcilable by
    broker_sync — instead of never having existed at all."""
    if row_id is None:
        return
    sets, args = [], []
    if price is not None:
        sets.append("price=?"); args.append(float(price))
    if status is not None:
        sets.append("status=?"); args.append(status)
    if tags is not None:
        sets.append("tags=?"); args.append(json.dumps(tags))
    if not sets:
        return
    args.append(row_id)
    try:
        with _lock, _conn() as c:
            c.execute(f"UPDATE orders SET {','.join(sets)} WHERE id=?", args)
    except Exception as e:
        print("[order_store] update_fill fail:", e, flush=True)


def query(date=None, date_from=None, date_to=None, source=None, mode=None, broker=None,
          strategy=None, instrument=None, tag=None, limit=5000):
    where, args = [], []
    for col, val in (("date", date), ("source", source), ("mode", mode),
                     ("broker", broker), ("strategy", strategy), ("instrument", instrument)):
        if val:
            where.append(f"{col}=?")
            args.append(val)
    if date_from:
        where.append("date>=?"); args.append(date_from)
    if date_to:
        where.append("date<=?"); args.append(date_to)
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


# Exit-leg reason tags that pos_monitor_loop records alongside "pos_monitor_exit"
# (and webhook/manual paths may add) — surfaced on completed trades so the UI can
# show WHY a trade exited (SL / target / EOD squareoff / RMS max-loss / trail).
_EXIT_REASON_PREFIXES = ("SL_HIT", "TP_HIT", "EOD_315_SQUAREOFF", "RMS_MAXLOSS",
                         "TRAIL_SL", "TARGET", "REVERSAL", "MANUAL_CLOSE", "TV_EXIT")


def _exit_reason(row):
    """Best human-ish exit reason from an exit leg's tags. '' if none recorded
    (e.g. a plain manual/webhook close that didn't tag a reason)."""
    for t in _tags(row):
        for p in _EXIT_REASON_PREFIXES:
            if str(t).startswith(p):
                return t
    return ""


def trades_for(date, **filters):
    """Net entry/exit legs into completed trades + open positions for a date.
    See `_net_rows()` for the netting algorithm. Returns {details, open, count}.
    """
    rows = _dead_filtered(query(date=date, **filters))
    return _net_rows(rows)


def trades_for_range(date_from, date_to, **filters):
    """Same as `trades_for()` but over an inclusive date range (for multi-day
    stats aggregation) — nets across the whole range at once, not per-day, so a
    trade whose entry/exit legs fall on different dates is still paired correctly.
    """
    rows = _dead_filtered(query(date_from=date_from, date_to=date_to, limit=200000, **filters))
    return _net_rows(rows)


def mark_externally_closed(row_id):
    """Mark a DB row as externally_closed (manually closed at broker / ghost position).
    broker_sync.py calls this when broker shows qty=0 for a DB-OPEN position (TRAP #44)."""
    with _lock, _conn() as c:
        c.execute("UPDATE orders SET status='externally_closed' WHERE id=?", (row_id,))


def _dead_filtered(rows):
    # Rejected/cancelled/failed/externally_closed orders = no real open position.
    # Inhe netting se bahar rakho (warna phantom open positions dikhte hain).
    _DEAD = {"rejected", "cancelled", "canceled", "failed", "expired", "externally_closed"}
    return [r for r in rows if str(r.get("status") or "").lower() not in _DEAD]


def _net_rows(rows):
    """Two-pass netting shared by `trades_for()`/`trades_for_range()`:
      Pass 1 — exact (source, strategy, trad_sym) round-trips. Ek strategy jo
               apni hi position open+close kare → clean pairing + attribution.
      Pass 2 — Pass 1 ke baad bache hue opposite legs ko (mode, trad_sym) pe
               FIFO net karo, chahe source/strategy alag ho. Isse Quick Order ka
               manual BUY ek webhook/strategy SELL ko bhi close kar deta hai
               (broker reality: same contract+account me sides net hote hain).
    Attribution = ENTRY (pehla) leg ka source/strategy.
    """
    details = []

    def _meta(r):
        return {"id": r["id"], "source": r["source"], "strategy": r["strategy"], "mode": r["mode"],
                "broker": r["broker"], "instrument": r["instrument"],
                "symbol": r["symbol"], "tags": _tags(r),
                "sec_id": r["sec_id"], "segment": r["segment"],
                "product_type": r["product_type"] or "NRML", "group_id": r["group_id"] or ""}

    def _complete(entry_r, exit_r):
        ep, xp, q = entry_r["price"], exit_r["price"], entry_r["qty"]
        pnl = (xp - ep) * q if entry_r["side"] == "BUY" else (ep - xp) * q
        d = {"sym": entry_r["trad_sym"], "entry": entry_r["side"], "qty": q,
             "entry_price": ep, "entry_time": entry_r["ts"][11:16],
             "entry_date": entry_r["ts"][:10], "exit_date": exit_r["ts"][:10],
             "exit_price": xp, "exit_time": exit_r["ts"][11:16], "pnl": round(pnl, 2)}
        d.update(_meta(entry_r))   # attribution from the entry leg
        d["exit_reason"] = _exit_reason(exit_r)   # WHY it closed (from exit leg)
        return d

    # OPEN-status rows = still-live positions; don't run them through netting.
    # Netting would pair a SELL OPEN + hedge BUY OPEN (same trad_sym/strategy)
    # → phantom completed trade (LESSONS.md TRAP #32).
    _OPEN_ST = {"open"}
    live_rows   = [r for r in rows if str(r.get("status") or "").lower() in _OPEN_ST]
    closed_rows = [r for r in rows if str(r.get("status") or "").lower() not in _OPEN_ST]

    def _as_open(r):
        o = {"sym": r["trad_sym"], "entry": r["side"], "qty": r["qty"],
             "entry_price": r["price"], "entry_time": r["ts"][11:16],
             "entry_date": r["ts"][:10],
             "exit_price": None, "exit_time": "—", "pnl": None}
        o.update(_meta(r))
        return o

    # ── Pass 1: exact (source, strategy, trad_sym) round-trips (closed only) ──
    open_pos = {}     # key -> currently-open entry row
    leftover = []     # legs not paired in pass 1
    for r in closed_rows:
        key = (r["source"], r["strategy"], r["trad_sym"])
        prev = open_pos.get(key)
        if prev and prev["side"] != r["side"]:
            details.append(_complete(prev, r))
            open_pos.pop(key, None)
        elif prev:                       # same side again (pyramid/dup) — bump prev
            leftover.append(prev)
            open_pos[key] = r
        else:
            open_pos[key] = r
    leftover.extend(open_pos.values())
    leftover.sort(key=lambda r: r["ts"])  # chronological for FIFO

    # ── Pass 2: net leftover opposite legs by (mode, trad_sym), FIFO ──
    stacks, opens = {}, []
    for r in leftover:
        k2 = (r["mode"], r["trad_sym"])
        st = stacks.setdefault(k2, [])
        if st and st[0]["side"] != r["side"]:
            details.append(_complete(st.pop(0), r))   # oldest open leg = entry
        else:
            st.append(r)
    for st in stacks.values():
        for r in st:
            opens.append(_as_open(r))

    # ── Live OPEN-status rows → directly open positions ──
    # Among SELL+BUY OPEN pairs for same trad_sym: show only SELL (main leg).
    # BUY is the hedge leg — no independent P&L to track.
    by_sym = {}
    for r in live_rows:
        by_sym.setdefault(r["trad_sym"], []).append(r)
    for sym_rows in by_sym.values():
        sells = [r for r in sym_rows if r["side"] == "SELL"]
        buys  = [r for r in sym_rows if r["side"] == "BUY"]
        for r in (sells if sells else buys):
            opens.append(_as_open(r))

    details.sort(key=lambda d: (d.get("entry_date", ""), d.get("entry_time", "")))
    return {"details": details, "open": opens, "count": len(details)}


def stats_summary(date_from=None, date_to=None, **filters):
    """Aggregate Profit Factor / Expectancy / Sharpe over closed trades in a
    date range (live/paper order_store data — companion to the backtest-only
    `_compute_stats()` in `_TOOLS/backtest_engine.py`, same formula style for
    consistency, but a separate implementation since that one is backtest-only).

    profit_factor = sum(wins) / abs(sum(losses))
    expectancy    = win_rate*avg_win - loss_rate*avg_loss
    sharpe        = mean(pnl) / stdev(pnl) * sqrt(n)   — NOT annualized, same
                    non-annualized convention as backtest_engine's version.
    """
    import statistics
    if date_from or date_to:
        details = trades_for_range(date_from or "0000-00-00", date_to or "9999-12-31", **filters)["details"]
    else:
        details = trades_for_range("0000-00-00", "9999-12-31", **filters)["details"]

    pnls = [d["pnl"] for d in details if d.get("pnl") is not None]
    n = len(pnls)
    if n == 0:
        return {"n_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "profit_factor": 0.0, "expectancy": 0.0, "sharpe": 0.0,
                "gross_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0}

    win_list = [p for p in pnls if p > 0]
    loss_list = [p for p in pnls if p <= 0]
    wins, losses = len(win_list), len(loss_list)
    win_rate = wins / n
    loss_rate = losses / n
    avg_win = (sum(win_list) / wins) if wins else 0.0
    avg_loss = (abs(sum(loss_list)) / losses) if losses else 0.0
    gross_loss = abs(sum(loss_list))
    profit_factor = round(sum(win_list) / gross_loss, 2) if gross_loss > 0 else (round(sum(win_list), 2) if win_list else 0.0)
    expectancy = round(win_rate * avg_win - loss_rate * avg_loss, 2)
    stdev = statistics.pstdev(pnls) if n > 1 else 0.0
    sharpe = round((statistics.mean(pnls) / stdev) * (n ** 0.5), 2) if stdev > 0 else 0.0

    return {
        "n_trades": n, "wins": wins, "losses": losses,
        "win_rate": round(win_rate * 100, 1),
        "profit_factor": profit_factor, "expectancy": expectancy, "sharpe": sharpe,
        "gross_pnl": round(sum(pnls), 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
    }


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

def delete_by_source(source):
    """Saare orders delete karo jinka source == given (health_check --fire-test
    apne 'healthtest' rows ko verify karne ke baad cleanup me use karta — production
    P&L kabhi pollute na ho). Returns deleted row count."""
    try:
        with _lock, _conn() as c:
            cur = c.execute("DELETE FROM orders WHERE source=?", (source,))
            return cur.rowcount
    except Exception as e:
        print("[order_store] delete_by_source fail:", e, flush=True)
        return 0

def update_tags(order_id, tags):
    """Updates the tags JSON string for a specific order ID."""
    try:
        with _lock, _conn() as c:
            c.execute("UPDATE orders SET tags=? WHERE id=?", (json.dumps(tags), order_id))
            c.commit()
    except Exception as e:
        print("Error updating tags:", e)
