"""Strategy #3 — ORB breakout + Supertrend confirmation (directional, ATM option via BS).

The 2026-07-10 full hunt found tod_orb ranked first but that DUPLICATES the already-deployed
mid_orb_nifty (same design). The best genuinely-NEW significant design was orb_st
(ORB break confirmed by Supertrend direction): 15m robust=0.91, p=0.000, skip-expiry ON.

This driver reuses run_hunt.main() end-to-end (optimize -> 1000-perm significance -> 3-pass
dashboard -> runs/<slug>/) but pins the screen to the orb_st design only, so the winner is
orb_st or nothing. No duplicated write logic.
"""
import sys
import run_hunt as rh

_real_screen = rh.screen

def _only_orbst(cache, tfs):
    rows = [r for r in _real_screen(cache, tfs) if r["design"] == "orb_st"]
    print(f"\n[pin] restricted to orb_st: {len(rows)} TF candidates", flush=True)
    return rows

rh.screen = _only_orbst
sys.argv = ["run_hunt.py", "--tf", "15m,5m", "--trials", "300", "--name", "orb_supertrend"]
rh.main()
