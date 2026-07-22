#!/usr/bin/env python3
"""
Regression test for the multi-leg atomicity fix (2026-07-22).

Two independent things are verified:

  PART A — the PREREQUISITE: order_store.record() must actually PERSIST group_id.
    `_COLS` (the INSERT column list) once omitted "group_id" even though record()
    built row["group_id"] and the DB column existed, so every row was written
    with the column default ("") no matter what the caller passed — silently
    disabling every group-aware feature (multi-leg atomic close in _do_squareoff,
    hedge-orphan protection / TRAP #30, broker_sync S5 naked-leg alert, the UI's
    group-close button). Records two legs of a straddle (shared group_id) + one
    standalone leg, reads them back through trades_for()->_net_rows(), and asserts
    the group'd legs surface WITH their group_id while the standalone stays "".

  PART B — the FIX: _queue_group_siblings() (trader_dashboard.py) selects the
    right still-open siblings and queues them with the "<reason>_GROUP" tag, and
    is a strict no-op when group_id is unset / no siblings match. The REAL shipped
    function body is extracted from trader_dashboard.py and exec'd against stubs
    (no heavy dashboard import) so we test the actual code, not a re-implementation.

Run:  python _DEV/tests/test_group_id_atomicity.py
Exit code 0 = all pass.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # _DEV/tests/<file> -> repo root
sys.path.insert(0, str(ROOT / "_core"))

_fails = []
def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        _fails.append(msg)


# ─────────────────────────────────────────────────────────────────────────────
# PART A — order_store persists group_id end-to-end
# ─────────────────────────────────────────────────────────────────────────────
print("PART A — order_store.record() persists group_id, readers surface it")
import order_store as osz

osz.DB_PATH = Path(tempfile.mkdtemp()) / "trades.db"
osz.init_db()

today = osz.ist_now_str()[:10]
GID = "STRDL_TEST_1"

# Long straddle (straddle_trader pattern: BUY CE + BUY PE, shared gid)
osz.record(side="BUY", qty=75, price=120.0, source="strategy", strategy="straddle_v1",
           mode="paper", broker="kite", symbol="NIFTY", instrument="options",
           trad_sym="NIFTY-24500-CE", sec_id="1111", segment="NSE_FNO",
           status="paper", group_id=GID)
osz.record(side="BUY", qty=75, price=110.0, source="strategy", strategy="straddle_v1",
           mode="paper", broker="kite", symbol="NIFTY", instrument="options",
           trad_sym="NIFTY-24500-PE", sec_id="2222", segment="NSE_FNO",
           status="paper", group_id=GID)
# Control: standalone leg, NO group_id
osz.record(side="SELL", qty=75, price=90.0, source="strategy", strategy="rsi_v1",
           mode="paper", broker="kite", symbol="NIFTY", instrument="options",
           trad_sym="NIFTY-24600-CE", sec_id="3333", segment="NSE_FNO",
           status="paper", group_id="")

opens = osz.trades_for(today).get("open", [])
by_sym = {o["sym"]: o for o in opens}

check("NIFTY-24500-CE" in by_sym and "NIFTY-24500-PE" in by_sym,
      "both straddle legs surface as open positions")
ce = by_sym.get("NIFTY-24500-CE", {})
pe = by_sym.get("NIFTY-24500-PE", {})
ctl = by_sym.get("NIFTY-24600-CE", {})
check(ce.get("group_id") == GID, f"CE leg carries group_id (got {ce.get('group_id')!r})")
check(pe.get("group_id") == GID, f"PE leg carries group_id (got {pe.get('group_id')!r})")
check(ctl.get("group_id") == "", f"standalone leg has empty group_id (got {ctl.get('group_id')!r})")
sibs = [o for o in opens if o.get("group_id") == GID and o is not ce]
check(len(sibs) == 1 and sibs[0]["sym"] == "NIFTY-24500-PE",
      "given CE leg, exactly its PE sibling is found by group_id match")


# ─────────────────────────────────────────────────────────────────────────────
# PART B — the real _queue_group_siblings() body, tested against stubs
# ─────────────────────────────────────────────────────────────────────────────
print("\nPART B — _queue_group_siblings() sibling-selection + reason suffix")
src = (ROOT / "trader_dashboard.py").read_text(encoding="utf-8")
m = re.search(r"\ndef _queue_group_siblings\(.*?(?=\n(?:def |# ──|@))", src, re.S)
assert m, "could not extract _queue_group_siblings from trader_dashboard.py"

queued = []   # (sym, sec_id, reason)
ns = {
    "_pgc_queue": lambda p, sec_id, reason: queued.append((p.get("sym"), sec_id, reason)),
    "print": lambda *a, **k: None,
}
exec(m.group(0), ns)
_qgs = ns["_queue_group_siblings"]

def leg(id, sym, sec, gid):
    return {"id": id, "sym": sym, "sec_id": sec, "group_id": gid}

# 1 — 2-leg straddle: firing on CE queues exactly the PE sibling.
queued.clear()
L_ce = leg(1, "NIFTY-24500-CE", "1111", "G1")
L_pe = leg(2, "NIFTY-24500-PE", "2222", "G1")
_qgs(L_ce, [L_ce, L_pe], set(), "DEFAULT_TSL_SL:-2000")
check(queued == [("NIFTY-24500-PE", "2222", "DEFAULT_TSL_SL:-2000_GROUP")],
      f"2-leg: PE sibling queued once with _GROUP reason (got {queued})")

# 2 — no group_id => strict no-op (keeps single-leg strategies safe).
queued.clear()
solo = leg(1, "RELIANCE-CE", "9", "")
_qgs(solo, [solo, leg(2, "OTHER", "8", "")], set(), "TRAILING_PROFIT_LOCK_PI")
check(queued == [], f"no group_id => nothing queued (got {queued})")

# 3 — sibling already closed this pass => not re-queued.
queued.clear()
_qgs(L_ce, [L_ce, L_pe], {2}, "TRAILING_PROFIT_LOCK_PI")
check(queued == [], f"sibling in closed_ids => not queued (got {queued})")

# 4 — different group_id => not a sibling.
queued.clear()
_qgs(L_ce, [L_ce, leg(3, "NIFTY-24700-PE", "7777", "G2")], set(), "X")
check(queued == [], f"different group_id => not queued (got {queued})")

# 5 — 4-leg iron condor: firing on one leg queues the other three.
queued.clear()
c1 = leg(1, "SELL-CE", "a", "C1"); c2 = leg(2, "BUY-CE", "b", "C1")
c3 = leg(3, "SELL-PE", "c", "C1"); c4 = leg(4, "BUY-PE", "d", "C1")
_qgs(c1, [c1, c2, c3, c4], set(), "TRAILING_PROFIT_LOCK_PI")
qsyms = sorted(x[0] for x in queued)
check(qsyms == ["BUY-CE", "BUY-PE", "SELL-PE"] and all(r.endswith("_GROUP") for _, _, r in queued),
      f"4-leg condor: the 3 siblings queued with _GROUP (got {queued})")

# 6 — sibling missing sec_id => skipped (can't queue without a key).
queued.clear()
_qgs(L_ce, [L_ce, {"id": 5, "sym": "BAD", "sec_id": None, "group_id": "G1"}], set(), "X")
check(queued == [], f"sibling without sec_id => skipped (got {queued})")


print("\n" + ("ALL PASS" if not _fails else f"{len(_fails)} FAILED"))
sys.exit(1 if _fails else 0)
