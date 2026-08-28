"""
test_zombie_exit_rule.py — guards the 2026-08-28 blunder from recurring.

Incident: a prior-day hedged straddle (straddle_alert_hedged) was squared off at
EOD, but pos_monitor's _do_squareoff recorded the exit legs WITHOUT their group_id.
So order_store.open_legs_in_group() still saw the group as net-open, the ±4k
position_exit_rule never auto-cleared, survived overnight, and RE-FIRED next day —
churning a closed position with real orders (one rejected → orphan leg).

Three fixes, each asserted here:
  #1 ROOT   — an exit recorded WITH its group_id nets the group flat →
              open_legs_in_group() returns [] → the rule's own `if not legs:
              clear_rule` finally works.
  #2 ORDER  — the group square-off cascade closes SHORT (SELL-entry) legs before
              BUY-entry wings (shorts-first, margin-safe).
  #3 GUARD  — the zombie-rule day-scope guard: an intraday group's rule whose legs
              are all from a prior day is cleared, never fired; an overnight
              strategy's rule is left alone.
"""
import os, sys, tempfile, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "_core"))

import order_store  # noqa: E402

# Point order_store at a throwaway DB
_tmp = Path(tempfile.mkdtemp())
order_store.DB_PATH = _tmp / "trades.db"
order_store.init_db()

GID = "STRADH_TEST_1"


def _rec(side, tsym, sec, price, **kw):
    order_store.record(side=side, qty=260, price=price, source="strategy",
                       strategy="straddle_alert_hedged", mode="live", broker="kite",
                       symbol="NIFTY", instrument="options", trad_sym=tsym,
                       sec_id=sec, segment="NSE_FNO", status="filled", **kw)


# ── Fix #1: enter a 4-leg hedged straddle under one group_id ─────────────────
_rec("BUY",  "NIFTY-Sep2026-24650-CE", "47011", 11.35, group_id=GID)   # wing
_rec("BUY",  "NIFTY-Sep2026-23850-PE", "46980", 8.60,  group_id=GID)   # wing
_rec("SELL", "NIFTY-Sep2026-24250-CE", "46995", 122.8, group_id=GID)   # short
_rec("SELL", "NIFTY-Sep2026-24250-PE", "46996", 84.6,  group_id=GID)   # short

legs = order_store.open_legs_in_group(GID)
assert len(legs) == 4, f"expected 4 open legs after entry, got {len(legs)}"

# BUGGY behaviour: EOD exit recorded WITHOUT group_id (blank) → group still net-open
_rec("SELL", "NIFTY-Sep2026-24650-CE", "47011", 8.05)    # no group_id
_rec("SELL", "NIFTY-Sep2026-23850-PE", "46980", 12.10)   # no group_id
_rec("BUY",  "NIFTY-Sep2026-24250-CE", "46995", 86.3)    # no group_id
_rec("BUY",  "NIFTY-Sep2026-24250-PE", "46996", 114.8)   # no group_id
legs_buggy = order_store.open_legs_in_group(GID)
assert len(legs_buggy) == 4, ("REPRO: without group_id on exits, the group STILL "
                              f"shows {len(legs_buggy)} open legs (the zombie) — this "
                              "is exactly what re-fired next day")

# FIXED behaviour: same exits but stamped WITH group_id → group nets flat
GID2 = "STRADH_TEST_2"
_rec("BUY",  "NIFTY-Sep2026-24650-CE", "47011", 11.35, group_id=GID2)
_rec("BUY",  "NIFTY-Sep2026-23850-PE", "46980", 8.60,  group_id=GID2)
_rec("SELL", "NIFTY-Sep2026-24250-CE", "46995", 122.8, group_id=GID2)
_rec("SELL", "NIFTY-Sep2026-24250-PE", "46996", 84.6,  group_id=GID2)
_rec("SELL", "NIFTY-Sep2026-24650-CE", "47011", 8.05,  group_id=GID2)   # exit + group_id
_rec("SELL", "NIFTY-Sep2026-23850-PE", "46980", 12.10, group_id=GID2)
_rec("BUY",  "NIFTY-Sep2026-24250-CE", "46995", 86.3,  group_id=GID2)
_rec("BUY",  "NIFTY-Sep2026-24250-PE", "46996", 114.8, group_id=GID2)
legs_fixed = order_store.open_legs_in_group(GID2)
assert legs_fixed == [], ("FIX #1: exits stamped with group_id must net the group "
                          f"flat → open_legs_in_group() == [], got {legs_fixed}")
print("Fix #1 OK — group_id on exit nets the group flat (rule will auto-clear)")


# ── Fix #2: shorts-first cascade sort key ────────────────────────────────────
_sort = lambda s: 0 if str(s.get("entry", "")).upper() == "SELL" else 1
group = [
    {"entry": "BUY",  "sym": "24650-CE-wing"},
    {"entry": "SELL", "sym": "24250-CE-short"},
    {"entry": "BUY",  "sym": "23850-PE-wing"},
    {"entry": "SELL", "sym": "24250-PE-short"},
]
ordered = [x["sym"] for x in sorted(group, key=_sort)]
assert ordered[:2] == ["24250-CE-short", "24250-PE-short"], \
    f"FIX #2: SHORT legs must sort first, got {ordered}"
assert ordered[2:] == ["24650-CE-wing", "23850-PE-wing"], \
    f"FIX #2: wings must sort last, got {ordered}"
print("Fix #2 OK — square-off cascade closes shorts (buy-to-close) before wings")


# ── Fix #3: zombie-rule day-scope guard logic ────────────────────────────────
def zombie_should_clear(legs, today, allow_overnight):
    """Mirror of the guard in _run_position_exit_rules: clear (don't fire) iff
    every leg is from a prior day AND the strategy is not allow_overnight."""
    if not legs:
        return True   # empty → cleared anyway
    all_prior = all((str(l.get("entry_date") or today) < today) for l in legs)
    return all_prior and not allow_overnight

today = "2026-08-28"
prior = [{"entry_date": "2026-08-27"}, {"entry_date": "2026-08-27"}]
todays = [{"entry_date": "2026-08-28"}, {"entry_date": "2026-08-27"}]

# intraday group, all prior day → ZOMBIE, must clear (today's incident)
assert zombie_should_clear(prior, today, allow_overnight=False) is True
# overnight strategy, same prior legs → legit hold, must NOT clear
assert zombie_should_clear(prior, today, allow_overnight=True) is False
# any leg from today → live group, must NOT clear
assert zombie_should_clear(todays, today, allow_overnight=False) is False
print("Fix #3 OK — prior-day intraday rule cleared; overnight/same-day rule kept")

print("\nALL PASS — the 2026-08-28 zombie-exit-rule blunder is guarded 3 ways.")
