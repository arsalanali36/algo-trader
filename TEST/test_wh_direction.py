"""Offline regression test for the 2026-07-16 webhook incident.

TV took 2 trades on NIFTY; we took 1. Two compounding causes:
  A) _recover_wh_state derived `direction` from the OPTION order side. arschain_MAIN
     sells PE for LONG and CE for SHORT, so opt_action is ALWAYS "SELL" -> every
     recovered position came back "SHORT", regardless of the real direction.
  B) _wh_state is per-process; release_position() in algo-monitor never cleared
     algo-dashboard's copy (TRAP #62), so a ghost survived the 10:27 SL exit.

Together: the 11:15 SHORT entry hit "already SHORT (pyramiding off)" and was dropped.
"""
import os
import sys

BASE = r"D:\KHAZANA\KHAZANA\PYTHON\CODE3B- TV BACKTEST ENGINE"
os.chdir(BASE)
for p in ("_data", "_core", "_ops", "."):
    sys.path.insert(0, os.path.join(BASE, p))

import dhan_master

SEC_ID = "57347"          # NIFTY-Jul2026-24150-PE — today's real live contract
fails = []


def check(name, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + name)
    print(f"          got={got!r} want={want!r}")
    if not ok:
        fails.append(name)


print("=" * 68)
print("1. get_option_type_by_sec_id — structured field, no string slicing")
print("=" * 68)
check("sec_id 57347 -> PE", dhan_master.get_option_type_by_sec_id(SEC_ID), "PE")
check("memoized 2nd call", dhan_master.get_option_type_by_sec_id(SEC_ID), "PE")
check("unknown sec_id -> None (never guess)",
      dhan_master.get_option_type_by_sec_id("999999999"), None)
check("None sec_id -> None", dhan_master.get_option_type_by_sec_id(None), None)


print()
print("=" * 68)
print("2. direction derivation — arschain_MAIN's real config")
print("=" * 68)
# nifty_config.json["webhooks"]["arschain_MAIN"] as deployed on the VPS
CFG = {"long_opt_type": "PE", "short_opt_type": "CE", "opt_action": "SELL"}


def derive(sec_id, cfg):
    """Mirrors the new _recover_wh_state block. None = undecidable -> skip."""
    ot = dhan_master.get_option_type_by_sec_id(sec_id)
    long_ot = (cfg.get("long_opt_type") or "").strip().upper()
    short_ot = (cfg.get("short_opt_type") or "").strip().upper()
    if ot and long_ot and ot == long_ot:
        return "LONG"
    if ot and short_ot and ot == short_ot:
        return "SHORT"
    return None


def old_derive(opt_action):
    """The buggy version being replaced."""
    return "SHORT" if opt_action == "SELL" else "LONG"


print("  today's leg: SELL NIFTY-Jul2026-24150-PE  (index LONG, per the entry log)")
print(f"  OLD code said: {old_derive('SELL')!r}   <-- inverted, this ate the 11:15 trade")
check("PE + long_opt_type=PE -> LONG", derive(SEC_ID, CFG), "LONG")

# the CE side of the same strategy = index SHORT
CE_SEC = dhan_master.get_option_contract("NIFTY", 24150, "CE", 0)
if CE_SEC and CE_SEC[0]:
    print(f"  CE contract for the mirror case: {CE_SEC[1]} (sec_id {CE_SEC[0]})")
    check("CE + short_opt_type=CE -> SHORT", derive(CE_SEC[0], CFG), "SHORT")
else:
    print("  SKIP CE mirror case — no CE contract resolved")

print()
print("  buy-side config style (long=CE / opt_action=BUY) must work too:")
BUY_CFG = {"long_opt_type": "CE", "short_opt_type": "PE", "opt_action": "BUY"}
check("PE + long=CE/short=PE -> SHORT", derive(SEC_ID, BUY_CFG), "SHORT")

print()
print("  undecidable cases must return None (skip, never guess):")
check("unknown sec_id -> None", derive("999999999", CFG), None)
check("cfg missing opt types -> None", derive(SEC_ID, {}), None)


print()
print("=" * 68)
print("3. _still_open_in_store — the cross-process ghost guard (TRAP #62)")
print("=" * 68)
import webhook_executor as wh


class FakeStore:
    def __init__(self, open_rows):
        self._rows = open_rows

    def trades_for(self, date):
        return {"open": self._rows}


def with_store(rows, st):
    real = sys.modules.get("order_store")
    sys.modules["order_store"] = FakeStore(rows)
    try:
        return wh._still_open_in_store(st)
    finally:
        if real is not None:
            sys.modules["order_store"] = real
        else:
            del sys.modules["order_store"]


ST = {"opt_sec_id": SEC_ID, "direction": "LONG"}

check("order_store flat -> False (ghost cleared, entry proceeds)",
      with_store([], ST), False)
check("position genuinely open -> True (real skip preserved)",
      with_store([{"sec_id": SEC_ID, "source": "webhook", "status": "filled", "tags": []}], ST),
      True)
check("CAPITAL_BLOCKED row is not a real position -> False",
      with_store([{"sec_id": SEC_ID, "source": "webhook", "status": "blocked",
                   "tags": ["CAPITAL_BLOCKED"]}], ST), False)
check("another strategy's leg on same sec_id -> False (not ours)",
      with_store([{"sec_id": SEC_ID, "source": "range", "status": "filled", "tags": []}], ST),
      False)


class BoomStore:
    def trades_for(self, date):
        raise RuntimeError("db locked")


check("store raises -> True (fail-safe: never stack a duplicate leg)",
      with_store.__wrapped__(ST) if hasattr(with_store, "__wrapped__") else
      (lambda: (sys.modules.__setitem__("order_store", BoomStore()),
                wh._still_open_in_store(ST))[1])(),
      True)


print()
print("=" * 68)
print("4. today's timeline replayed end-to-end")
print("=" * 68)
print("  10:01  ENTRY LONG  -> SELL PE @140      state.direction = LONG   (correct)")
print("  ~10:15 dashboard restart -> _recover_wh_state()")
print(f"           OLD: direction = {old_derive('SELL')!r}  <-- silently inverted")
print(f"           NEW: direction = {derive(SEC_ID, CFG)!r}   <-- correct")
print("  10:27  DEFAULT_TSL_SL fires in algo-monitor -> release_position()")
print("           monitor's _wh_state cleared; dashboard's copy = GHOST (TRAP #62)")
print("  11:15  ENTRY SHORT arrives")
print(f"           OLD: ghost.direction={old_derive('SELL')!r} == 'SHORT' -> \"already SHORT\" SKIP")
print(f"           NEW: _still_open_in_store -> {with_store([], ST)} -> ghost cleared -> ENTRY PROCEEDS")

print()
print("=" * 68)
if fails:
    print(f"RESULT: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("RESULT: all checks passed")
