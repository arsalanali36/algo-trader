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

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "trades.db"
_lock = threading.Lock()

# NOTE: "group_id" MUST stay in this INSERT list. It was silently omitted for a
# long time even though record() builds row["group_id"], the DB column exists
# (init_db migration), and _meta()/readers surface it — so every row was written
# with the column's DEFAULT (NULL -> "") regardless of what the caller passed.
# That silently disabled EVERY group-aware feature (multi-leg atomic close in
# _do_squareoff, hedge-orphan protection / TRAP #30, broker_sync S5 naked-leg
# alert, the UI's group-close button). If you ever trim this tuple, keep group_id.
_COLS = ("ts", "date", "source", "strategy", "mode", "broker", "symbol",
         "instrument", "trad_sym", "sec_id", "segment", "side", "qty", "price",
         "correlation_id", "broker_order_id", "status", "tags", "product_type",
         "group_id")


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
        _real_fill = str(status or "").lower() not in (
            "blocked", "rejected", "cancelled", "canceled", "failed", "expired")
        if float(price or 0) <= 0 and _real_fill:
            # ASCII-only (no emoji) — a print that raises UnicodeEncodeError on a
            # non-UTF8 console would be caught below and SKIP the insert.
            print(f"[order_store] WARNING SUSPICIOUS 0-price {status} fill -- {side} {qty} "
                  f"{trad_sym or symbol} src={source} strat={strategy}. Premium fetch "
                  f"likely failed (DH-904). See LESSONS.md TRAP #1.", flush=True)
        # GUARD 2 (TRAP #1 family, 2026-07-29): an OPTION recorded at ~the underlying
        # INDEX/SPOT level (e.g. a NIFTY option at 24236) is never a real premium — a
        # caller fell back to the index quote when the option-premium fetch failed. ONE
        # such row (195q @ 24236) mis-paired 3 legs in netting into a phantom +15.6L and
        # made "almost every" completed trade's exit look wrong. This is the SINGLE
        # choke-point every order passes through, so REFUSING it here protects EVERY
        # current AND future code path (not just the one caller that regressed). Two
        # independent robust detections (a real option premium trips NEITHER):
        #   (a) within 5% of the underlying's live index spot (NIFTY/BANKNIFTY), or
        #   (b) price > 0.95 * strike  (universal — the index level always sits near the
        #       strike range; even a very deep-ITM real premium stays well under 0.95x).
        if _real_fill and float(price or 0) > 0:
            _ts = str(trad_sym or "")
            if _ts.endswith("-CE") or _ts.endswith("-PE"):
                _bad = False
                _px = float(price)
                try:
                    import shared_ltp_cache as _slc
                    _root = _ts.split("-")[0].strip().upper()
                    _spot = _slc.get_index(_root, max_age=86400)
                    if _spot and _spot > 0 and abs(_px - float(_spot)) / float(_spot) < 0.05:
                        _bad = True
                except Exception:
                    pass
                if not _bad:
                    try:
                        _strike = float(_ts.split("-")[-2])
                        if _strike > 0 and _px > 0.95 * _strike:
                            _bad = True
                    except (ValueError, IndexError):
                        pass
                if _bad:
                    print(f"[order_store] REJECT index-level option fill -- {side} {qty} {_ts} "
                          f"@ {_px} (~ underlying index level, not a real premium). Premium "
                          f"fetch fell back to the index. NOT recorded (would poison netting "
                          f"into a phantom P&L). See LESSONS.md TRAP #1.", flush=True)
                    try:
                        import notify
                        notify.error("indexpx_" + _ts,
                                     f"⚠️ {_ts} @ {_px:.0f} index-level price refused (premium "
                                     f"fetch fail) — order NOT recorded", source="order_store")
                    except Exception:
                        pass
                    return None
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


