---
description: Run a new strategy idea through the FULL research gate in one command — screen → optimize → significance → Monte-Carlo → data-integrity (Task 1) → 3-pass → BS-reprice. Research only; never deploys.
---

# hunt-pipeline

Run a strategy idea through the complete, non-negotiable research gate so no idea ever
gets "half-validated" ad-hoc. Takes a slug name as `$ARGUMENTS` (e.g.
`/hunt-pipeline my_new_strat`).

## HARD EXCLUSION (never violate)
This is research. It writes ONLY under `scratch/nifty_trend/runs/<slug>/` and the runs
index. It may NOT edit any `_core/` live-order-path file, may NOT deploy anything, and
may NOT flip any strategy `active`/`mode` in `nifty_config.json`. Deploy is always a
separate, manual, explicitly-approved step.

## 0. Idle-aware + parallel-safety pre-check
- If a human is mid-edit (uncommitted changes < 15 min) → append
  `skipped — active session detected` to `data/loop_activity.log` and STOP.
- Parallel hunts MUST go through `hunt_guard.py`'s lock registry (it's built into
  `run_hunt.main()`), so a second session can't clobber this build. If you're running
  two independent tracks at once, use a separate **git worktree** per track and let
  `hunt_guard` serialize the shared `runs/index.json` / `compare.json` writes. NEVER
  run two sessions against the same working dir unguarded, and NEVER `pkill python`
  broadly — only the pid `python scratch/nifty_trend/hunt.py status` lists as yours.

## 1. Run the full gate
`cd scratch/nifty_trend && python -X utf8 run_hunt.py --name $ARGUMENTS`
This already does, in order: screen → optimize (rank `min(train,oos)`, TRAP #103) →
significance (p<0.05) → Monte-Carlo → **data-integrity pre-check (Task 1: worst-day
TRAP #109 sanity)** → 3 passes × 3 periods → Black-Scholes reprice (real DOM slippage,
SLIP_ENABLED=True by default) → writes `runs/<slug>/` + appends `runs/index.json`.

## 2. Read the verdict honestly
- No design passed significance → "no edge to ship" is the correct, valuable outcome.
  Record it, do not keep adding complexity to force a pass.
- Passed significance BUT `meta.json.shippable == false` → the data-integrity gate
  failed (data not trustworthy, TRAP #109 shape). Do NOT treat it as shippable; report
  the `data_integrity` block and stop.
- Passed everything → report the `bs_full` Sharpe/net/DD + the data_integrity block.
  Shipping is still a SEPARATE manual decision by the user.

## 3. Log the run
Append ONE line to `data/loop_activity.log`:
`<ISO timestamp> | hunt-pipeline:$ARGUMENTS | tokens=<approx> | runtime=<Ns> | <shippable|no-edge|data-untrusted|failed>`

## Not for scheduling
Unlike the audit/eod loops, this runs on demand (a new idea), not on a clock.
