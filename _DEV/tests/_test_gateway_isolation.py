"""
_test_gateway_isolation.py — Task 3/5 verification (2026-07-07).

Do fake strategies, SAME contract (sec_id) — cross-strategy interference nahi
honi chahiye. Monkeypatch-based, koi network/broker/DB call nahi. Run:

    python _test_gateway_isolation.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import broker_sync
import order_store
import execution_gateway
import risk_gate

PASS = FAIL = 0


def check(name, cond):
    global PASS, FAIL
    print(f"{'PASS' if cond else 'FAIL'}  {name}")
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


SID = "43892"
TSYM = "NIFTY-Jul2026-25200-CE"

# ── Fake order_store: Strategy A ka round-trip CLOSED, Strategy B ka lot OPEN ──
_fake_store = {
    "open": [
        {"strategy": "STRAT_B", "sec_id": SID, "sym": TSYM, "qty": 65, "tags": []},
    ],
    "details": [
        {"strategy": "STRAT_A", "sec_id": SID, "sym": TSYM, "qty": 65},  # closed round-trip
    ],
    "count": 1,
}
_orig_trades_for = order_store.trades_for
order_store.trades_for = lambda date, **f: _fake_store

try:
    # 1) _my_open_qty — per-strategy truth
    check("A (closed round-trip) -> confident 0", broker_sync._my_open_qty("STRAT_A", SID, TSYM) == 0)
    check("B (open row) -> 65", broker_sync._my_open_qty("STRAT_B", SID, TSYM) == 65)
    check("unknown strategy -> None (uncertain)", broker_sync._my_open_qty("STRAT_X", SID, TSYM) is None)

    # 2) is_flat, strategy-aware — THE over-sell scenario from the task list:
    #    A exit ho chuka, B ka lot broker-net ko non-zero rakhta hai.
    broker_sync._cache.clear()   # broker-level cache empty — sirf order_store bolega
    check("is_flat(A) = True (mera exit record hua — broker-net irrelevant)",
          broker_sync.is_flat("kite", TSYM, SID, strategy_id="STRAT_A") is True)
    check("is_flat(B) = False (mera lot open — no broker evidence)",
          broker_sync.is_flat("kite", TSYM, SID, strategy_id="STRAT_B") is False)
    check("is_flat without strategy_id = False (old behavior preserved)",
          broker_sync.is_flat("kite", TSYM, SID) is False)
    check("is_flat_fresh(A) = True via order_store (no broker call needed)",
          broker_sync.is_flat_fresh("kite", TSYM, SID, strategy_id="STRAT_A") is True)

    # 3) _known_broker_keys — qty DICT (TRAP #58 partial-untracked defeat fix)
    rows = [
        {"sec_id": SID, "qty": 65},           # Strategy A
        {"sec_id": SID, "qty": 65},           # Strategy B — same contract
        {"sec_id": "999", "qty": 30},
    ]
    known = broker_sync._known_broker_keys("dhan", rows)
    check("known qty summed across strategies (65+65=130)", known.get(SID) == 130)
    check("other contract independent", known.get("999") == 30)
    # partial-untracked math: broker 195 vs known 130 -> extra 65 untracked
    broker_qty = 195
    check("partial-untracked detected (broker 195 > known 130)", broker_qty > known[SID])
    check("extra qty = 65", broker_qty - known[SID] == 65)

    # 4) gateway mode="backtest" — deterministic max-premium gate
    _orig_cap = risk_gate.max_premium_cap_for
    risk_gate.max_premium_cap_for = lambda s: 100.0
    r1 = execution_gateway.execute_signal("STRAT_A", "BANKNIFTY", "BUY", 1, 30,
                                          SID, TSYM, mode="backtest", est_price=150.0)
    r2 = execution_gateway.execute_signal("STRAT_A", "BANKNIFTY", "BUY", 1, 30,
                                          SID, TSYM, mode="backtest", est_price=50.0)
    risk_gate.max_premium_cap_for = _orig_cap
    check("backtest gate: premium 150 > cap 100 -> blocked", r1["status"] == "blocked")
    check("backtest gate: premium 50 <= cap 100 -> ok", r2["ok"] is True)

    # 5) gateway execute_exit — skipped_flat pe smart_order call NAHI hota
    import smart_order as _so
    _orig_exec = _so.execute
    _called = {"n": 0}
    _so.execute = lambda *a, **k: (_called.__setitem__("n", _called["n"] + 1) or {"ok": True})
    _orig_gb = execution_gateway.get_broker
    execution_gateway.get_broker = lambda n: object()
    _orig_iff = broker_sync.is_flat_fresh
    broker_sync.is_flat_fresh = lambda *a, **k: True
    rx = execution_gateway.execute_exit("STRAT_A", "NIFTY", SID, TSYM, 65,
                                        entry_side="BUY", mode="live")
    broker_sync.is_flat_fresh = _orig_iff
    execution_gateway.get_broker = _orig_gb
    _so.execute = _orig_exec
    check("exit on already-flat -> skipped_flat", rx["status"] == "skipped_flat")
    check("no order fired on skipped_flat", _called["n"] == 0)

finally:
    order_store.trades_for = _orig_trades_for

print(f"\nRESULT: {PASS} PASS, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