def update_fill(row_id, price=None, status=None, tags=None, broker_order_id=None):
    """Update a previously-recorded row's price/status/tags in place.

    Used by smart_order.execute()'s live path (TRAP #58/#62 root fix) — a
    provisional row is written the moment the broker ACCEPTS an order
    (before the ~8s fill-confirm poll even starts), tagged UNCONFIRMED_FILL.
    Once the poll resolves, this updates that same row: confirmed TRADED ->
    correct price + clear the tag; confirmed REJECTED -> status='rejected'
    (excluded from all P&L via _dead_filtered, correctly). If the poll times
    out either way, the row is simply left as-is — already a real 'filled'
    leg, already protected by pos_monitor_loop, already reconcilable by
    broker_sync — instead of never having existed at all.

    broker_order_id: also used by the order-chase (TRAP #64) — cancelling a
    stale unfilled limit and re-placing at a fresh price gets a NEW broker
    order id; this keeps the row pointing at whichever order is currently
    live so a later get_fill() poll checks the right one."""
    if row_id is None:
        return
    sets, args = [], []
    if price is not None:
        sets.append("price=?"); args.append(float(price))
    if status is not None:
        sets.append("status=?"); args.append(status)
    if tags is not None:
        sets.append("tags=?"); args.append(json.dumps(tags))
    if broker_order_id is not None:
        sets.append("broker_order_id=?"); args.append(str(broker_order_id))
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


# Exit-leg reason tags that pos_monitor_loop/webhook_executor/manual-close
# record on the exit leg — surfaced on completed trades so the UI can show WHY
# a trade exited. Expanded 2026-07-02 (grep-audited against every actual
# extra_tags=[...]/tag=.../reason=... call site in trader_dashboard.py,
# webhook_executor.py, broker_sync.py — was missing several real reasons that
# had been silently falling through to "" this whole time, e.g. KILL_FLOOR).
# A `_GROUP` suffix (hedge sibling auto-closed alongside its pair) still
# matches its base prefix here via startswith — no separate entry needed.
_EXIT_REASON_PREFIXES = (
    # pos_monitor_loop — per-position triggers
    "SL_HIT", "TP_HIT",
    # pos_monitor_loop — account/EOD/expiry-wide triggers
    "EOD_315_SQUAREOFF", "EXPIRY_EOD_SQUAREOFF", "EXPIRY_ITM_SQUAREOFF",
    "RMS_MAXLOSS", "RMS_PROFIT_TARGET", "KILL_FLOOR", "TRAILING_PROFIT_LOCK",
    "NO_PRICE_EMERGENCY_EXIT",
    # webhook_executor — TradingView-signal-driven strategies
    "TARGET", "TRAIL_SL", "IDX_TRAIL", "REVERSAL", "TV_EXIT",
    "GLOBAL_CAP", "SQUAREOFF_315",
    # manual / broker-detected
    "MANUAL_CLOSE", "EXTERNALLY_CLOSED", "MANUAL_EXIT_BROKER",
    # ── option-mission strategies (2026-07-16) ───────────────────────────────
    # These all TAG their reason correctly (execute_exit puts it in extra_tags)
    # — they were just never listed here, so _exit_reason() didn't recognise
    # them and the column stayed blank for every mission exit. Third time this
    # exact gap has bitten (TRAP #88 range/ATR_TRAILING, #91 rsi) — so these
    # are FAMILY prefixes, matched by startswith: a new <FAM>_TRAIL / _ROLLBACK
    # variant is covered the day it's written, without touching this list.
    # Add the family prefix here when you add a new strategy family.
    "ORB",        # orb_trader (ORB_SL/ORB_TARGET) + 03_orbst (ORBST_SL)
    "BNF",        # 07_banknifty (BNF_SL/BNF_TARGET)
    "CHAIN",      # 04_chainzone (CHAIN_SL)
    "STRADDLE",   # straddle_trader (_SL/_TP/_ROLLBACK)
    "STRANGLE",   # strangle_trader (_SL/_TP/_ROLLBACK) — 00.07 fwd-paper
    "DVERT",      # 02_debit_vertical (_SL/_TP/_ROLLBACK)
    "BSPRD",      # 05_backspread (_SL/_TP/_TRAIL/_ROLLBACK)
    "SVOL",       # 06_shortvol (_SL/_TP/_ROLLBACK)
    "VRP",        # vrp_straddle (VRP_*) + vrp_condor (VRPC_*)
    # range_trader.py — strategy's own ATR-trailing exit (2026-07-03: this
    # reason was computed + logged but never tagged on the actual exit
    # order, so this column stayed blank for every ATR-driven exit)
    "ATR_TRAILING",
    # 01_rsi_v1.py — strategy's own RSI-midline exit (same gap, same day:
    # close_position()'s smart_order.execute() call had no extra_tags at all)
    "RSI_MIDLINE_EXIT",
    # pos_monitor_loop — Default Target/SL exit profile (2026-07-04)
    "DEFAULT_TSL_TARGET", "DEFAULT_TSL_SL",
    # per-group combined-MTM auto-exit rule (2026-07-24, #02 payoff panel)
    "GROUP_TARGET", "GROUP_SL",
    # Auto-Rolling ATM Straddle (02.09) — ROLLER_ROLL_EXIT (buy-back on roll) +
    # ROLLER_ABORT (unwind on a failed roll/deploy). Family prefix (startswith).
    "ROLLER",
    # option mission strategies' own TP/SL/rollback exits (family prefixes —
    # each covers _TP / _SL / _ROLLBACK). These were tagged on the exit order
    # but never recognized here, so the Exit Reason column stayed blank for
    # every Long Straddle / Debit Vertical / Ratio Backspread / Short-Vol
    # Iron-Fly / VRP exit (task 71, same Critical-Rule-9 gap as ATR/RSI above).
    "STRADDLE_", "STRANGLE_", "DVERT_", "BSPRD_", "SVOL_", "VRP_",
)


