# algo-trader — ML-Assisted Strategy Mining (NIFTY Options)

**Created:** 2026-07-13
**How to use this file:** Paste the "PROMPT FOR CLAUDE CODE" block below into a
Claude Code session, run from inside the repo. As tasks complete, move them to
the DONE LOG at the bottom with date + commit hash (same convention as
`claude-code-worklist.md`). Add new tasks at the end rather than starting a
new file.

---

## Context (read before doing anything)

We already have a working strategy pipeline: `scratch/nifty_trend/run_hunt.py`
gates every candidate on significance (p<0.05) + Monte Carlo before it's
shippable, real DOM-calibrated slippage (ADR-005) is wired into
`bs_option.py`/`real_struct2`, and `runs/compare.json` produces a correlation
matrix across strategies. All of that stays as the FINAL gate — nothing in
this file replaces it.

The gap: every strategy shipped so far was **hand-designed by us** (RSI/EMA/
range logic, chain-zone, straddle-ORB, etc.), then validated by the pipeline.
We now want to flip part of the discovery step — use ML on our own recorded
NIFTY option-chain + spot data (IV, OI, premium, spread, ATR, PCR, DOM-derived
features) to **surface candidate entry conditions we haven't thought of**,
then run those candidates through the EXACT SAME existing gate before
anything is called a strategy.

**Known danger, already proven by our own data:** the correlation matrix
already shows effective-independent-strategies ≈3.8 out of 7 shipped (chain_
zone_naked vs chain_zone_credit = 0.994 correlation). ML search over a large
feature/parameter space will find MANY more correlated near-duplicates and
some outright false positives (multiple-testing) unless we add stronger
statistical guardrails than the current pipeline has. Task 0 below is
mandatory and comes BEFORE any ML mining — do not skip it to get to the fun
part.

**Underlying:** NIFTY (BANKNIFTY later, once NIFTY pipeline is proven).
**Data available:** option-chain lake (IV/OI/premium/spread/strike/expiry),
spot candles, ATR/PCR derived series — confirm exact tables/paths at Task 0a.

---

## PROMPT FOR CLAUDE CODE

