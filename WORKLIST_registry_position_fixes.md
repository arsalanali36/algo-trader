# Worklist — Registry / Result / Position / Chart fixes

**Branch:** `feat/registry-position-fixes` (worktree `CODE3B_WORKTREE_registry-fixes`, base `734be41`)
**Date:** 2026-07-13 · isolated worktree so `master` stays untouched.

> Note: `master` advanced by 3 unrelated commits during this work (gitignore / VPS
> git-deploy docs / vrp removal). **Zero file overlap** with this branch → merge is clean.

| # | Task | Status | Commit | Verified |
|---|------|--------|--------|----------|
| 57 | Registry: Ars Chain (paper) showed no Stop button | ✅ | `456058b` | browser (mock) — 04.01/04.02 now show `now` + Stop |
| 61 | Registry: header cleanup + ctrl-click expand/collapse all | ✅ | `456058b` | browser (mock) — order search→tags→group; OOOO/CCCC toggle |
| 60 | Result page: click a P&L-distribution bar → filter trades | ✅ | `8ca4ed8` | browser (real run) — 567→1, chip, unit-toggle clears |
| 64 | Charts: ORB-family SL/Target/OR overlays on backtest chart | ✅ | `134e4e0` | browser+data — dvert/straddle/backspread patched, mid_orb regression OK |
| 58 | Positions: strategy tag shows `NN.MM - Name` not just number | ✅ | `eb459a4` | node unit-test |
| 62 | Positions: right-click group → open strategy Lab result page | ✅ | `eb459a4` | node unit-test (slug resolve) |
| 65 | Positions: Group-by dropdown (+By Strategy) + Export dropdown | ✅ | `eb459a4` | node unit-test (mode/key/migration) |
| 63 | ORB+Supertrend got global aggressive RMS, not its own SL/target | ✅ | `0cc7d81` | py_compile + audit; **MONEY-PATH — paper-test on VPS before live** |
| 59 | Debit Vertical / ORBST "10:30 2 entries, 2nd looks off" | ✅ | `79c55b1` | node unit-test — leg badge; **netting NOT changed (no confirmed bug)** |

## Notes / follow-ups
- **#63 (money-path, NOT deployed):** the 6 mission ORB traders now pass `own_exit=True`
  to `execution_gateway.execute_signal`, which skips the global RMS default per-instrument
  SL profile (AGGR_TSL). RMS daily-loss cap + 3:15 EOD squareoff still apply (blanket).
  **Paper-test on VPS**: confirm ORB positions no longer get AGGR_TSL and each strategy's
  own exit + daily-cap + 3:15 still fire, before enabling live.
- **#59:** dvert's two rows = the two legs of one spread (different strikes, correct). Added
  a `🔗 leg N/M` chip. Did **not** touch order_store netting (money-path, no reproducible
  bug locally — orbst_v1/dvert_v1 rows are VPS-only). If a real per-row mismatch exists,
  need the actual VPS rows/screenshot to diagnose safely.
- **Task 10 (pre-existing, not mine):** `03_orbst_trader.py:170 _supertrend_dir` DUP-INDICATOR
  recorded in `_TOOLS/audit_baseline.json` as known debt (ratchet still blocks new violations).
  Real fix = move it into `_CHARTING/indicators.py`.
- `scratch/nifty_trend/rebuild_dashboards.py` (new): re-copies the shared dashboard template
  into every `runs/<slug>/index.html` after a template edit (results.js untouched).
