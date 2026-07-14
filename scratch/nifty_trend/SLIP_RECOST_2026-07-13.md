# SLIP re-cost — real DOM spread applied to all pre-ADR-005 hub runs

**Run:** 2026-07-13 (worklist Task 6). **Tool:** `dom_recost.py` (read-only — re-costs each
run's *recorded* per-trade premiums with the DOM-measured half-spread, no pipeline re-run;
EXACT for single-leg ATM-buy, understated for multi-leg net-premium structures).
**DOM per-leg half-spread ≈ 0.133%** (by premium band, ADR-005). `zero` = old recorded
(zero-slip) number; `real` = DOM median; `2x` = regime stress.

All 14 runs in `runs/index.json` predated the ADR-005 commit (2026-07-12 15:56). 0 were post.
Vol-family (`shortvol_ironfly`) already separately re-tested (LESSONS #111) — included here for
completeness, verdict unchanged.

## Before/after — bs|full Sharpe (headline) + OOS

| # | slug (config) | full zero→real | train zero→real | oos zero→real | gate cross? |
|---|---|---|---|---|---|
| 00 | mid_orb_nifty (orb_v1) | 2.366 → **2.319** | 2.389 → 2.345 | 2.509 → 2.451 | no ✅ |
| 01 | long_straddle_orb (straddle_v1) | 3.549 → **3.286** | 3.415 → 3.177 | 3.872 → 3.546 | no ✅ |
| 02 | debit_vertical_orb (dvert_v1) ⚠️ML | 1.671 → **1.593** | 2.003 → 1.928 | 1.005 → **0.926** | 🔴 **OOS below 1.0** |
| — | long_strangle_orb (failed sig p=0.07) ⚠️ML | 4.085 → 3.961 | 3.749 → 3.636 | 4.823 → 4.669 | no (but sig-failed) |
| 03 | orb_supertrend (orbst_v1) | 2.065 → **2.004** | 2.107 → 2.045 | 1.876 → 1.827 | no ✅ |
| 04 | chain_zone_longatm (chainzone_v1) | 1.946 → **1.872** | 1.907 → 1.830 | 2.031 → 1.960 | no ✅ |
| 04-N | chain_zone_naked ⚠️ML | −0.625 → −0.722 | −0.930 → −1.034 | −0.197 → −0.294 | already dead |
| 04-C | chain_zone_credit ⚠️ML | −1.003 → −1.095 | −1.344 → −1.446 | −0.541 → −0.633 | already dead |
| 04-P | chain_zone_positional | 0.974 → **0.934** | 0.983 → 0.941 | 1.062 → **0.985** | 🔴 **OOS below 1.0** (full was already <1) |
| 05 | ratio_backspread (backspread_v1) ⚠️ML | 1.555 → **1.515** | 1.356 → 1.316 | 1.959 → 1.918 | no ✅ |
| — | gamma_scalp (REJECTED) ⚠️ML | 0.124 → 0.004 | −0.310 → −0.289 | — | already rejected (net −172%) |
| 06 | shortvol_ironfly (REJECTED) ⚠️ML | −3.509 → −3.708 | −3.147 → −3.321 | −4.142 → −4.386 | already rejected |
| 07 | banknifty_hunt (banknifty_v1) ⚠️ML† | 1.459 → **1.368** | 1.505 → 1.451 | 1.440 → 1.225 (2x: **1.007**) | no under real; 2x at gate |
| 08 | pivot_continuation | 0.967 → **0.941** | 0.700 → 0.680 | 1.965 → 1.918 | no new (full already <1, borderline) |

⚠️ML = multi-leg (net premium recorded → per-leg spread understated by dom_recost → **real cost
is a bit worse than shown**). † `banknifty_hunt` is design-wise single-leg ATM (Mid-Day ORB), just
not in dom_recost's `SINGLE_LEG_EXACT` whitelist — its `real` number is reliable-ish.

## Flags (Task 6c — Sharpe crossing the 1.0 gate from adding real slippage)

- 🔴 **debit_vertical_orb (02 / dvert_v1)** — OOS 1.005 → **0.926**. Full (1.59) and train (1.93)
  stay well above; only OOS dips under the gate. It's a LIVE **paper** strategy. Not a money risk
  (paper), but the OOS edge is now sub-gate under real spread → downgrade confidence, watch live.
- 🔴 **chain_zone_positional (04-P)** — OOS 1.062 → **0.985**. This is a *variant*, NOT the deployed
  `chainzone_v1` (= chain_zone_longatm, which stays 1.87). Full was already 0.97. Marginal either way.

## Live paper fauj — verdict under real slippage
00/01/03/04/05 all keep Sharpe comfortably >1 (full) after real spread → **cost-model artifact
ruled out, edge is real**. 02 (dvert) is the one softening (OOS). 07 (banknifty) holds under real,
marginal only at 2× stress. Dead/rejected (naked/credit/gamma/ironfly) just get deeper — unchanged.

## 6e — SLIP_ENABLED can't silently revert
`SLIP_ENABLED` is only ever assigned `True` (`bs_option.py:67` default; `real_struct2.py:176`
explicit). No code path sets it `False`. `run_hunt.py` never touches it → every future hunt
inherits real DOM slippage by default. The only `flat` references are `real_struct2.py`'s own
*labeled* stress scenario, not a silent override. ✅

## Hub overwrite (6d)
dom_recost is read-only — `runs/<slug>/` still show the old zero-slip numbers. Deltas are small
(≤0.26 Sharpe; no live strategy's full-period Sharpe crosses the gate). Decision on whether to
overwrite the hub via full `run_hunt.py` re-runs (which also re-optimize under slippage) is
pending user go-ahead — **before/after captured here first, per 6d.**
