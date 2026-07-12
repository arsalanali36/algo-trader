# ADR-005 — Real bid/ask spread slippage in the BS backtest pass (from brother's DOM data)

**Date:** 2026-07-12 · **Status:** Accepted

## Context

The option-strategy backtests price fills with Black-Scholes at the option **MID** (fair value).
Two cost engines existed, and they disagreed on spread:

- `bs_option.reprice*()` (ALL directional ATM strategies — pivot #08, chain-zone, ORB, straddle,
  debit-vertical, backspread via `option_structures.backtest_structure`) applied real Zerodha
  `calc_charges` but **ZERO spread slippage** → optimistic (a real fill crosses the bid/ask).
- `real_struct2.backtest()` (vol-selling family, real option-lake) applied a **flat 0.5%/leg**
  slip knob → for ATM legs ~4x too harsh vs reality.

Brother's 20-level order-book (DOM) data (`C:\_SABHAI DATA - Copy`, 21 days Jun-Jul'26, ATM CE/PE/FUT,
~8 snaps/sec, READ-ONLY) let us MEASURE the real spread for the first time (`dom_spread.py` →
`dom_spread_calib.json`): NIFTY ATM option one-way half-spread ≈ **0.11-0.16%/leg** (median), deep-OTM
wings ≈ 0.24% (median; 1.2% mean, 2% p90), FUT ≈ 0.008%.

## Decision

A single shared cost helper in `bs_option.py` is the ONE source of truth for spread slippage,
used by every pricing engine (no duplication — CLAUDE.md Rule 6B):

- `bs_option.slip_frac_for(premium)` → per-leg one-way half-spread FRACTION, DOM-calibrated by the
  option's premium band (`dom_cost.py` loads `dom_spread_calib.json`; 0.15% conservative fallback if
  the calib file is absent — never crashes).
- `bs_option.slip_cost_leg(ep, xp, qty)` → rupee cost for one leg's round trip = `h*(|ep|+|xp|)*qty`
  (same algebra as buying at ask / selling at bid off the BS mid).
- Global knobs: `SLIP_ENABLED` (default **True**) and `SLIP_MULT` (default 1.0; >1 = regime stress).

Wired into: `bs_option.reprice / reprice_positional / reprice_naked / reprice_spread` (per leg) and
`option_structures.backtest_structure.close_pos` (per leg, `qty*|side|` for ratio legs). `real_struct2`
gained a `slip_mode` param ("dom" default = the shared helper; "flat" = legacy 0.5% to reproduce the
old retracted vol numbers).

Each trade dict now carries a `slip` field alongside `fee` for transparency.

## Consequence

- **Every FUTURE hunt bakes in real spread by default** — the Sharpe≥1 gate now applies to honest,
  spread-inclusive numbers. This is the intended "secure for the future" behaviour.
- **Existing `runs/<slug>/` folders still hold their old ZERO-slip numbers** until re-run. So a run
  recorded before 2026-07-12 will NOT match a fresh re-run of the same strategy (the re-run is lower/
  honest). `dom_recost.py` gives the honest number for any existing run without re-running the pipeline.
  To refresh a run's official numbers, re-run its `build_*.py` (slip now applied automatically).
- Set `bs_option.SLIP_ENABLED = False` to reproduce any pre-2026-07-12 recorded number exactly
  (verified: pivot #08 bs|full = 0.967 with slip off, 0.941 with slip on — matches `dom_recost`).
- The DOM calib is RECENT-regime (2026); for pre-2022 years it is optimistic → use `SLIP_MULT` (2x)
  for a robustness stress. Deployed ATM-buy strategies survive even 2x (haircut only ~2-8% of Sharpe).
- **Verdicts unchanged for the deployed fleet** (all survive); the two borderline strategies (#08
  pivot, chain-zone positional) are confirmed marginal, not cost-artifacts.

Tooling: `dom_spread.py` (measure), `dom_cost.py` (load), `dom_recost.py` (re-cost recorded runs).
Full numbers in `scratch/nifty_trend/OPTION_STRATEGY_MISSION.md` RESUME-HERE.
