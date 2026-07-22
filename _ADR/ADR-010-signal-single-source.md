# ADR-010 — Signal is single-source: live and backtest run the SAME code

**Date:** 2026-07-22
**Status:** Accepted
**Related:** TRAP #130 (chain-zone never-matched), TRAP #153 (two-implementation divergence),
ADR-002 (indicator single source), Rule 6B (duplicate → extend), Rule 10 (backtest fidelity).

## Context (the blunder)

A strategy is deployed live **because its backtest number is trusted** (Sharpe / net% /
significance). That trust is only valid if the live trader fires the **same entry signal**
the backtest measured.

For months it did not. Each strategy had **two independent Python implementations of "the
same" signal**:

- **backtest:** `scratch/nifty_trend/intraday_engine.py` (`design_signals` — orb / tod_orb /
  orb_st / chain_zone) + `option_structures.py` / `build_overnight_orb.py` (which delegate to it).
- **live:** each `strategies/live/*_trader.py` had its OWN inline copy of the ORB / chain-zone
  math in `compute_signal` / `compute_breakout`.

They were **never proven signal-identical** (the 90.2% validation was Pine↔Python, a
different axis). They drifted, silently:

- `orb_v1` lived at **33% presence-match**; `dvert` had **opposite-sign P&L** (backtest −5k /
  live +1k). Smoking gun: 2026-07-13 `orb_v1` live log said `signal=none` all day while the
  backtest engine took a trade the same day.
- Root divergences that caused it: **OR boundary** (live used `< or_end`, backtest `<= or_end`)
  and **crossover ATR reference** (live compared against a *previous-bar* level `or_high + k·atr_p`,
  backtest against the *current-bar* level). Both tiny; both enough to flip individual signals.

A backtest that isn't the code you run live is not a backtest — it's a hope.

## Decision

**The entry signal has exactly ONE implementation. It lives in `strategies/signals/*.py`.
Both the backtest engine and the live trader import and call it.**

- `strategies/signals/orb.py` — `orb_signals` (vectorised, windowed + no-window + trend-filter)
  + `orb_st_signals` + point-in-time `orb_signal_last` / `orb_st_signal_last` for live.
- `strategies/signals/chain_zone.py` — `chain_zone_signals` (zone state machine + candle
  patterns + daily chain-levels) + point-in-time `chain_zone_signal_last`.
- Backtest: `intraday_engine.design_signals` / `_chain_zone_signals` **delegate** to these.
- Live: each `*_trader.py` calls `<signal>_signal_last(df, params, ...)`; only the ATR
  stop / order sizing stays in the trader (that's exit/execution, not signal).

**Every new signal follows this shape from day one — no inline copy, ever.**

## Consequences

- **Guaranteed match by construction.** Live can only differ from backtest via legitimate
  execution effects (RMS gating, real-time-vs-bar-close timing, fills) — never signal drift.
- **A guard test per signal family** locks it: `_DEV/tests/test_orb_single_source.py`,
  `test_chainzone_single_source.py` prove backtest == shared (bit-identical, 150k+ bars) AND
  live point-in-time == backtest (fired bars). Drift → test fails.
- **A commit-time guard** (`architecture_audit.py` check `INLINE-SIGNAL`) blocks any
  `strategies/live/*.py` that computes an ORB / chain-zone signal inline instead of importing
  the shared module. Escape hatch `# inline-signal-ok: <reason>` for a deliberate, documented
  not-yet-migrated file (only `07_banknifty_trader.py` today, baselined).
- **Trade-off accepted:** the vectorised shared fn is O(n) per point-in-time live call (the
  live trader re-runs it over its fetched window each loop). Cheap in practice (live windows are
  small); worth it for the zero-drift guarantee. If ever a hot path, memoise — do NOT fork a
  second copy.

## What this does NOT cover (honest boundary)

Only strategies whose signal is a **spot candle series** can literally share code. As of
2026-07-22, unified: orb / orbst / straddle / dvert / backspread / strangle / overnight /
chain-zone (8). Genuinely cannot via this pattern:

- **VRP family** (condor / weekly / straddle / short-vol iron-fly): live IV-rank comes from
  **live ATM premiums** (`vrp_signal`), backtest IV-rank from the **historical option-chain
  lake** (`optlake_load.iv_rank_daily`) — different data domains; align the formula + threshold,
  but not literally the same code on the same input.
- **BankNifty ORB:** signal *is* ORB (could share the module), but its backtest is on a separate
  BankNifty data store — migrate when that alignment is done (baselined debt until then).
- **RSI / EMA:** different design; RSI's backtest isn't in the deployed `runs/` shape.
- **ARS_CHAIN (range/webhook):** validated separately as Pine↔Python 90.2%.
