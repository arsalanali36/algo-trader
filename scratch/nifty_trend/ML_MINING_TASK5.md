# Task 5 — Fib-retracement + Premium-divergence + Pre-open-gap seed (addendum to ml-strategy-mining-tasklist.md)

**Context:** Task 0-4 already complete (see CLAUDE.md 2026-07-14 entry + ARCHITECTURE_LOG). GP infra
(`ml_gate.py`, `ml_gp.py`/`ml_gp_precompute.py`, evolution/validation/lockbox split, DSR gate,
`ml_mining_log.csv` trials log) already exists and is proven — reuse it, don't rebuild it. Approach
A/B on the generic 39-signal space is DONE and on the DO-NOT-REDO list. This task is a NEW,
narrower, user-seeded hypothesis-family — different enough from what was mined that it's not
covered by the do-not-redo list, but small enough that it needs a hybrid grid+GP approach rather
than pure 300-random-rule GP.

---

## PROMPT FOR CLAUDE CODE

This is a user-seeded hypothesis, not open-ended random mining. Reuse ml_gate.py's evolution/
validation/lockbox windows and DSR/trials-log machinery as-is — do not redefine them. Reuse
ml_features.py v2's existing columns (gap_open_pct, ce_close, pe_close, ce_oi, pe_oi, atm_iv,
straddle_norm etc.) wherever they already cover what's needed; only add genuinely new columns.

═══════════════════════════════════════════════════════════
TASK 5a — NEW FEATURES (extend ml_features.py to v3)
═══════════════════════════════════════════════════════════
Add these columns (confirm against real data availability first, same discipline as Task 0a):

1. FIRST-CANDLE FIB LEVELS: from the day's first 1-min candle (09:15-09:16) high/low range,
   compute Fibonacci retracement levels: 23.6%, 38.2%, 50%, 61.8%, 78.6% (both from-high and
   from-low, i.e. "opposite side" versions). Store as absolute spot-price levels per day.
2. CONFIRM-CANDLE STATE: for the 2nd, 3rd, and 5th 1-min candles of the day, a column indicating
   which (if any) Fib level that candle's CLOSE crossed, and the close direction relative to the
   first candle (continuation vs reversal of the first candle's own direction).
3. PREMIUM-INDEPENDENT-BEHAVIOR: per-bar classification of how ATM CE and PE premiums are moving
   relative to each other and to spot, over the same 2nd/3rd/5th-candle window:
   * index_up_ce_up / index_up_ce_down (divergence)
   * index_down_pe_up / index_down_pe_down (divergence)
   * ce_pe_co_expanding (both premiums up together — IV-driven, direction-independent)
   * ce_pe_co_contracting (both premiums down together)
   Use existing ce_close/pe_close columns, just add the relative-move classification.
4. OI-BUILDUP ZONE (if not already covered by existing oi_imbalance/d_atm_oi_30m columns):
   long-buildup / short-buildup / short-covering / long-unwinding classification at the level
   nearest to each Fib level from #1, using price+OI combination (standard 4-quadrant logic).
5. PRE-OPEN GAP: gap_open_pct already exists in v2 — confirm what it's actually computed from
   (previous close vs 9:15 first-tick, or previous close vs NSE's 9:08-9:12 indicative
   equilibrium price from the pre-open call auction — these are NOT the same number). If it's the
   latter, no new column needed. If it's the former, add a new column using the pre-open
   indicative price/gap if that data is available in the lake (check ml_data_inventory.json for
   coverage — this may not exist for the full history since NSE only added F&O pre-open in Dec
   2025; document the coverage gap honestly if partial).

Version bump to ml_features_v3.csv.gz, update ml_features_manifest.json same as v1→v2 pattern.

═══════════════════════════════════════════════════════════
TASK 5b — HYBRID SEARCH (grid first, GP second — NOT pure random)
═══════════════════════════════════════════════════════════
Parameter space here is bounded (~750-1000 discrete combinations across Fib-level ×
confirm-candle × direction(continuation/reversal) × premium-behavior-class ×
OI-buildup-filter), not the open 39-signal space. Exhaustive grid search is more reliable than
random-seed GP for a space this size.

a) Grid search: enumerate all combinations from Task 5a's new columns, evaluate each via the
   SAME evolution-window fitness function ml_gate.py already uses (day_pnl on evo_bars only).
   No validation/lockbox touch during this step — same discipline as the existing GP run.
b) Report the full grid, ranked by evo-Sharpe, same salvage-report format as
   ml_gp_salvage_report.json (rule | sr_evo | sr_val | trades_evo | trades_val | pnl_val | dsr |
   dsr_pass) — run validation and DSR on the full top-N (not just top-1), same "honest, show the
   collapse" discipline as the existing salvage report. Expect most to fail DSR just like the
   last run — that's fine, report honestly either way.
