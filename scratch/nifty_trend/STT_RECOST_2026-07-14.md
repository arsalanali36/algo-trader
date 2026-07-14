# STT re-cost — date-aware Budget-2026 charges applied to all 14 hub runs

**Run:** 2026-07-14 (master-prompt-cost-calendar-refresh Part A). **Tool:** `stt_recost.py`
(read-only — extends `dom_recost.py`; re-costs each run's *recorded* per-trade premiums with
the new DATE-AWARE charges model in `charges.py`, layered on top of the DOM half-spread.
No pipeline re-run.)

## The verified regime table (now live in `charges.py`, all engines wired)

| rate (options) | pre 2024-10-01 | 2024-10-01 → 2026-03-31 | ≥ 2026-04-01 (Budget-2026) |
|---|---|---|---|
| STT (SELL side, on premium) | 0.0625% | 0.10% | **0.15%** |
| NSE txn (both legs, on premium) | 0.053% | 0.03503% | **0.03553%** |
| Futures STT (sell) | 0.0125% | 0.02% | **0.05%** |

Verified live against zerodha.com/charges (2026-07-14) + Zerodha z-connect Oct-2024 bulletin.
Brokerage flat ₹20/order, SEBI ₹10/crore, stamp 0.003% buy, GST 18% — unchanged across regimes.
**⚠️ The "₹40/order under SEBI's 50% cash-collateral rule" claim is NOT on Zerodha's charges
page — flat ₹20 is listed. Modeled ₹20. If a real contract note ever shows ₹40, change
`charges.BROKERAGE_PER_ORDER` (one place) — and check the account's cash-vs-pledge mix.**

The old code costed EVERY trade at the pre-Oct-2024 rates. Equivalence-tested: pre-Oct-2024
trades reproduce the old fee to the rupee (max-diff 0), so `rec` below == the recorded numbers.

## Columns
`rec` = recorded (old charges, zero slip) · `+dom` = + DOM real half-spread (≈ SLIP_RECOST's
"real") · `+stt` = + date-aware charges delta ONLY · `+both` = DOM + STT = **the new honest
number**. The two deltas are reported separately, as asked.

## bs-pass Sharpe — full / OOS (rec → +dom → +both) + ₹ attribution (full period)

| # | slug | full rec→dom→both | oos rec→dom→both | slip ₹ (full) | stt-delta ₹ (full) | gate cross from STT? |
|---|---|---|---|---|---|---|
| 00 | mid_orb_nifty | 2.366 → 2.319 → 2.319 | 2.509 → 2.451 → 2.449 | 8,624 | **123** | no |
| 01 | long_straddle_orb | 3.549 → 3.286 → 3.284 | 3.872 → 3.546 → 3.541 | 66,631 | **455** | no |
| 02 | debit_vertical_orb ⚠️ML | 1.671 → 1.593 → 1.593 | 1.005 → 0.926 → 0.925 | 18,856 | **107** | no (OOS was already <1 from DOM) |
| — | long_strangle_orb (sig-failed) ⚠️ML | 4.085 → 3.961 → 3.960 | 4.823 → 4.669 → 4.666 | 31,792 | **337** | no |
| 03 | orb_supertrend | 2.065 → 2.004 → 2.003 | 1.876 → 1.827 → 1.827 | 15,752 | **102** | no |
| 04 | chain_zone_longatm | 1.946 → 1.872 → 1.871 | 2.031 → 1.960 → 1.959 | 27,357 | **206** | no |
| 04-N | chain_zone_naked ⚠️ML (SELL) | −0.625 → −0.722 → −0.722 | −0.197 → −0.294 → −0.296 | 25,371 | **158** | already dead |
| 04-C | chain_zone_credit ⚠️ML (SELL) | −1.003 → −1.095 → −1.096 | −0.541 → −0.633 → −0.635 | 23,649 | **124** | already dead |
| 04-P | chain_zone_positional | 0.974 → 0.934 → 0.934 | 1.062 → 1.024 → 1.024 | 22,617 | **65** | no (full already <1) |
| 05 | ratio_backspread ⚠️ML | 1.555 → 1.515 → 1.515 | 1.959 → 1.918 → 1.917 | 8,328 | **69** | no |
| — | gamma_scalp (REJECTED) ⚠️ML | 0.124 → 0.004 → 0.004 | −10.31 → −10.53 → −10.54 | 42,830 | **146** | already rejected |
| 06 | shortvol_ironfly (REJECTED) ⚠️ML (SELL) | −3.509 → −3.708 → −3.710 | −4.142 → −4.386 → −4.391 | 33,990 | **188** | already rejected |
| 07 | banknifty_hunt ⚠️ML† | 1.459 → 1.368 → 1.367 | 1.440 → 1.225 → 1.216 | 15,806 | **363** | no |
| 08 | pivot_continuation | 0.967 → 0.941 → 0.941 | 1.965 → 1.918 → 1.917 | 6,851 | **62** | no (full already <1, borderline) |

