# ADR-019 — Group-close ops resolve legs from the group's OWN ledger, not by filtering global netting

**Date:** 2026-08-21
**Status:** Accepted (VPS-live `c3c2e00`) — Part 1 of 3 (Parts 2/3 pending)

## Context

A placement GROUP (an iron condor / hedged straddle / strangle placed together,
sharing a `group_id`) is closed atomically by three real-order paths:

1. `_run_position_exit_rules` — the ₹-basket combined-MTM auto SL/Target (ADR-014)
2. `/api/close-group` — the manual "Close all (N)" button
3. the hedge-sibling close — closing one leg cascades to its siblings

All three resolved "which legs are open in this group" by taking the GLOBAL
`order_store.trades_for_range(...).open` list and **filtering it by group_id**.

That global list comes from `_net_rows`, which FIFO-pairs opposite legs by
`(mode, trad_sym)` across a multi-day window. But an index/stock **monthly**
option keeps the same `sec_id`/`trad_sym` for weeks and is re-traded across days;
plus the ledger accumulates leftover `externally_closed` reconcile/manual legs on
those same contracts (kept in netting on purpose, TRAP #167b). So the global
netting cross-pairs a prior-day / different-group leg against TODAY's group leg —
**stripping the group_id from, and corrupting the qty of, some of today's legs.**

This bit LIVE (2026-08-21, TRAP #183): a 4-leg BANKNIFTY hedged straddle's ₹4k
basket-SL fired on a real −₹4,474 but resolved as only **2 legs** (a 2026-08-17
manual q30 leftover on the shared monthly 57600-CE cross-netted two legs away) →
it closed 2 and left the short 57600-CE **naked**.

## Decision

Resolve a group's legs from the **group's own ledger**, never from a filtered
global netting.

New `order_store.open_legs_in_group(group_id)`:
- reads only rows `WHERE group_id = gid`,
- nets signed qty per `sec_id` within the group (entries − exits carrying the gid),
- returns net-open legs shaped exactly like `_net_rows`' open entries (drop-in).

`group_id` IS the placement identity, so within-group netting is exact and immune
to any other day / strategy / manual leg on the same contract. All three
group-close paths use it. The global `_net_rows` / `trades_for_range` is left
**untouched** — it feeds ~30 consumers (including `risk_gate` capital/margin), and
rewriting that core to fix a group-scoped bug is exactly how a "root fix" becomes
a bigger bomb.

**Direction of error is deliberate:** the resolver OVER-includes, never
under-includes. A leg closed by a manual/reconcile order that did NOT carry the
gid is invisible to within-group netting, so it stays listed as open — but that
produces no wrong order, because `execution_gateway.execute_exit`'s fresh per-leg
flat-check skips a leg already flat at the broker. UNDER-listing (the old global-
filter bug) is the dangerous direction — it is what left a leg naked.

## Consequence

- Group closes (auto ₹-basket SL/Target, manual Close-all, hedge-sibling) now
  always act on the group's true full leg set → a hedged structure can never be
  left partially-closed / naked by these paths.
- A new hedged multi-leg strategy that places legs with a `group_id` is protected
  by construction — no per-strategy wiring.
- Trade-off accepted: the resolver can list a leg as "still open" that a manual
  broker close (without the gid) already flattened; the per-leg flat-check absorbs
  this safely (a skipped no-op close, never a wrong order).
- Global netting's own cross-pollution (a resolved `externally_closed` leg still
  FIFO-pairing with an unrelated future leg) is NOT fixed by this ADR — it still
  slightly corrupts global P&L/counts, just no longer causes naked legs. That is
  **Part 2** (seal it in `_net_rows` with a full before/after whole-DB test) +
  **Part 3** (purge the ~110 existing leftover legs). Deferred deliberately given
  the blast radius of the netting core.

Guard: `_DEV/tests/test_group_leg_resolution.py`. See TRAP #183,
memory `project_code3b_group_leg_resolution`.
