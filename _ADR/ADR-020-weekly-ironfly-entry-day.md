# ADR-020 — Weekly Iron-Fly (02.17): "day-after-expiry" entry detection

**Date:** 2026-08-24
**Status:** Accepted
**Related:** `_ops/weekly_ironfly.py`, `_ops/weekly_ironfly_live.py`, [[project_code3b_weekly_ironfly]],
reuses ADR-012/016 patterns (auto-straddle/roller) + `strangle_live` infra.

## Context

New strategy 02.17 = a POSITIONAL weekly iron-fly whose entry rule is
**"the first trading day AFTER a weekly expiry, at 09:20"** (fresh weekly cycle = max
premium to sell). Unlike every other strategy in the repo, its entry is gated on a
*calendar event* (an expiry just passed), not on a price signal or a fixed clock time.

Two ways to detect "today is the day after a weekly expiry":

1. **Weekday/holiday math** — port `scratch/nifty_trend/expiry_calendar.py` logic onto the
   live path (compute the weekly-expiry weekday, holiday-shift it, check if the previous
   trading day was that date).
2. **Front-expiry roll** — read the front weekly expiry of the ATM contract from
   `dhan_master` each morning; the day the front expiry *rolls forward* is (by definition)
   the first day of a new cycle = the day after the previous expiry.

## Decision

**Use option 2 (front-expiry roll), persisted via a `last_expiry_seen` marker in the
strategy's own store.** `should_enter(front_today, marker, has_open)` is a pure function;
the live loop supplies `front_today` from `dhan_master.get_expiry_for_sec_id(ATM CE)`.

- First run ever (`marker is None`) → **bootstrap only** (adopt today's front, do NOT enter)
  so a fresh deploy can't fire a mid-week first entry. Trading begins from the next roll.
- `front != marker` → new cycle started → **ENTER**, advance marker.
- `front == marker` / already open / no front → no entry.

## Consequences

- **No research imports on the live order path.** `expiry_calendar.py` lives under
  `scratch/nifty_trend/` (research). Option 1 would have pulled research code into a live
  trader — exactly the coupling the repo avoids. `dhan_master` (already a live dependency)
  gives the *real listed* expiry, which is **holiday-proof by construction** — NSE's own
  holiday-shifts are already baked into the listed contract, so we never re-derive them.
- **Trade-off accepted:** on a fresh deploy the strategy waits until the next expiry roll
  before its first entry (bootstrap-skip). For a PAPER forward-validation strategy this is
  the safe default — no surprise mid-week entry. If an immediate first entry is ever wanted,
  seed `last_expiry_seen` in the store to the *previous* cycle's expiry by hand.
- **Marker is durable** (in `data/weekly_ironfly_positions.json`) so a restart mid-cycle
  does not re-fire (survives the TRAP #76 restart class).
- Backtest uses the simpler weekday-match (`_nearest_weekly`, 252 correct entries over 5y);
  live uses the roll-marker. Same *intent*, different mechanism — reconcile on paper
  (Rule 10) before real money, like every backtest-≠-live-impl strategy here.