c) Seed step (only if 5b finds evo-Sharpe worth pursuing further, e.g. >1.5 with reasonable
   trade count): take the top 5-10 grid survivors and feed them as SEEDED individuals into a
   fresh ml_gp.py wave-based run (reuse existing wave/checkpoint/resume machinery), mixed with
   the existing 39-signal vocabulary so GP can explore combining this family with signals like
   iv_rank_60d, atm_skew, pcr_oi etc. and fine-tune continuous thresholds (exact OI-buildup
   cutoff, exact premium-divergence %) that the grid can't explore efficiently.
d) Same 4-gate acceptance chain as before: evo-fitness → validation → DSR@fullN → lockbox.
   Nothing from this task skips a step just because it started from a user hypothesis rather
   than random seed — a good idea still needs to survive the same honest test as a random one.

═══════════════════════════════════════════════════════════
TASK 5c — REPORT
═══════════════════════════════════════════════════════════
Update CLAUDE.md session entry + ARCHITECTURE_LOG same format as the 2026-07-14 ML mining entry.
Explicitly compare this family's convergent zone (if any) against the existing VRP short-straddle-
overnight zone from Task 3 — is this a genuinely different edge (low correlation) or another path
to the same underlying pattern? Note in DONE LOG of ml-strategy-mining-tasklist.md.

---

## DONE LOG
- [x] **Task 5a — features → ml_features_v3.csv.gz (2026-07-14).** Added: first-candle (09:15)
  fib retracement ladder (23.6/38.2/50/61.8/78.6%, from-high + from-low; continuous `fibpos_k`
  = (close_k−low1)/range for k=2/3/5), `conf_k` (continuation +1 / reversal −1 vs candle-1 body
  dir), `pm_class` (premium-divergence 6-class), `oi_bld_k` (4-quadrant OI buildup), `expiry_regime`
  (Thu-era 0 / Tue-era 1). **DATA-REALITY ADAPTATION (verified, honest):** the option-chain lake
  is **5-minute bars end-to-end** (300s spacing 2021-2026 — no 1-min option data exists, TRAP #100
  family). So premium-divergence is measured on the **09:15 option bar's own open→close** (= exactly
  candles 1-5, same contract, no drift), NOT per-1-min-candle; OI-buildup on the **09:15→09:20 5m
  snapshots**. Spot-side fib/confirm ARE true 1-min. Leak-safe: fib/conf/pm broadcast only to bars
  ≥09:20, oi_bld only to bars ≥09:25 (info-completion times). **Pre-open gap (spec #5): confirmed
  `gap_open_pct` = prev-close vs 09:15 open; NSE 09:08-09:12 auction indicative price is in NO data
  source here (spot + lake both start 09:15) → no auction-gap column possible, coverage gap
  documented.** `ml_features.py` VERSION=3, manifest updated.
- [x] **Task 5b — exhaustive grid (`ml_grid5.py`) → ml_grid5_report.json.** 2,100 rules × 12 combos
  = 25,200 evals, fitness on EVOLUTION window only, every eval logged. 2,592 met MIN_TRADES.
  **Result: 58 of top-60 are `short_straddle`.** Best robust-both: `[short_straddle|eod] k3
  fibpos≤0.382 CONT oi=long_buildup` evo **1.75** / val **2.45** / full 1.86 (129/20 trades, corr
  0.16 vs shipped). Several val > evo (2.58, 1.99, 1.90 — good OOS sign). **BUT every single rule
  FAILS DSR (0.000)** at N=4.35M cumulative — same collapse as the last GP run, exactly as the spec
  anticipated.
- [x] **Task 5c — GP seed (`ml_gp_seed5.py`) → ml_gp_seed5_report.json + verdict.** Top-14 grid
  survivors seeded + full 39-signal vocab, 2 bounded short-straddle waves. **DECISIVE:** the moment
  GP had access to IV/ATR/OI signals it **DROPPED the fib structure entirely** and re-converged on
  the EXACT existing VRP zone: `[short_straddle|OVERNIGHT] IF ce_iv>21.21 AND d_atm_oi_30m≤0.20 AND
  atr14_pct>0.13` — evo ~3.1-3.3 / val **2.24** / DSR up to 0.017 (still < ~3.9 bar). This is
  **identical** to Task 3's GP-v2 convergent zone (evo 3.29/val 2.25/DSR 0.02). **VERDICT: the
  fib/premium/gap family is NOT a genuinely new edge — it's a weaker intraday PROXY for the same
  high-IV short-vol pattern the VRP zone already captures. Given the real driver (IV level), the
  search prefers it and discards the opening-range fib gate.** Same family, low corr (0.10-0.15)
  only vs the LONG ORB fauj (expected: short vs long vol), formally unprovable after a 4.36M-rule
  cumulative search. Nothing deployable; lockbox untouched (nothing earned it). Honest negative,
  in the spirit the spec asked for.
