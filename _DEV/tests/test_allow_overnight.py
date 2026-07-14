"""ADR-006 safety regression — prove the overnight lane is PURELY ADDITIVE:
every strategy NOT explicitly flagged still gets the blanket 3:15 squareoff.

Tests (a) risk_gate.allow_overnight() defaults + fail-safe, and (b) the exact
skip-decision boolean used in pos_monitor_loop's EOD block.
"""
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "_core")))
import importlib
rg = importlib.import_module("risk_gate")

def patch_cfg(cfg):
    rg._risk_cfg = lambda: cfg

# ── allow_overnight() helper ───────────────────────────────────────────────
patch_cfg({})
assert rg.allow_overnight("vrp_v1") is False, "no config → default False"
assert rg.allow_overnight("") is False and rg.allow_overnight(None) is False

patch_cfg({"per_strategy": {"vrp_v1": {"allow_overnight": True}}})
assert rg.allow_overnight("vrp_v1") is True, "per-strategy true → True"
assert rg.allow_overnight("orb_v1") is False, "OTHER strategy unaffected → False"

patch_cfg({"global": {"allow_overnight": True}})   # global switch must NOT work
assert rg.allow_overnight("vrp_v1") is False, "no global switch by design → False"

def _boom(): raise RuntimeError("bad config")
rg._risk_cfg = _boom
assert rg.allow_overnight("vrp_v1") is False, "exception → fail-safe False"

# ── exact pos_monitor_loop skip-decision boolean ───────────────────────────
patch_cfg({"per_strategy": {"vrp_v1": {"allow_overnight": True}}})
def skip_eod(owner, exp_day):
    """mirrors trader_dashboard.py pos_monitor_loop EOD block exactly."""
    try:
        return bool(owner) and rg.allow_overnight(owner) and not exp_day
    except Exception:
        return False

# THE CRITICAL PROPERTY: anyone not flagged is ALWAYS squared off (skip=False)
assert skip_eod("orb_v1", False) is False, "🔴 non-flagged intraday MUST square off"
assert skip_eod("chainzone_v1", False) is False, "🔴 non-flagged MUST square off"
assert skip_eod("rsi_v1", False) is False, "🔴 non-flagged MUST square off"
assert skip_eod("", False) is False, "empty owner → square off"
assert skip_eod(None, False) is False, "no owner → square off"
# flagged strategy
assert skip_eod("vrp_v1", False) is True, "flagged + non-expiry → HOLD overnight"
assert skip_eod("vrp_v1", True) is False, "🔴 flagged BUT expiry day → MUST square off"

print("ALL PASS — overnight lane is additive; non-flagged strategies still square off at 3:15;")
print("           flagged strategy holds only on non-expiry days; fail-safe on any error.")