```
Work through tasks in order. Task 0 is a hard prerequisite — do not run any
ML search until Task 0's guardrails exist and are wired into the gate.

═══════════════════════════════════════════════════════════
TASK 0 — STATISTICAL GUARDRAILS (prerequisite, do this first)
═══════════════════════════════════════════════════════════
Our existing run_hunt.py gate (p<0.05 + Monte Carlo) was designed for a
handful of hand-picked candidates per session, not for hundreds/thousands
of ML-generated candidates. At ML scale it will pass false positives by
chance alone unless we correct for it.

a) DATA INVENTORY: enumerate exactly what NIFTY option-chain history we have
   (date range, granularity, which fields — IV/OI/premium/bid-ask/spot —
   are real vs backfilled/synthetic per the earlier ADR on rollingoption
   spread caveats). Report as a table before building anything on top of it.
   Flag any gap that would silently bias a model (e.g. survivorship in which
   strikes got recorded, missing bid-ask pre-Phase-A3).

b) DEFLATED SHARPE RATIO: implement a deflated-Sharpe check (Bailey/López de
   Prado style) that adjusts the significance bar based on HOW MANY candidate
   variants were actually tried in a mining run (not just the one that
   "won"). Wire this into run_hunt.py as an additional required pass — a
   candidate must clear both the existing p<0.05 gate AND deflated-Sharpe
   AND Monte Carlo. Log the number-of-trials figure used for each deflation
   calc so it's auditable later, not a black box number.

c) PURGED WALK-FORWARD CV: replace/augment whatever train/test split ML
   candidates use with a purged + embargoed walk-forward split (no
   overlapping-window leakage between option-chain rows that share the same
   underlying move). Document the exact purge/embargo window chosen and why.

d) HARD OOS LOCKBOX: carve out the most recent ~2-3 months of NIFTY
   option-chain data into a lockbox directory that NOTHING in this task
   list — no feature selection, no hyperparameter tuning, no threshold
   picking — is allowed to read until a candidate has already cleared (b)
   and (c) on the earlier data. Only then run it once against the lockbox
   as a final check. If a candidate fails on the lockbox, it's dead — no
   re-tuning against lockbox results, that defeats the purpose.

e) MULTIPLE-TESTING LOG: every mining run (Task 2/3) must append one row per
   candidate tried — not just winners — to a persistent log (e.g.
   scratch/nifty_trend/ml_mining_log.csv), so (b)'s deflation number is
   always computed against a true trial count, not a cherry-picked one.

Report before/after: what the existing gate would have passed vs what it
passes now with (b)-(d) added, using a known example (re-run one of our
already-shipped strategies through the stricter gate as a sanity check that
it still passes — if it doesn't, something is calibrated wrong).

═══════════════════════════════════════════════════════════
TASK 1 — FEATURE LAYER (shared by both mining approaches below)
═══════════════════════════════════════════════════════════
Build scratch/nifty_trend/ml_features.py: turns our existing option-chain +
spot data into a clean per-bar/per-signal-window feature table. Candidate
features (confirm availability against Task 0a's inventory, drop what we
don't actually have real data for):
  - IV-Rank / IV-Percentile (existing IV series)
  - OI shift at strike, Put-Call Ratio, Max-OI-strike distance from spot
  - Realized vol (existing RV builder in bs_option.py) vs IV spread/ratio
  - ATR, time-of-day bucket, day-of-week, days-to-expiry
  - Spread/liquidity proxy (bid-ask where real; flag synthetic-spread rows
    per the earlier ADR caveat, do not silently mix real+synthetic)
  - Rolling realized straddle/strangle premium decay curve shape
Output: one versioned parquet/csv per feature-set revision (don't silently
overwrite — same discipline as Task 6 in the other worklist: show before/
after if features change under a strategy that was already mined).

═══════════════════════════════════════════════════════════
TASK 2 — APPROACH A: FEATURE-IMPORTANCE → RULE EXTRACTION (do this first,
lower risk, more interpretable, easier to explain in a KHAZANA video)
═══════════════════════════════════════════════════════════
a) Train a gradient-boosted tree (XGBoost/LightGBM) on Task 1's feature
   table. Target: define 2-3 candidate targets explicitly and test each
   separately, don't blend them — e.g. (i) forward 15-min NIFTY spot return
   sign, (ii) forward ATM-straddle premium decay outperformance vs a flat
   theta baseline, (iii) profitable-setup binary label using our own
   existing SL/target definitions. Use purged walk-forward CV (Task 0c).
b) Extract top feature importances (SHAP preferred over raw gain/split
   importance — less biased toward high-cardinality features). Report the
   top 5-8 features per target with their SHAP direction (feature high →
   pushes target which way).
c) Convert the top 2-4 features into an EXPLICIT, READABLE rule (e.g. "IV-
   Rank > 70 AND OI-shift-at-ATM > X AND entry window 9:20-9:25") — not a
   black-box model prediction used directly as a signal. This rule is what
   gets backtested, not the model's raw output.
d) Run the resulting rule(s) through run_hunt.py with Task 0's stricter
   gate (deflated-Sharpe + purged CV + eventual lockbox check). Log every
   rule variant tried (even ones that get rejected at step (c) for being
   too close to an existing shipped strategy per the correlation matrix)
   to the multiple-testing log (Task 0e).
e) Report format: candidate rule | target used | features | OOS Sharpe |
   deflated-Sharpe pass y/n | correlation vs existing 7 shipped strategies
   (from runs/compare.json) | lockbox result (only after b-d clear).

═══════════════════════════════════════════════════════════
TASK 3 — APPROACH B: GENETIC PROGRAMMING (only after Task 2 is running
cleanly — this is higher-risk/more overfit-prone, needs Task 0's guardrails
proven first)
═══════════════════════════════════════════════════════════
a) Use gplearn or DEAP to evolve entry/exit condition trees over Task 1's
   feature set. Fitness function must be OOS-Sharpe-under-purged-CV (Task
   0c), NOT in-sample fitness — this is the single most common way genetic
   search overfits.
b) Cap tree depth/complexity explicitly (document the cap and why) — an
   evolved rule with 15 nested conditions is not deployable or explainable,
   even if it backtests well; it's almost certainly overfit noise.
c) Every generation's population (not just the best individual) counts
   toward the multiple-testing log (Task 0e) — genetic search tries far
   more candidates per run than approach A, so this number matters a lot
   for the deflated-Sharpe calc.
d) Same reporting + gate + lockbox discipline as Task 2e.

═══════════════════════════════════════════════════════════
TASK 4 — INTEGRATION / REPORTING
═══════════════════════════════════════════════════════════
a) Any candidate that clears Task 0-3's full gate (including lockbox) gets
   added to runs/index.json the same way existing strategies are, and shows
   up in the existing hub/lab/registry UI — no new dashboard needed.
b) Update this file's DONE LOG + append any new gap/TRAP found (same
   self-maintenance convention as claude-code-worklist.md) — don't just
   mention it in chat and let it evaporate.
c) Do NOT auto-deploy anything from this file to paper or live — every
   candidate that clears the gate needs an explicit user go-ahead before
   `run_hunt.py`'s output becomes a strategies/live/*.py file, same as any
   other strategy.
```

