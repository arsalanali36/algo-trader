# ADR-018 — Leg-collision main-gate: two strategies never share an option contract

**Date:** 2026-08-18
**Status:** Accepted (VPS-live)

## Context

At the broker, option positions are fungible by CONTRACT, not by strategy. Two
independently-running strategies can resolve legs to the same option contract
(e.g. two hedged BANKNIFTY strategies whose ATM/OTM strikes coincide on a given
spot). When they do, the broker nets their orders against each other:

- A BUY (one strategy's protective wing) on top of another strategy's SHORT
  silently CLOSES that short at the broker → the first strategy's defined-risk
  structure becomes a naked long wing. `order_store` still shows it open (per-
  strategy tracking is correct), but the real broker position is gone.
- Same-side sharing (SELL on an existing SELL) nets to a combined lot, so both
  strategies' exits, P&L attribution and combined-MTM ₹-basket monitoring key off
  one fungible position.

This happened live (2026-08-18, TRAP #177): `bnf_strangle_hedged` and
`straddle_alert_hedged` both held `BANKNIFTY-56900-PE` (one SELL, one BUY), net 0
at Kite — one strategy's hedge broke. The per-strategy flat-check (`_my_open_qty`)
protects exit accounting but does nothing about the ENTRY collision.

## Decision

A two-layer guard, both reading only `order_store` (fail-open on error):

1. **Universal MAIN GATE** in `execution_gateway.execute_signal` — the single
   choke-point every strategy's entry passes through (same place as the trading-
   day guard). For `mode=="live" and source=="strategy"`, if the leg's sec_id is
   already open for ANOTHER live strategy, the entry is REFUSED (`blocked`,
   reason `leg_collision`). Every current and future strategy is protected without
   per-strategy wiring.

2. **Smart pre-shift** (`_core/leg_collision.clear_leg`, `strategy_safety.
   compute_hedge_target(avoid=)`, `strategy_safety.wing_by_delta(avoid=)`) in the
   hedged fire paths — on collision the strategy steps one strike further OTM and
   re-resolves so it still trades a neighbouring strike; aborts (no naked/shared)
   if no clear strike within `max_shift`. Wired into `bnf_strangle_hedged`,
   `straddle_alert_hedged`, and the arschain hedged vertical (04.03.01).

**Key scoping:**
- **LIVE-only** (`occupied_sec_ids(..., live_only=True)`): broker fungibility is a
  live-only phenomenon. A paper leg never reaches the broker, so a paper twin must
  never false-block its live sibling (both trade the same strikes by design).
- **Strategy-sourced only:** a manual / trigger order is the user's own call and
  is NOT collision-gated.
- **Both sides refused:** same-side sharing is technically nettable but leaves the
  shared fungible lot's accounting ambiguous, so it is refused too.
- The gate's block decision is decoupled from its `log()` call — a logging failure
  (e.g. console encoding) must never silently disable a safety gate.

## Consequence

- Two strategies can no longer break each other's structure via the broker. The
  main gate is the backstop for any un-wired / future strategy; the pre-shift lets
  the three hedged strategies keep trading on a collision instead of aborting.
- **Rule 10 trade-off (accepted):** on a collision the strike shifts (rare) —
  a small deviation from the backtested strike, chosen as safety over fidelity.
  Paper never shifts, so backtested paper twins are untouched.
- Guard: `_DEV/tests/test_leg_collision.py`; `architecture_audit` 0 FAIL.
- Related: TRAP #177, TRAP #145 (per-strategy netting), ADR-011 (authoritative
  reconcile — which only ADDS broker fills, hence the phantom-leg accumulation
  cleaned up in the same session).
