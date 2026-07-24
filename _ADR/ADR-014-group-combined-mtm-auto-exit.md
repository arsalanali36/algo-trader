# ADR-014 — Per-group combined-MTM auto-exit (payoff panel #02)

Status: ACCEPTED — built 2026-07-24, PAPER + LIVE capable (user decision). Live fire = forward-verify.
Date: 2026-07-24

## Context

The Orders 📊 Payoff panel already showed a **combined-premium chart** (net MTM of
all a group's legs over time). The user wanted to put a **combined SL + Target**
on that chart and have the WHOLE group square off the moment the combined MTM
hits either line — "manual straddle me aise karte hain, par SL/target dikhta
nahi kahin". So: (a) show the two draggable lines, (b) actually auto-exit on hit,
(c) generalize it beyond straddles to ANY hedge/pair group.

The auto-straddle basket-exit (`auto_straddle.check_exit` + `_close_straddle`)
already does exactly this shape for straddles — but it's PAPER-hard-locked and
straddle-specific. The question was how to generalize it without re-writing
order placement or weakening the existing exit safety.

## Decision

A thin rule layer over the EXISTING exit machinery — no new order-placement code.

- **`_ops/position_exit_rules.py`** — PURE store + decision (no broker/order
  import, mirrors `auto_straddle.py`/`price_triggers.py`). A rule per group
  (`g:<group_id>` or `i:<sorted ids>`): `{target_rs, sl_rs, mode}`. Disk-persisted
  (`data/position_exit_rules.json`), restart-safe, auto-clears when the group is
  flat. `check_exit(combined_mtm, target_rs, sl_rs)` returns `target`/`sl`/`None`
  (None on bad data → freeze).
- **`/api/position-exit-rule` POST/DELETE** — arm/clear. `_exit_rule_identity()`
  makes `ids=` and `group_id=` derive the SAME key.
- **`_run_position_exit_rules()`** — runs inside `auto_straddle_loop`
  (monitor_daemon, ~3s, shares the ltp-poller-warmed cache). Each cycle:
  re-resolve the group's OPEN legs FRESH from order_store; compute combined MTM =
  Σ per-leg signed MTM (`(entry−ltp)` for SELL, `(ltp−entry)` for BUY, × qty) —
  exactly what the combined chart draws; **FREEZE (never fire) if any leg LTP is
  missing/stale** (TRAP #1 shape); on target/SL, square off every leg via
  **`execution_gateway.execute_exit`** (its own fresh flat-check, recorded under
  each leg's OWN strategy/source/mode); clear the rule.

**Fires in the group's OWN mode (user decision): paper group → paper exit, LIVE
group → REAL square-off order.** A live leg means a real leg to protect, and
closing a position is the safe direction. The panel disables Apply for CLOSED
groups (nothing to exit).

## Consequence (trade-offs accepted)

- **Reuses the battle-tested exit path** (execute_exit + its flat-check + group
  awareness) — the only new code is "compute combined MTM + decide when". This
  is deliberately the same reuse discipline as the straddle basket-exit (Rule 6B).
- **Can place REAL orders on a live group.** Mitigated: freeze-on-bad-data,
  fresh per-cycle leg re-resolve, execute_exit's own flat-check, error→skip, and
  it only ever CLOSES (never opens). A stale/gone group auto-clears its rule.
- **Not backtested** — it's a discretionary risk-management overlay the user
  arms per position, not a strategy signal (Rule 10 is about strategy entry/exit
  changing a validated number; this is user-armed protection, like a manual SL).
- **Single-process monitor** (monitor_daemon) → no cross-process double-fire.
  If pos_monitor's own SL/EOD closes a leg first, the next cycle sees the group
  flat and clears — no conflict.

## Pre-mortem (shapes checked)

- #1 stale-state → fresh order_store re-resolve each cycle + execute_exit flat-check. ✓
- #1 ₹0/bad-price → FREEZE on any missing leg LTP, never fire on incomplete data. ✓
- #2 built≠wired → wired into auto_straddle_loop; verified offline (fire-path 6/6). ✓
- #3 RAM-only → rules on disk, restart-safe, auto-clear on flat. ✓
- #4 duplicate logic → reuses execute_exit; combined-MTM formula = the chart's. ✓
- #5 fail-open → error → skip (never fire); freeze on bad data. ✓
- #6 shared resource → LTP via shared_ltp_cache + ltp_poller (batched), no per-leg REST. ✓
- #9 exit reason tagged → GROUP_TARGET/GROUP_SL prefixes + badges (Rule 9). ✓

## Verified

Pure module 8/8; fire-path 6/6 (target fire, SL fire, in-band hold, bad-data
freeze, group-flat auto-clear, live-mode→real exit path); endpoint key
derivation consistent. **Live PAPER fire = final forward-verify.**

Related: ADR-012 (auto-straddle basket-exit, the precedent), LESSONS TRAP #1
(₹0/freeze), Rule 6B (reuse exit path), memory `project_code3b_payoff_panel`.