def _exit_reason(row):
    """Best human-ish exit reason from an exit leg's tags. '' if none recorded
    (e.g. a plain manual/webhook close that didn't tag a reason)."""
    for t in _tags(row):
        for p in _EXIT_REASON_PREFIXES:
            if str(t).startswith(p):
                return t
    # A reconcile-inserted broker trade (source=manual / MANUAL_TRADE tag) acting
    # as the exit leg = the position was closed by hand at the broker. Surface it
    # as MANUAL_CLOSE instead of blank (Critical Rule 9 — no exit should read
    # blank; matches TRAP #92's source=manual -> MANUAL_CLOSE backfill convention).
    if str(row.get("source") or "").lower() == "manual" or "MANUAL_TRADE" in _tags(row):
        return "MANUAL_CLOSE"
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

    def _complete(entry_r, exit_r, q=None):
        # q = matched (closed) quantity. QUANTITY-AWARE: a partial exit (e.g. a manual
        # 2-lot close of a 3-lot position) closes only `q`, NOT the whole entry row —
        # the remainder stays OPEN so pos_monitor/RMS keep managing it and the app never
        # thinks a partially-reduced position is flat (TRAP #167). q=None → full entry qty
        # (equal-qty round-trip = byte-identical to the old behaviour).
        ep, xp = entry_r["price"], exit_r["price"]
        if q is None:
            q = entry_r["qty"]
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
    # BLOCKED rows (CAPITAL_BLOCKED — RMS rejected the entry, it never executed)
    # must ALSO be kept out of netting. Their price is often an index-level
    # placeholder, and FIFO-pairing a blocked leg against an unrelated real leg of
    # the same trad_sym produces a phantom ₹-lakh "completed trade" that further
    # corrupts daily-P&L / RMS profit-target (LESSONS.md TRAP #101). They surface
    # via `_as_open()` below so the "Capital se Block hui Entries" panel still sees
    # them — but they are NEVER a real completed-trade leg.
    _BLOCKED_ST = {"blocked"}
    live_rows    = [r for r in rows if str(r.get("status") or "").lower() in _OPEN_ST]
    blocked_rows = [r for r in rows if str(r.get("status") or "").lower() in _BLOCKED_ST]
    closed_rows  = [r for r in rows if str(r.get("status") or "").lower() not in _OPEN_ST
                    and str(r.get("status") or "").lower() not in _BLOCKED_ST]

    def _as_open(r, qty=None):
        # qty override = the REMAINING open quantity after a partial close (TRAP #167).
        # None → the row's full qty (unchanged for a never-partially-closed position).
        o = {"sym": r["trad_sym"], "entry": r["side"],
             "qty": r["qty"] if qty is None else qty,
             "entry_price": r["price"], "entry_time": r["ts"][11:16],
             "entry_date": r["ts"][:10],
             # sec_id = the ONLY unique contract key. trad_sym carries just month+year
             # (no expiry day) so two open positions can share it on different expiries
             # (weekly + monthly) → LTP/P&L must join on sec_id, never trad_sym (TRAP #166).
             "sec_id": r["sec_id"],
             "exit_price": None, "exit_time": "—", "pnl": None}
        o.update(_meta(r))
        return o

    def _q(r):
        try:
            return abs(int(r["qty"] or 0))
        except Exception:
            return 0

    # ── Pass 1: exact (source, strategy, trad_sym) round-trips, QUANTITY-AWARE ──
    # FIFO of still-open legs per key, each carrying its remaining qty [row, rem].
    # A partial exit closes only min(exit_rem, entry_rem) — the rest stays open, so a
    # manually-reduced position (3 lots → sell 2) is NEVER seen as flat (TRAP #167).
    open_fifo = {}    # key -> [[row, rem], ...] (chronological)
    for r in closed_rows:
        key = (r["source"], r["strategy"], r["trad_sym"])
        rem = _q(r)
        fifo = open_fifo.setdefault(key, [])
        # net r against the OLDEST opposite-side legs first
        while rem > 0 and fifo and fifo[0][0]["side"] != r["side"]:
            entry_r, erem = fifo[0]
            m = min(rem, erem)
            details.append(_complete(entry_r, r, m))
            rem -= m
            erem -= m
            if erem <= 0:
                fifo.pop(0)
            else:
                fifo[0][1] = erem
        if rem > 0:                       # same-side (pyramid) or nothing to net → stays open
            fifo.append([r, rem])
    leftover = []     # [row, rem] legs still open after pass 1
    for fifo in open_fifo.values():
        leftover.extend(fifo)
    leftover.sort(key=lambda x: x[0]["ts"])  # chronological for FIFO

    # ── Pass 2: net leftover opposite legs by (mode, trad_sym), FIFO, QUANTITY-AWARE ──
    # Cross-STRATEGY netting here is intentional ONLY for a genuine manual close
    # (Quick Order / reconcile, source='manual') that closes some strategy's leg
    # on the same contract+account. Two INDEPENDENT positions that merely SHARE a
    # contract — a straddle's long CE vs a price-trigger's short CE, or a
    # straddle's long PE vs a backspread's short PE — must NOT net into each
    # other; doing so produced phantom "exits" at the wrong time with blank
    # reasons and hid real legs (cross-strategy netting bug, 2026-07-22). Every
    # automated exit (SL/TP/EOD/lock/TSL + broker_sync ghost-close) records under
    # the position's OWN source+strategy, so a legit round-trip is ALWAYS
    # same-strategy — only a human/reconcile 'manual' leg ever crosses that line.
    _MANUAL_CLOSERS = {"manual"}
    stacks, opens = {}, []
    for r, rem in leftover:
        k2 = (r["mode"], r["trad_sym"])
        st = stacks.setdefault(k2, [])   # [[row, rem], ...]
        while rem > 0:
            # Oldest opposite-side leg we're ALLOWED to net against: prefer a
            # same-strategy match, else fall back to a manual cross-close. Scan the
            # whole stack (not just st[0]) so a same-strategy pair still nets even
            # when a foreign leg happens to sit in front of it.
            same_idx = manual_idx = None
            for i, (e, _er) in enumerate(st):
                if e["side"] == r["side"]:
                    continue
                if e["strategy"] == r["strategy"]:
                    same_idx = i
                    break
                if manual_idx is None and (e["source"] in _MANUAL_CLOSERS
                                           or r["source"] in _MANUAL_CLOSERS):
                    manual_idx = i
            idx = same_idx if same_idx is not None else manual_idx
            if idx is None:
                break
            e, erem = st[idx]
            m = min(rem, erem)
            details.append(_complete(e, r, m))   # e = entry (older)
            rem -= m
            erem -= m
            if erem <= 0:
                st.pop(idx)
            else:
                st[idx][1] = erem
        if rem > 0:
            st.append([r, rem])
    for st in stacks.values():
        for r, rem in st:
            opens.append(_as_open(r, qty=rem))

    # ── Blocked (CAPITAL_BLOCKED) rows → surface directly, never netted ──
    # (kept separate above so they can't FIFO-pair into a phantom completed trade)
    for r in blocked_rows:
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


