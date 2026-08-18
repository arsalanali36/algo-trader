import sys, os
_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, _ROOT)
import _paths  # noqa: F401  — puts _core/_data/brokers on sys.path (flat imports)
import leg_collision as lc
import strategy_safety as ss
import execution_gateway as gw

fails = []
def ok(name, cond):
    print(("PASS " if cond else "FAIL ") + name); (fails.append(name) if not cond else None)

# ── occupied_sec_ids: LIVE-only, excludes self / paper / CAPITAL_BLOCKED / 0-qty ──
class _FakeOS:
    OPEN = [
        {"strategy": "bnf_strangle_hedged", "sec_id": "S_LIVE_OTHER", "qty": 25, "mode": "live", "tags": []},
        {"strategy": "straddle_alert_hedged", "sec_id": "S_SELF", "qty": 25, "mode": "live", "tags": []},
        {"strategy": "straddle_920", "sec_id": "S_PAPER", "qty": 25, "mode": "paper", "tags": []},
        {"strategy": "x", "sec_id": "S_BLOCKED", "qty": 25, "mode": "live", "tags": ["CAPITAL_BLOCKED"]},
        {"strategy": "y", "sec_id": "S_ZERO", "qty": 0, "mode": "live", "tags": []},
    ]
    @staticmethod
    def trades_for(_d): return {"open": _FakeOS.OPEN, "details": []}
sys.modules["order_store"] = _FakeOS

occ = lc.occupied_sec_ids("straddle_alert_hedged")   # exclude self
ok("live OTHER-strategy leg occupies", "S_LIVE_OTHER" in occ)
ok("self excluded", "S_SELF" not in occ)
ok("paper leg does NOT occupy (broker fungibility is live-only)", "S_PAPER" not in occ)
ok("CAPITAL_BLOCKED excluded", "S_BLOCKED" not in occ)
ok("zero-qty excluded", "S_ZERO" not in occ)

# ── clear_leg: shift OTM off a shared contract; abort when unavoidable ──
def _res(sy, sp, ot, off): return (f"SEC{off}", f"{sy}-{ot}-{off}", 25)
ok("clear base when empty avoid", lc.clear_leg("BNF", 1, "CE", 3, set(), _res)[0] == "SEC3")
r = lc.clear_leg("BNF", 1, "CE", 0, {"SEC0", "SEC1"}, _res, max_shift=4)
ok("shift past two occupied contracts", r[0] == "SEC2" and r[3] == 2)
allocc = {f"SEC{k}" for k in range(30)}
ok("abort when no clear strike within max_shift", lc.clear_leg("BNF", 1, "CE", 0, allocc, _res, 4)[0] is None)

# ── wing_by_delta: avoid set skips an occupied wing strike ──
import dhan_master as dm
_orig_ex = dm.get_option_contract_ex
def _fake_ex(sym, spot, ot, off):
    return (f"W{off}", f"{sym}-{ot}-{off}", 25, 100.0 + off, "2026-08-26 15:30:00")
dm.get_option_contract_ex = _fake_ex
try:
    # bs/T likely unavailable in test → falls back to first RESOLVABLE (=min_strikes floor);
    # with the floor occupied, it must skip to the next clear strike.
    floor_off = 0 + max(2, 1)                       # sold_offset 0 + min_strikes 2
    w = ss.wing_by_delta("BNF", 1, "CE", 0, 0.25, None, "2026-08-26 15:30:00",
                         min_strikes=2, avoid={f"W{floor_off}"})
    ok("wing_by_delta skips an occupied floor strike", w[0] is not None and w[0] != f"W{floor_off}")
finally:
    dm.get_option_contract_ex = _orig_ex

# ── gateway MAIN-GATE: refuse a live strategy leg on another strategy's contract ──
import market_calendar as _mc
_mc.is_trading_day = lambda *a, **k: True
lc.occupied_sec_ids = lambda *a, **k: {"COLLIDE"}      # force a collision
r = gw.execute_signal("some_strat", "BANKNIFTY", "SELL", 1, 25, "COLLIDE", "BNF-CE",
                      mode="live", source="strategy", gate=False)
ok("live strategy leg on shared contract is BLOCKED", r["status"] == "blocked" and r["reason"] == "leg_collision")
# manual order on the same contract is the user's own call → NOT collision-blocked
r2 = gw.execute_signal("manual", "BANKNIFTY", "SELL", 1, 25, "COLLIDE", "BNF-CE",
                       mode="live", source="manual", gate=False)
ok("manual order is NOT collision-blocked (user intent)", r2["reason"] != "leg_collision")
# paper strategy entry never reaches the broker → NOT collision-blocked
r3 = gw.execute_signal("some_strat", "BANKNIFTY", "SELL", 1, 25, "COLLIDE", "BNF-CE",
                       mode="paper", source="strategy", gate=False)
ok("paper entry is NOT collision-blocked", r3["reason"] != "leg_collision")

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAIL: {fails}"))
sys.exit(1 if fails else 0)
