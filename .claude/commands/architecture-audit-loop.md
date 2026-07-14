---
description: Run the architecture audit, and if a NEW fail/warn appeared vs the last run, draft a fix as a review diff (never auto-commit). Research/audit only — never touches live-order-path code.
---

# architecture-audit-loop

You are running the recurring architecture-audit loop for the CODE3B algo-trader.
This is **audit + draft only**. Follow every rail below — they exist because this
runs unattended.

## HARD EXCLUSION (never violate)
You may NOT edit, auto-fix, or even stage any of these live-order-path files:
`_core/execution_gateway.py`, `_core/webhook_executor.py`, `_core/smart_order.py`,
`_core/risk_gate.py`, `_core/strategy_safety.py`, `_core/order_store.py`, or anything
else under `_core/`. If the audit flags one of those, DRAFT the finding as text for
the user — do not write to the file. Loops never get near real order placement.

## 0. Idle-aware pre-check (skip if a human is working)
Before doing anything else:
- `git status --porcelain` — if any file was modified in the **last 15 minutes**
  (`git status` + `git diff --stat`, or file mtimes), OR
- a `hunt_guard.py` lock is held (`python scratch/nifty_trend/hunt.py status`, or a
  non-empty `scratch/nifty_trend/runs/_active_hunts.json`),
then **skip this run**: append `skipped — active session detected` to
`data/loop_activity.log` (see §5) and STOP. Do not queue or retry — wait for the next
scheduled slot.

## 1. Run the audit
`python -X utf8 _TOOLS/architecture_audit.py --report` (full repo). Capture the
FAIL/WARN lines and the `RESULT: N FAIL, M WARN` line. Hard wall-clock cap: **30 min**
— if it hasn't finished, kill it and record `failed (timeout)` in §5.

## 2. Diff vs the last run
`data/audit_history/` holds one file per prior run (`audit_<YYYY-MM-DD_HHMM>.txt`,
newest = last). Compare this run's FAIL/WARN set to the newest file:
- **No new fail/warn** → result = `clean`. Skip §3.
- **A fail/warn is present now that wasn't last time** → result = `fixes-drafted`.
  Proceed to §3 for the NEW ones only (don't re-draft known/baselined debt).

Then save this run: write the full audit output to
`data/audit_history/audit_<YYYY-MM-DD_HHMM>.txt` (create the dir if missing).

## 3. Draft a fix (NO commit, NO write to excluded files)
For each NEW finding, produce a **review diff** in your reply: the file, the
problematic lines, and the exact change you'd make (as a fenced diff or before/after),
plus one line on why. Do NOT apply it, do NOT `git add`, do NOT commit. The user
reviews and applies. If the finding is in an excluded live-path file (§HARD
EXCLUSION), describe it in words only.

## 4. max_consecutive_failures = 3
If this loop has recorded `failed` on its last **3** consecutive runs (check the tail
of `data/loop_activity.log`), STOP looping and tell the user it needs attention
instead of continuing.

## 5. Log the run (always, even on skip/fail)
Append ONE line to `data/loop_activity.log`:
`<ISO timestamp> | architecture-audit-loop | tokens=<approx or n/a> | runtime=<Ns> | <clean|fixes-drafted|skipped|failed[: reason]>`

## Frequency (how it's meant to be scheduled)
`2x-daily` — once pre-market ~09:00 IST and once post-market ~15:50 IST, i.e. outside
active trading/dev hours. Do NOT schedule shorter than this unless the user asks.
