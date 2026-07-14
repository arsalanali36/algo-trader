---
description: Read today's EOD report + signal-replay, and if a MISS/EXTRA/GATED anomaly appears that isn't already a known pattern, draft a diagnosis + fix for review. Reporting only — never touches live-order-path code.
---

# eod-report-review

You are running the recurring end-of-day report review for the CODE3B algo-trader.
**Reporting + draft only.** This reads what `eod_report.py` / `signal_replay.py`
already produced (their own 15:45 IST timer runs first) and surfaces anything new.

## HARD EXCLUSION (never violate)
Same as architecture-audit-loop: you may NOT edit or auto-fix any `_core/` live-order-
path file (`execution_gateway.py`, `webhook_executor.py`, `smart_order.py`,
`risk_gate.py`, `strategy_safety.py`, `order_store.py`). Draft findings as text only.

## 0. Idle-aware pre-check
Same as architecture-audit-loop §0 — if there are uncommitted changes in the last
15 min or a `hunt_guard` lock is held, append `skipped — active session detected` to
`data/loop_activity.log` and STOP.

## 1. Load today's report
- Latest `data/reports/eod_<date>.html` (today's; if `eod_report.py`'s 15:45 timer
  hasn't run yet, run `python -X utf8 _ops/eod_report.py` first, then read it).
- Signal-replay verdicts: `python -X utf8 _ops/signal_replay.py` for today (run the
  SAME EVENING — Dhan intraday history is short). Verdicts: MATCH / GATED(reason) /
  🔴 MISS / 🔴 EXTRA.
Hard wall-clock cap: **45 min** — over that, record `failed (timeout)` and stop.

## 2. Flag only NEW anomalies
Known/expected patterns (do NOT re-flag): any `GATED(window|late|paused|expiry-day)`
(these are the strategy correctly not-trading), a strategy that was down for a known
reason, anything already noted in `LESSONS.md`. A genuine anomaly =
- 🔴 **MISS** (offline signal fired but live neither entered nor logged a skip),
- 🔴 **EXTRA** (live entry with no offline signal),
- an unexplained heartbeat gap / PAPER-vs-config mode mismatch (TRAP #57) /
  ₹0-fill (TRAP #1) that the digest surfaced and isn't already understood.

## 3. Draft a diagnosis + fix (NO commit, NO write to excluded files)
For each new anomaly: state the strategy, the verdict, the likely mechanism (cite the
trader's own signal code / logs — do not guess), and a **draft fix as a review diff**.
Do NOT apply or commit. Live-path file involved → words only.

## 4. max_consecutive_failures = 3
If the last 3 consecutive runs in `data/loop_activity.log` are `failed`, STOP and flag
the user instead of looping.

## 5. Log the run (always)
Append ONE line to `data/loop_activity.log`:
`<ISO timestamp> | eod-report-review | tokens=<approx or n/a> | runtime=<Ns> | <clean|anomalies-drafted|skipped|failed[: reason]>`

## FIRST RUN IS MANUAL
Before this is ever scheduled, the user runs it ONCE by hand on today's/yesterday's
report to confirm the output is sane and non-noisy. Only after that eyeball does it go
on a schedule.

## Frequency
`1d` — once ~16:00 IST (after `eod_report.py`'s own 15:45 timer has already run). Not
shorter.
