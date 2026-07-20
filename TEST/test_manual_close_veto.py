"""Fix B — manual-close veto. When the user closes a position (app button or a
Kite/external close broker_sync detects), the strategy/webhook must NOT re-open
that (strategy, symbol) for the rest of the day. Verified end-to-end through the
REAL gate_entry (the single chokepoint every strategy + webhook entry passes).

Uses a temp veto file — the real data/ dir is never written.
"""
import os
import sys
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
for _p in ("_data", "_core", "_ops", "brokers", "."):
    sys.path.insert(0, os.path.join(BASE, _p))

fails = []


def check(name, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + name)
    if not ok:
        print(f"          got={got!r} want={want!r}")
        fails.append(name)


import risk_gate
import strategy_safety

_fd, _tmp = tempfile.mkstemp(suffix=".json")
os.close(_fd)
os.remove(_tmp)
from pathlib import Path
risk_gate._manual_veto_path = lambda: Path(_tmp)   # isolate from real data/

try:
    print("\n=== A. mark / check / granularity ===")
    check("A0 clean start — not vetoed", risk_gate.is_manual_close_vetoed("arschain_MAIN", "NIFTY"), False)
    risk_gate.mark_manual_closed("arschain_MAIN", "NIFTY")
    check("A1 after mark — vetoed", risk_gate.is_manual_close_vetoed("arschain_MAIN", "NIFTY"), True)
    check("A2 case-insensitive symbol", risk_gate.is_manual_close_vetoed("arschain_MAIN", "nifty"), True)
    check("A3 different SYMBOL not vetoed", risk_gate.is_manual_close_vetoed("arschain_MAIN", "BANKNIFTY"), False)
    check("A4 different STRATEGY not vetoed", risk_gate.is_manual_close_vetoed("range_v1", "NIFTY"), False)

    print("\n=== B. gate_entry (the real chokepoint) BLOCKS a vetoed re-entry ===")
    ok, qty, reason = strategy_safety.gate_entry("arschain_MAIN", "NIFTY", 1, 65, 100.0,
                                                 side="SELL", mode="paper")
    check("B1 gate_entry blocked", ok, False)
    check("B2 qty 0", qty, 0)
    check("B3 reason names the veto", "veto" in (reason or "").lower(), True)

    print("\n=== C. clear → re-entry allowed again ===")
    n = risk_gate.clear_manual_veto("arschain_MAIN", "NIFTY")
    check("C1 cleared one", n, 1)
    check("C2 no longer vetoed", risk_gate.is_manual_close_vetoed("arschain_MAIN", "NIFTY"), False)
    # gate_entry now passes the veto gate (it may still block downstream for
    # config reasons, but the reason must NOT be the veto)
    ok2, _q2, reason2 = strategy_safety.gate_entry("arschain_MAIN", "NIFTY", 1, 65, 100.0,
                                                   side="SELL", mode="paper")
    check("C3 gate_entry no longer blocked BY VETO", "veto" in (reason2 or "").lower(), False)

    print("\n=== D. persistence across calls (survives a 'restart') ===")
    risk_gate.mark_manual_closed("vrp_condor_v1", "NIFTY")
    # simulate a fresh process reading the same file
    check("D1 veto persisted to disk", "vrp_condor_v1|NIFTY" in Path(_tmp).read_text(), True)
    check("D2 still vetoed on re-read", risk_gate.is_manual_close_vetoed("vrp_condor_v1", "NIFTY"), True)
finally:
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(_tmp + s)
        except OSError:
            pass

print()
if fails:
    print(f"RESULT: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("RESULT: all passed ✅")