⚠️ML = multi-leg (net premium recorded → BOTH deltas per-leg-understated, same caveat as
dom_recost). † single-leg by design, just not in the EXACT whitelist.

## Findings

1. **The STT refresh moves nothing that matters — zero gate crossings.** Every run's
   Sharpe changes at the 3rd decimal; the ₹ attribution shows why: DOM spread costs each run
   ₹6.8k–₹66.6k over the full window, the STT correction costs **₹62–₹455**. No strategy that
   survived DOM-spread drops below the 1.0 gate from the corrected charges (`stt_recost.py`
   gate-cross check: none).
2. **Why so small (3 stacked reasons):** (a) ~70-100% of each run's trades are pre-Oct-2024 →
   delta exactly ₹0 by construction; (b) the Oct-2024 regime is roughly **cost-neutral for
   option BUYERS** — STT +0.0375% on the sell-side premium is offset by the txn cut −0.018%
   on both legs (several train slices show tiny NEGATIVE deltas: −₹4/−₹16/−₹39); (c) only
   ~2 months of post-Apr-2026 trades exist in the data (10–55 per run) that pay the real
   Budget-2026 hike.
3. **ORB-family (00/01/03) disproportionate-impact check (Part A Task 4): NOT confirmed in
   the historical window** — high trade count × near-zero per-trade delta = noise. But
   **going FORWARD every trade is post-Apr-2026**: a 1-lot ATM BUY round-trip now costs
   ~₹4–5 more than the old model said (~+6% of fee), ~₹9–10 for a 2-leg straddle, and
   **materially more for SELL-entry structures** (STT hits the bigger entry-side premium —
   the 04-N/06 family, already dead anyway). High-frequency buyers: the DOM spread remains
   the dominant cost, ~10× the STT hike per trade.
4. **Forward defaults are now honest automatically:** `bs_option.calc_charges(when=None)` =
   TODAY's regime, every backtest engine passes the trade's entry timestamp, `run_hunt.py`
   inherits via `bs.reprice*` — the Task-5 hunt (and every future hunt) prices each
   historical trade at its own regime with zero extra wiring.

## Correction to SLIP_RECOST_2026-07-13.md

The 04-P `chain_zone_positional` OOS cell "1.062 → **0.985**" in that report transcribed the
**2× stress** column as "real" (dom_recost re-run today: zero 1.062 / real **1.024** / 2×
0.985; full/train cells were correct). So the 🔴 "OOS below 1.0" flag for 04-P was wrong —
under real DOM spread its OOS holds at 1.024 (full-period 0.934 stays <1, verdict "marginal
either way" unchanged). 02 dvert's 🔴 OOS flag (0.926) re-confirms exactly.
(Also note: 04-P's recorded bs|oos metric block says 1.105 while re-deriving from its own
all_trades gives 1.062 — a pre-existing recorded-metrics quirk in that run, flagged by
dom_recost's own MISMATCH check, unrelated to today's change.)

## What was changed in code (Part A)

- **NEW `charges.py`** — single source of truth, regime table + `option_charges()` /
  `futures_charges()` / `opt_rates_vec()`.
- `bs_option.calc_charges(..., when=)` delegates there; all 4 `reprice*` pass entry ts.
- Entry-ts wired at every engine call site: option_structures, real_struct, real_struct2,
  real_calendar, positional_vol, gamma_scalp, delta_neutral_fly, oi_signals,
  pivot_nextday_real, vps_pivot_real, ml_gp_precompute (`charges_vec(dates=)` vectorized).
- `templates/index.html` calcCharges → current regime (0.15% / 0.03553%) — the live
  dashboard costs TODAY's trades only. (Old rates were **under-reporting today's tax**.)
- NOT touched (deliberate): `engine.py` / `intraday_engine.py`'s rough `₹40 + 0.02%` spot
  fee — that's the pass-① instrument reference layer, not an options-charges model; the
  deployable pass-③ is what got fixed. `ml_rules_a.py`'s flat ~₹100/trade approximation —
  Approach-A mining is concluded (no edge found), noted only.
- `runs/<slug>/` recorded numbers NOT overwritten (same policy as SLIP_RECOST — deltas are
  sub-noise; a full `run_hunt.py` re-run would also re-optimize and is not justified by
  ₹62–₹455 total).