---

## Guardrails — quick reference (why each exists)

| Rail | What it prevents |
|---|---|
| Deflated Sharpe (0b) | ML tries hundreds of candidates; plain p<0.05 alone lets luck through at scale |
| Purged + embargoed walk-forward (0c) | Overlapping option-chain windows leak future info into "OOS" results |
| Hard OOS lockbox (0d) | Prevents unconsciously re-tuning against the final test set (the most common ML backtest failure) |
| Multiple-testing log (0e) | Makes the deflation calc honest — every trial counted, not just winners |
| Rule extraction over raw model output (2c) | Keeps the shipped signal explainable/auditable, not a black box in a live options strategy |
| Fitness = OOS Sharpe, not in-sample (3a) | Genetic search will otherwise evolve toward pure in-sample noise fitting |
| Complexity cap (3b) | An unreadable evolved rule can't be trusted with real capital or explained on camera |
| Correlation check vs existing 7 (2e/3d) | Don't ship a new "strategy" that's actually strategy #04 wearing a different hat (0.994-correlation problem, already proven in our own data) |
| No auto-deploy (4c) | Every new signal — ML-found or hand-designed — needs the same human go-ahead before touching paper/live |

---

## DONE LOG

*(Move completed tasks here with date + commit hash as they land.)*

- [x] **Task 0 — statistical guardrails** — 2026-07-13, commit `d2b8da5`
  - 0a inventory: `ML_MINING_DATA_INVENTORY.md` + `ml_data_inventory.py/.json`. Lake =
    84 series (WEEK/MONTH × CE/PE × ATM±10) × 1246 days (2021-07-01→2026-07-10) × 5-min,
    REAL premium/IV/OI/volume/strike/spot. Bid-ask NOT recorded (DOM haircut only —
    never present spread features as real). Deep-ITM zero-IV gradient (up to 43% on
    WEEK CE_ATMm10) → IV features stay within ~ATM±5. TRAP #109: rolling series =
    state features only, held-position P&L must use real_struct2.
  - 0b deflated Sharpe: `ml_gate.deflated_sharpe()` (no scipy — runs on VPS), wired into
    run_hunt.py as required pass (p<0.05 AND DSR≥0.95 AND MC); N + variance source
    logged in meta.json for audit.
  - 0c purged walk-forward CV: `ml_gate.purged_walk_forward()` — purge 1d (labels are
    intraday→EOD, ≤1d overlap), embargo 5d (one weekly-expiry vol-regime cycle).
  - 0d lockbox: cutoff 2026-04-15 (last ~60 trading days). LOCAL mining lake physically
    carved by `ml_lockbox_split.py` (verify CLEAN); VPS master untouched. Access only via
    `lockbox_frame()` → audit-logged to `ml_lockbox_access.log`. Shared spot csv guarded
    by `trim_lockbox()`/`assert_no_lockbox()` instead of a physical split.
  - 0e trials log: `ml_mining_log.csv` via `ml_gate.log_trials()/count_trials()`; run_hunt
    logs every optimizer candidate.
  - **Before/after calibration (`ml_gate_check.py`) — honest finding:** best-of-1000 pure
    NOISE (annualized Sharpe 1.71 — old gate would love it) → DSR 0.34, rejected. Shipped
    mid_orb/orb_supertrend (p=0.000 old gate) → DSR 0.87/0.90 = below the 0.95 bar even
    with measured cross-trial variance. Gate discriminates cleanly (separation 0.53); the
    tasklist's "shipped must still pass" assumption turned out wrong in an informative
    way — consistent with DOM validation calling several strats marginal. **Policy set:
    0.95 bar applies to NEW ML-mined candidates; legacy grandfathered. User call to revisit.**
  - GAP/TRAP found: DSR variance from OOS-window trial Sharpes over-deflates ~2× (shorter
    window = wider estimator noise) — use time-weighted full-window trial statistic
    (fixed in ml_gate_check.py). Also: local ML stack = Python 3.8 pins (sklearn 1.3.2,
    lightgbm 4.5, xgboost 2.0.3, shap 0.44.1); mining runs LOCAL (VPS = 1-core/4GB live
    box, no ML libs), lake synced local + lockbox-split.

