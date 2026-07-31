"""
test_partial_netting.py — quantity-aware netting in order_store._net_rows (TRAP #167).

Bug: a partial manual close (3 lots held → sell 2 on the broker) was netted as a FULL
round-trip (the whole entry row popped, qty = entry qty), so the remaining 1 lot vanished
from open positions → app thought the position was flat → it stopped managing / signalling
while the live broker position stayed open.

Fix: FIFO netting closes only min(exit_qty, entry_qty); the remainder stays OPEN with the
reduced qty. Equal-qty round-trips must be byte-identical (strict superset).

Run: python -X utf8 _DEV/tests/test_partial_netting.py
"""
import sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_core"))

_fails = []
def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    if not cond:
        _fails.append(msg)

import order_store as osz
osz.DB_PATH = Path(tempfile.mkdtemp()) / "trades.db"
osz.init_db()
DAY = osz.ist_now_str()[:10]

def rec(side, qty, price, strat, tsym, sec, source="strategy", status="filled"):
    osz.record(side=side, qty=qty, price=price, source=source, strategy=strat,
               mode="live", broker="kite", symbol=tsym.split("-")[0], instrument="options",
               trad_sym=tsym, sec_id=sec, segment="NSE_FNO", status=status)

def opens_for(tsym):
    d = osz.trades_for(DAY)
    return [o for o in d["open"] if o["sym"] == tsym]
def details_for(tsym):
    d = osz.trades_for(DAY)
    return [t for t in d["details"] if t["sym"] == tsym]

# ── 1. EQUAL round-trip (unchanged): BUY 195 then SELL 195, same strategy ──
print("1. equal-qty round-trip → 1 completed(195), 0 open")
rec("BUY", 195, 100.0, "orb_v1", "T1-Aug2026-24000-CE", "9001")
rec("SELL", 195, 120.0, "orb_v1", "T1-Aug2026-24000-CE", "9001")
d1, o1 = details_for("T1-Aug2026-24000-CE"), opens_for("T1-Aug2026-24000-CE")
check(len(o1) == 0, "equal: no open leftover")
check(len(d1) == 1 and d1[0]["qty"] == 195, "equal: one completed, qty 195")
check(d1 and d1[0]["pnl"] == round((120-100)*195, 2), "equal: pnl correct")

# ── 2. PARTIAL close (THE FIX): BUY 195 (3 lots), manual SELL 130 (2 lots) ──
print("2. partial close 3→sell2 → completed(130) + OPEN(65) remains")
rec("BUY", 195, 50.0, "pine2python_v1", "T2-Aug2026-24100-PE", "9002")
rec("SELL", 130, 60.0, "pine2python_v1", "T2-Aug2026-24100-PE", "9002", source="broker_reconcile")
d2, o2 = details_for("T2-Aug2026-24100-PE"), opens_for("T2-Aug2026-24100-PE")
check(len(o2) == 1 and o2[0]["qty"] == 65, "partial: 65 (1 lot) still OPEN  [got %s]" % ([o['qty'] for o in o2]))
check(len(d2) == 1 and d2[0]["qty"] == 130, "partial: closed leg qty = 130 (the 2 sold)")
check(o2 and o2[0]["entry"] == "BUY" and o2[0]["sec_id"] == "9002", "partial: remainder keeps side+sec_id")

# ── 3. OVER-CLOSE across two entries (FIFO): BUY 65 + BUY 130, SELL 160 ──
print("3. FIFO over-close: BUY65 + BUY130 then SELL160 → completed 65+95, OPEN 35")
rec("BUY", 65, 10.0, "orb_v1", "T3-Aug2026-24200-CE", "9003")
rec("BUY", 130, 10.0, "orb_v1", "T3-Aug2026-24200-CE", "9003")
rec("SELL", 160, 12.0, "orb_v1", "T3-Aug2026-24200-CE", "9003")
d3, o3 = details_for("T3-Aug2026-24200-CE"), opens_for("T3-Aug2026-24200-CE")
closed_qty = sorted(t["qty"] for t in d3)
check(closed_qty == [65, 95], "fifo: two completed 65 & 95  [got %s]" % closed_qty)
check(len(o3) == 1 and o3[0]["qty"] == 35, "fifo: 35 remains open  [got %s]" % ([o['qty'] for o in o3]))

# ── 4. TWO INDEPENDENT strategies same contract must NOT net (TRAP #145 preserved) ──
print("4. two strategies, same contract, opposite sides → both stay OPEN (no cross-net)")
rec("BUY", 75, 40.0, "straddle_v1", "T4-Aug2026-24300-CE", "9004")
rec("SELL", 75, 40.0, "backspread_v1", "T4-Aug2026-24300-CE", "9004")
o4 = opens_for("T4-Aug2026-24300-CE")
check(len(o4) == 2, "no-cross-net: both legs remain open (not phantom-closed)  [got %d]" % len(o4))

print()
if _fails:
    print("RESULT: %d FAIL" % len(_fails)); sys.exit(1)
print("RESULT: ALL PASS")
