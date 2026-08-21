"""Guard for the 2026-08-21 naked-leg incident (order_store.open_legs_in_group).

A hedged straddle's basket-SL resolved its 4-leg group as only 2 legs (because it
filtered the GLOBAL multi-day netting by group_id, and a prior-day leftover leg on
the same MONTHLY contract cross-netted today's leg away) → it closed 2 legs and
left the other short NAKED.

open_legs_in_group() resolves a group from its OWN ledger, so it is immune to any
other day / strategy / manual leg on the same contract. These tests assert:
  1. it returns the group's clean, full-qty legs even when a cross-day
     broker_reconcile leftover exists on one of the group's contracts, AND
  2. that same scenario genuinely CONTAMINATES the old global-filter path
     (so the test documents WHY the group-scoped resolver exists).

Run: python _DEV/tests/test_group_leg_resolution.py
"""
import sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "_core"))
import order_store as os_

os_.DB_PATH = Path(tempfile.gettempdir()) / "_test_group_leg_resolution.db"
if os_.DB_PATH.exists():
    os_.DB_PATH.unlink()
os_.init_db()

GID = "STRADH_BANKNIFTY_TEST"
# 4-leg hedged straddle placed TODAY (distinct monthly contracts, all qty 150)
LEGS = [
    # side, trad_sym, sec_id, price
    ("BUY",  "BANKNIFTY-Aug2026-58100-CE", "59111", 95.10),   # long CE wing
    ("BUY",  "BANKNIFTY-Aug2026-57100-PE", "59082", 86.40),   # long PE wing
    ("SELL", "BANKNIFTY-Aug2026-57600-CE", "59092", 301.05),  # short CE body
    ("SELL", "BANKNIFTY-Aug2026-57600-PE", "59093", 235.27),  # short PE body
]
for side, tsym, sec, px in LEGS:
    os_.record(side, 150, px, source="strategy", strategy="straddle_alert_hedged",
               mode="live", broker="kite", symbol="BANKNIFTY", instrument="options",
               trad_sym=tsym, sec_id=sec, segment="NSE_FNO", status="filled",
               group_id=GID, ts="2026-08-21 09:29:44")

# A leftover from a PRIOR day on the SAME monthly 57600-CE contract: a small manual
# broker-mirror BUY that never had a matching pair (the real 2026-08-17 q30 leg).
# It flows into the global netting (externally_closed is NOT dropped, TRAP #167b)
# and, being a manual-closer, FIFO-pairs against today's SELL — corrupting its qty.
os_.record("BUY", 30, 570.0, source="broker_reconcile", strategy="manual",
           mode="live", broker="kite", symbol="BANKNIFTY", instrument="options",
           trad_sym="BANKNIFTY-Aug2026-57600-CE", sec_id="59092", segment="NSE_FNO",
           status="externally_closed", group_id="MANUAL_BANKNIFTY_OLD",
           ts="2026-08-17 15:11:09")

fail = 0
def check(name, cond):
    global fail
    print(("  PASS" if cond else "  FAIL"), name)
    if not cond:
        fail += 1

# ── NEW: group-scoped resolver — the fix ─────────────────────────────────────
legs = os_.open_legs_in_group(GID)
by_sec = {l["sec_id"]: l for l in legs}
print("open_legs_in_group(GID) — the fix:")
check("returns exactly 4 legs (not 2)", len(legs) == 4)
check("all 4 contracts present",
      set(by_sec) == {"59111", "59082", "59092", "59093"})
check("every leg qty == 150 (short CE NOT corrupted to 120)",
      all(l["qty"] == 150 for l in legs))
check("short 57600-CE resolves as SELL 150 (the leg that went naked)",
      by_sec.get("59092", {}).get("entry") == "SELL" and by_sec["59092"]["qty"] == 150)
check("every leg keeps group_id == GID",
      all(l["group_id"] == GID for l in legs))
check("entry_price anchors on the ENTRY leg (301.05), not an exit price",
      abs(by_sec.get("59092", {}).get("entry_price", 0) - 301.05) < 1e-6)

# ── Global netting filtered by group_id, WITH the Part-2 guard OFF: the ORIGINAL
#    bug reproduces (the prior-day q30 cross-nets today's leg away). ────────────
def _global_group_ce(gid):
    _rng = os_.trades_for_range("2026-08-14", "2026-08-21")
    o = [p for p in _rng.get("open", []) if (p.get("group_id") or "") == gid]
    return next((p for p in o if str(p.get("sec_id")) == "59092"), None)

os_._GROUP_ISOLATION = False   # simulate the pre-2026-08-21 global netting
old_ce = _global_group_ce(GID)
print("Global-filter path with Part-2 guard OFF — reproduces the bug:")
check("guard OFF: global path DOES corrupt the short 57600-CE leg "
      "(missing, or qty != 150)",
      old_ce is None or old_ce.get("qty") != 150)

# ── Part 2: WITH the guard ON (default), the global path is ALSO clean now ────
os_._GROUP_ISOLATION = True
new_ce = _global_group_ce(GID)
print("Global-filter path with Part-2 guard ON — no longer corrupts:")
check("guard ON: global path keeps short 57600-CE as SELL 150",
      new_ce is not None and new_ce.get("entry") == "SELL"
      and new_ce.get("qty") == 150)

# ── empty / unknown group → [] (never raises) ────────────────────────────────
print("edge cases:")
check("empty group_id -> []", os_.open_legs_in_group("") == [])
check("unknown group_id -> []", os_.open_legs_in_group("NOPE_XYZ") == [])

# ── fully-closed group (within-group exits carry the gid) -> [] ───────────────
CGID = "CLOSED_GRP_TEST"
os_.record("SELL", 150, 200.0, source="strategy", strategy="s", mode="live",
           trad_sym="X-Aug2026-24000-CE", sec_id="700", segment="NSE_FNO",
           status="filled", group_id=CGID, ts="2026-08-21 09:00:00")
os_.record("BUY", 150, 120.0, source="strategy", strategy="s", mode="live",
           trad_sym="X-Aug2026-24000-CE", sec_id="700", segment="NSE_FNO",
           status="filled", group_id=CGID, ts="2026-08-21 15:10:00")
check("group closed within its own ledger -> [] (net 0)",
      os_.open_legs_in_group(CGID) == [])

print("\n%s (%d failure(s))" % ("ALL PASS" if fail == 0 else "FAILURES", fail))
sys.exit(1 if fail else 0)