- [x] **Task 1 — feature layer** — 2026-07-13, commit `cc4cbe4`
  - `ml_features.py` → `ml_features_v1.csv.gz` (39 cols × 86,703 5-min bars,
    2021-07-29→2026-04-13; ends pre-lockbox, `assert_no_lockbox` enforced in-builder).
  - IV features within ATM±5 only; OI features (PCR/max-OI/imbalance/Δ30m) use ±10;
    premium-decay shape vs trailing 5-day linear-theta slope (leak-free, shift(1));
    liquidity columns carry `_proxy` suffix (bid-ask never real); dte from the
    verified expiry calendar (Thu→Tue switch handled).
  - Versioned + `ml_features_manifest.json`; existing version file never silently
    overwritten. Sanity: iv_rank∈[0,1], put-skew −0.9 mean, max-OI CE +3.2/PE −2.3
    offsets, rv_iv_ratio 0.66 (VRP visible) — all market-consistent.
- [x] **Task 2 — feature-importance rule extraction (Approach A)** — 2026-07-13, commit `c0faae4`
  — **pipeline complete, ZERO candidates cleared the gate (honest result)**
  - 2a: 3 targets separately (`ml_mine_a.py`): T1 fwd-15min direction (deadzone ±0.03%),
    T2 held-strike straddle 1h-decay vs flat-theta baseline (TRAP #109-safe labeling),
    T3 house SL/target long-setup binary. Purged-CV LightGBM.
  - 2b SHAP: T1 AUC 0.518 = **no edge, dropped** (market efficient at 15min). T2 AUC 0.81
    — but top driver dte_days = mechanical theta curve, not alpha. T3 AUC 0.59 —
    ORB-flavored (early-day + gap + rising IV).
  - 2c/2d (`ml_rules_a.py`): per-fold depth-2 tree → readable rule, ≥3/5-fold consensus
    required; real trade sims (short held-strike straddle w/ DOM slip 0.13%/leg; spot
    SL 1.5×ATR/2.5R). Every fold rule + consensus + reject → `ml_mining_log.csv`.
  - 2e verdicts: **T2** consensus `dte_days ≤ 0.24` (expiry-afternoon straddle sell):
    234 trades, Sharpe 0.18, bootstrap p=0.53, **DSR 0.001 → DEAD** (pattern real,
    trade dies to gamma+costs — matches LESSONS "intraday vol dies to costs").
    **T3**: no stable cross-fold consensus → **DEAD**. Lockbox untouched (nothing
    earned a final eval — as designed).
  - GAP noted: consensus-median bug fixed (same-feature conds must collapse to binding
    threshold before median). Next mining rounds should try positional/overnight
    targets (ADR-006 lane) — intraday premium targets keep hitting the known cost wall.
- [x] **Task 3 — genetic programming** — 2026-07-14, commits `6b0cb4f`/`7fa8713`/`8286c4e`
  — **~4.0M rules over two runs, ZERO clear the formal gate; convergent pointer = VRP family**
  - Engine: wave-based GP (`ml_gp.py` + `ml_gp_precompute.py`), 12 combos = {long CE/PE,
    short/long straddle} × {eod, overnight, expiry}, real WEEK premium held-strike,
    Zerodha charges + DOM slip, single lot. Evolution 2021-07→2025-06 only; validation
    2025-07→2026-04 never seen by selection; ≤4 AND-conditions; per-gen checkpoints,
    per-wave milestones; every genome → ml_mining_log.csv (654MB, disk-only audit log).
  - **v1 overnight run (511 waves, 3.43M rules) VOID — TRAP #114:** entire leaderboard
    (val Sharpe 7.1!) was the WEEK-series contract-roll seam — buy dying expiry-afternoon
    straddle, "exit" into next week's contract (94.4% of signals = expiry-day bars; ~0
    trades on the guarded table). Fixed: exit day must be ≤ entry's expiry. LESSONS #114.
  - Salvage scan of v1's clean (eod/expiry) rules: best evo Sharpe 3.5-3.6 ALL collapsed
    to validation ~0 — textbook curve-fit, the train/validation split did its job.
  - **v2 run (roll-guarded, 115 waves, 900k rules):** top-50 distinct rules ALL converge
    on ONE basin — `IV > 21 AND ATR high AND OI not building → short straddle overnight`.
    Best: evo 3.29 / **val 2.25 (positive, 10 trades)** / DSR 0.02 vs bar ~3.9 @ N=4.0M /
    corr 0.15 vs 7 shipped / val P&L +₹42k single lot. = the machine independently
    rediscovered the hand-validated VRP panic-fade zone. Real edge, existing strategy,
    formally unprovable after a 4M-rule search. Lockbox untouched (nothing earned it).
  - DSR methodology lesson: deflation variance must come from RANDOM unselected rules
    (null std 0.739 ann); the evolved population's spread implied an unpassable sr*≈13.