def stats_summary(date_from=None, date_to=None, trades=None, **filters):
    """Aggregate Profit Factor / Expectancy / Sharpe over closed trades in a
    date range (live/paper order_store data — companion to the backtest-only
    `_compute_stats()` in `_TOOLS/backtest_engine.py`, same formula style for
    consistency, but a separate implementation since that one is backtest-only).

    profit_factor = sum(wins) / abs(sum(losses))
    expectancy    = win_rate*avg_win - loss_rate*avg_loss
    sharpe        = mean(pnl) / stdev(pnl) * sqrt(n)   — NOT annualized, same
                    non-annualized convention as backtest_engine's version.

    trades= — caller apna (already netted+filtered) trade list de sakta hai;
    tab date/filters ignore hote hain. Dashboard ka stats-summary route isi se
    resolve-aware strategy filtering ke baad WAHI trade set metrics me bhejta
    hai jo table dikhata hai (pills == table, exact). Display-only helper.
    """
    import statistics
    if trades is not None:
        details = trades
    elif date_from or date_to:
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


def update_tag_fields(order_id, set_fields):
    """Atomically MERGE tag changes into an order's CURRENT DB tags (read-modify-
    write under _lock) — the race-safe alternative to update_tags()' full-list
    overwrite. `set_fields` = {PREFIX: value}: each writes 'PREFIX:value',
    replacing any existing 'PREFIX:...'; value None drops that prefix.

    Why this exists: pos_monitor rewrote a position's ENTIRE tag array every ~5s
    from a start-of-cycle snapshot. If a user set SL/Target via the ⚙ modal
    (update_tags) mid-cycle, that write-back clobbered it — so manual/trigger
    positions (which carry no entry-time default SL) silently lost their gear-set
    SL within seconds (SL not shown in row/modal + exit reason '-'). Merging only
    the specific fields we own preserves everything else a concurrent writer set.
    """
    if not set_fields:
        return
    try:
        with _lock, _conn() as c:
            row = c.execute("SELECT tags FROM orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                return
            try:
                tags = json.loads(row[0] or "[]")
            except Exception:
                tags = []
            for pref, val in set_fields.items():
                p2 = str(pref) + ":"
                tags = [t for t in tags if not (isinstance(t, str) and t.startswith(p2))]
                if val is not None:
                    tags.append(f"{pref}:{val}")
            c.execute("UPDATE orders SET tags=? WHERE id=?", (json.dumps(tags), order_id))
            c.commit()
    except Exception as e:
        print("Error updating tag fields:", e)
