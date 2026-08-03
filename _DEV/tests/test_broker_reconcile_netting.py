"""
test_broker_reconcile_netting.py — a mirror-recorded broker close nets against the
strategy leg it closes (TRAP #170), WITHOUT reopening strategy-vs-strategy netting
(TRAP #145).

Root of the BAJFINANCE 2026-08-03 stuck phantom: rsi_v1_PAPER BOUGHT a CE (real
Kite order) and it was closed by a SELL. The authoritative mirror
(reconcile_broker.apply, ADR-011) recorded that SELL as a leg with
source='broker_reconcile' — but order_store netting only cross-paired
source='manual', so the real BUY and the real SELL could NEVER net → a permanent
phantom short (-2250) the "Sync from Broker" button couldn't clear.

Fix: 'broker_reconcile' joins _MANUAL_CLOSERS — a mirror leg IS broker truth, so it
closes whatever strategy leg it corresponds to on the same contract+account. A
second strategy's independent position (source='strategy') must still NOT net.

Run: python -X utf8 _DEV/tests/test_broker_reconcile_netting.py
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
    return [o for o in osz.trades_for(DAY)["open"] if o["sym"] == tsym]
def details_for(tsym):
    return [t for t in osz.trades_for(DAY)["details"] if t["sym"] == tsym]

# ── 1. THE BAJFINANCE CASE: strategy BUY + mirror-recorded SELL → 1 completed, 0 open ──
print("1. rsi BUY 2250 + broker_reconcile SELL 2250 → completed(+pnl), 0 open")
rec("BUY", 2250, 26.52, "rsi_v1_PAPER", "BAJFINANCE-Aug2026-1160-CE", "74230")
rec("SELL", 2250, 28.50, "manual", "BAJFINANCE-Aug2026-1160-CE", "74230", source="broker_reconcile")
o1, d1 = opens_for("BAJFINANCE-Aug2026-1160-CE"), details_for("BAJFINANCE-Aug2026-1160-CE")
check(len(o1) == 0, "no phantom open leg remains")
check(len(d1) == 1 and d1[0]["qty"] == 2250, "one completed round-trip, qty 2250")
check(d1 and round(d1[0]["pnl"], 2) == round((28.50 - 26.52) * 2250, 2),
      "pnl = (28.50-26.52)*2250 (matches broker booked)")

# ── 2. TRAP #145 PRESERVED: two independent strategy legs sharing a contract DON'T net ──
print("2. straddle SHORT CE + trigger LONG CE (both source='strategy') → NOT netted")
rec("SELL", 75, 100.0, "straddle_920", "T2-Aug2026-24000-CE", "8002")
rec("BUY", 75, 105.0, "manual_trigger", "T2-Aug2026-24000-CE", "8002")   # both source='strategy'
o2, d2 = opens_for("T2-Aug2026-24000-CE"), details_for("T2-Aug2026-24000-CE")
check(len(o2) == 2, "two independent strategy legs stay OPEN (no false cross-net)")
check(len(d2) == 0, "no phantom completed trade between two strategies")

# ── 3. mirror close still routes to the RIGHT strategy when one is same-strategy ──
print("3. same-strategy pair preferred over a mirror cross-close on same contract")
rec("BUY", 50, 10.0, "orb_v1", "T3-Aug2026-24000-PE", "8003")            # orb entry
rec("SELL", 50, 12.0, "orb_v1", "T3-Aug2026-24000-PE", "8003")           # orb's own exit (same-strategy)
o3, d3 = opens_for("T3-Aug2026-24000-PE"), details_for("T3-Aug2026-24000-PE")
check(len(o3) == 0 and len(d3) == 1, "same-strategy round-trip nets as before (unaffected)")

print()
if _fails:
    print(f"❌ {len(_fails)} FAIL")
    sys.exit(1)
print("✅ all pass")