- [x] **Task 4 — integration/reporting** — 2026-07-14: nothing to add to runs/index.json
  (no candidate cleared the gate — by design); DONE LOG + LESSONS self-maintenance done
  (TRAP #114); no-auto-deploy discipline held throughout (zero deploys without go-ahead).
  Mechanism for future clears: run_hunt.py already writes runs/<slug>/ + index.json.
- [x] **Task 5 — Fib-retracement + premium-divergence + pre-open-gap (user-seeded), 2026-07-14.**
  Spec: `ML_MINING_TASK5.md` (that file's DONE LOG has the per-subtask detail). 5a: `ml_features_v3`
  (fib ladder + confirm-candle + premium-divergence + OI-buildup + expiry_regime; premium/OI mapped
  to the lake's real 5-min resolution — 1-min option data doesn't exist; pre-open auction gap not in
  any source, documented). 5b: exhaustive grid `ml_grid5.py` (25,200 evals) → **58/60 top rules =
  short_straddle, best evo 1.75/val 2.45, ALL fail DSR 0.000**. 5c: GP seed `ml_gp_seed5.py` →
  **GP discarded the fib structure and re-converged on the EXACT VRP overnight zone** (ce_iv>21 +
  ATR-high + OI-flat, val 2.24, DSR 0.017). **VERDICT: same family as Task 3's VRP zone, not a new
  edge — a weaker intraday proxy for the same high-IV short-vol signal; unprovable at N=4.36M.**
  Nothing deployed, lockbox untouched. Confirms the DO-NOT-REDO intuition: NIFTY intraday/weekly
  short-vol is saturated — the machine keeps rediscovering the one VRP basin from every seed.
