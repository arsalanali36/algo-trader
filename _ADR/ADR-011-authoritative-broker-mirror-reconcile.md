# ADR-011 — Authoritative broker-mirror reconciliation (retire the heuristic guessers)

Date: 2026-07-23
Status: Accepted (B2 cutover live)

## Context

Live position/fill reconciliation between `order_store` and the real broker was
INFERENCE-based and spread across three heuristic auto-scans in `pos_monitor_loop`:

- `broker_sync.sync_if_due` — DB-open/broker-flat "ghost" close, records an exit by
  fill-SIGNATURE / trade-id; has the TRAP #60 "fill already used → skip" branch.
- `broker_sync.untracked_scan_if_due` — broker-open/DB-absent, diffs CURRENT positions.
- `broker_sync.reconcile_if_due` (`reconcile_manual_trades`) — records manual fills by
  signature+count / order-id.

Every one of these GUESSES which order_store row a broker fill belongs to, and each
misfire got a new guard (TRAP #44/#58/#59/#60/#61/#67/#92/#93/#138/#139/#145 + the
2026-07-23 paper-ate-the-fill + split-65+65 whack-a-mole). The class never closed —
because inference on ambiguous data is fundamentally fragile, and two writers with
different keys (signature vs trade-id vs order-id) actively fight (partial-record →
residual phantom). Symptom for the user: 1–2 trades/day get "stuck" (app ≠ broker),
each needing manual clean-up; days wasted.

## Decision

ONE authoritative reconciler that MIRRORS the broker instead of guessing.

`_ops/reconcile_broker.py`:
- The broker's own trade book is the single source of truth for LIVE fills. Every
  order the app places already stores its broker `order_id` (`order_store.broker_order_id`);
  the broker gives every fill a unique `(order_id, trade_id)`.
- A broker order the app has a row for = KNOWN. A broker order the app has NO row for =
  EXTERNAL (manual entry/close) → record it EXACTLY ONCE, aggregated per order_id → one
  matched-qty row (netting-safe — the whack-a-mole came from a single SELL 130 unable to
  pair split 65+65 fills), attributed to the contract's single open live strategy (else
  `manual`). Keyed by `broker_order_id` → idempotent. LIVE only; PAPER never touched.
- `mirror_if_due()` runs every `pos_monitor` tick (~2.5 min cooldown, market window),
  fires a bell notify on each auto-reconcile ("app detected your Zerodha close, recorded")
  and FLAGS (no silent write) anything ambiguous — unresolved symbol, or a residual
  net mismatch that means an app-side phantom the broker has no record of.

The three heuristic auto-scans are DISABLED in `pos_monitor_loop`. The manual buttons
(`/api/sync-positions` = `force_sync`, `/api/reconcile-manual-trades`) stay for on-demand
use. `invariant_guard` (independent, read-only, every 120s) remains the watchdog that
alerts on ANY app-net ≠ broker-net.

## Consequence

- Trade-off accepted: we lose `sync_if_due`'s naked-hedge-leg alert (S5) and Dhan
  auto-adopt (irrelevant — algo trades on Kite). If naked-leg alerting is wanted it gets
  re-added to the authoritative path, not the retired heuristic.
- Attribution across MULTIPLE live strategies on the SAME contract is not auto-written —
  `_open_live_strategy` returns None when >1 strategy holds a contract → attributed to
  `manual` and net still matches; a truly ambiguous split is FLAGGED, not guessed.
- Proven before cutover: `apply()` reconstructed the correct clean state from the real
  pre-incident backup in ONE pass (arschain 169→162.3, +₹871, 0 residual) and was
  idempotent (2nd run: 0 actions). Verified read-only planner agrees with reality live.
- Safety net: `invariant_guard` catches any reconciler mistake within ~2 min. The write
  path only records confident external orders and reports the rest — it cannot silently
  do the wrong thing in an unproven scenario.

Related: LESSONS.md (2026-07-23 broker-mirror), ADR-009 (manual-exit robustness — the
heuristic era this supersedes).

## Addendum (2026-08-03, TRAP #170) — the last heuristic hole + the netting mismatch

The original decision left the manual `/api/sync-positions` button on the retired
`force_sync` heuristic ("stays for on-demand use", above). That was the last place the
guesser survived, and it bit exactly as the class always did: a strategy's own open BUY
was flat at the broker (its closing SELL had already been recorded separately by the
authoritative mirror as a `broker_reconcile` leg) → `force_sync` marked the BUY
`externally_closed` and, on the mirror-tagged SELL, `defer`-ed while still counting it as
"cleared" — leaving one real round-trip split into a permanent phantom short the button
falsely reported as fixed (BAJFINANCE 2026-08-03).

Two compounding causes, two fixes:

1. **Netting could not use the mirror's own records.** `order_store._net_rows` allowed a
   cross-strategy close ONLY for `source='manual'` (the TRAP #145 guard). But the mirror
   records an external close as `source='broker_reconcile'` — so it could never pair with
   the strategy leg it closed. The authoritative record existed but was inert. **Fix:**
   `_MANUAL_CLOSERS = {"manual", "broker_reconcile"}` — a mirror leg is broker truth and
   may cross-net exactly like a manual close. This does NOT reopen strategy-vs-strategy
   netting (a `broker_reconcile` leg is not a second strategy's position). Test:
   `_DEV/tests/test_broker_reconcile_netting.py`.

2. **The button still guessed.** **Fix:** `/api/sync-positions` now calls
   `reconcile_broker.apply(dry_run=False)` — the SAME authoritative mirror the auto-loop
   uses — not `force_sync`. It records confident external orders (idempotent by order_id)
   and reports residual mismatches honestly; it never marks a strategy leg
   `externally_closed` and never reports a "cleared" count for a no-op.

Revised position: the retired heuristics (`force_sync`/`_run_sync`, `untracked_scan`,
`reconcile_if_due`) are now off the AUTO path **and** the user-facing button. `force_sync`
survives only as dead code + the `is_flat`/`is_flat_fresh` cache readers (which do their
own fetch); it should not be re-wired to any UI. ONE reconciler, everywhere.

Related: LESSONS.md TRAP #170, memory `project_code3b_broker_reconcile_netting`.
