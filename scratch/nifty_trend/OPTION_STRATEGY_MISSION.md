# 🎯 OPTION-STRATEGY MISSION — 10 Robust Strategies

**Owner:** Arsalan · **Started:** 2026-07-10 (Fri) · **Deadline goal:** Mon 2026-07-13
**Rule:** RELENTLESS. Ye mission tab tak zinda hai jab tak 10 validated strategies ship na ho jayein.
Har naye session mein: neeche **"RESUME HERE"** padho, current phase se aage badho.

> This file is mission-control. It survives context resets. Update it after EVERY strategy + every phase.

---

## 🔴 RESUME HERE (single source of truth for "where are we")

- **🔀 PARALLEL HUNTS = architecture now (user, 2026-07-12) — READ `MULTI_SESSION.md` BEFORE
  launching/killing ANY build.** 2-3 hunts / Claude sessions side-by-side: launch via
  `python hunt.py build_X.py` (detached + log), `python hunt.py status` = who runs what.
  `hunt_guard.py` (wired inside run_hunt.main) = slug claim (duplicate refused), locked
  `runs/index.json`/`compare.json` writes, dead-pid prune, Windows-safe locks (self-deadlock
  race found+fixed in testing, 5/5 concurrency tests pass). **NEVER blanket-kill python —
  kill only a pid from status that owns YOUR slug** (2026-07-10 cross-session kill incident).

- **🧪 #08 = Pivot-Extreme Continuation (USER'S OWN HYPOTHESIS, 2026-07-12) — VALIDATED, borderline
  on the Sharpe gate, NOT yet deployed (user decision pending).** User's market-structure insight:
  at extreme pivots (S3-S5/R3-R5) the intraday BOUNCE is a trap (trapped traders' exits get bought
  into by the big seller for liquidity), the real edge is CONTINUATION in the fall/trend direction.
  Data confirmed BOTH claims: `pivot_rev` (naive bounce) NEGATIVE every config (Sharpe −0.4..−1.6);
  `pivot_break` (fresh close-through S3/S4/S5 or R3/R4/R5 in trend direction) POSITIVE.
  **Winner: NIFTY 3m, band(3,4,5), tol=5, atr_sl=2.5, rr=1.5, trail_atr=2, all-day.**
  Gates (bs|full): **p=0.014 ✅** (5m was 0.077 ✗), trades 400 ✅, maxDD −1.5% ✅, MC worst-5%
  Sharpe 1.06 (all-positive) ✅, train 0.70 / **OOS 1.94** (no decay — OOS stronger) ✅,
  **Sharpe 0.967 = 3% short of the ≥1.0 gate ⚠️** → borderline pass, honesty says: user reviews
  dashboard before any deploy. Net +23.4%, win 52%. `runs/pivot_continuation/` (build_pivot.py,
  designs in intraday_engine: pivot_rev + pivot_break via _daily_levels S/R arrays).
  **Claim-2 next-day variant TESTED (2026-07-12) — spot-REAL, option-UNSHIPPABLE.**
  Design: day-D pierces extreme S/R AND recovers by 15:00 (trap confirmed) → hold
  breach-direction overnight, exit next-day 15:00. Screen: plain breach = NO edge;
  breach+bounce = positive BOTH directions (LONG avg +33pts win 67%, SHORT +16pts) —
  the bounce is the essential ingredient, exactly the user's story. Full spot backtest
  band(3,4,5)/eod-entry: +25.7%, p=0.034 ✅ SIGNIFICANT, tr 0.55/oos 0.63. BS-model
  reprice looked great (Sharpe 2.34) **but REAL Dhan lake (held-strike, MONTH, charges+slip)
  = −0.2%/5yr, Sharpe 0.00** — same window BS said +7.1%/1.63 → pure TRAP #106 gap.
  Why: avg edge ~17 spot pts/trade vs ATM delta 0.5 (half the move) + overnight theta +
  real VRP-rich premium + slippage. ITM offsets 0/2/4/6/8 all ≈ noise (best +1.1%/5yr).
  **Conclusion: the phenomenon is REAL at spot (user's hypothesis right again) but no
  option vehicle keeps the edge at retail costs; linear vehicle (futures) would keep it
  but = uncapped overnight gap risk, out of mission scope.** Scripts: pivot_nextday.py
  (screen), pivot_nextday_bt.py (full+sig), vps_pivot_real.py (real-lake check, VPS).
  Intraday #08 (3m, same phenomenon, options-viable) remains the shippable expression.

- **✅ USER APPROVED ALL THREE for PAPER (2026-07-10 night):** #01 Long Straddle, #02 Debit
  Vertical, #03 ORB+Supertrend — "teeno hi ko paper trade karenge".
- **📛 NUMBERING CONVENTION (user, 2026-07-10):** every mission strategy carries its number
  everywhere — hub titles ("01 - …"), MISSION table, live-trader filenames (`02_…_trader.py`,
  like the existing `01_rsi_v1.py` precedent) and dashboard display names. #01's deployed file
  stays `straddle_trader.py` (renaming a RUNNING trader's file/tag = orphan-position risk,
  LESSONS traps) — it is documented as **01** everywhere else.
- **✅ ALL 3 DEPLOYED PAPER ON VPS (2026-07-11 00:06 IST):** 01 `straddle_trader.py` (was already
  running), 02 `02_debit_vertical_trader.py` (dvert_v1), 03 `03_orbst_trader.py` (orbst_v1) — all
  `--paper`, config keys active:true on VPS nifty_config.json, "Market closed" loop, will trade
  from Monday 09:16. Dashboard STRATEGIES keys: straddle / dvert / orbst.
- **✅ #04 = Chain-Zone (user's own Pine "Ars_Auto_Rev_Chain") BUILT + APPROVED for paper (2026-07-11).**
  NIFTY 5m, ATM option BUY (long→CE, short→PE), stop_only ATR×2.5, p=0.000, Sharpe 1.95, +69.1%, DD −3.1%,
  train 1.91≈OOS 2.04, MC orig 2.64≈median 2.66. `runs/chain_zone_longatm/` (dashboard has 5 PASS toggles:
  ①Instr ②RMS ③BS-BUY ④Naked-SELL ⑤Credit-Spread — selling forms both LOSE, short-gamma caps winners).
  Live trader `strategies/live/04_chainzone_trader.py` (config `chainzone_v1`, STRATEGIES key `chainzone`,
  active:false paper) — compute_signal 40/40 parity vs backtest, py_compile OK. Pine-matched build:
  `chain_zone_v1.pine`. FAQ system added (faq_lib.py → meta.faq, dashboard Philosophy panel, backfilled all runs).
  **✅ VPS-DEPLOYED PAPER 2026-07-11 (holiday, 0 open positions):** 3 chain run-folders + runs/index.json merged
  (hub shows 8 rows); 04_chainzone_trader.py scp'd (VPS py_compile + deps OK); chainzone_v1 surgically added to
  VPS nifty_config.json (active:true paper, all 13 existing keys preserved + .bak); STRATEGIES "chainzone" line
  surgically inserted into VPS trader_dashboard.py; algo-dashboard restarted (KillMode=process → straddle/dvert/
  orbst PIDs survived); 20s run-test clean (banner+PAPER, no crash, 0 phantom orders). Monday 9:10 scheduler
  auto-starts it paper. STRATEGIES key `chainzone`.
- **✅ #05 APPROVED + VPS-DEPLOYED PAPER (2026-07-11 13:00):** `strategies/live/05_backspread_trader.py`
  (config `backspread_v1` active:true paper, STRATEGIES key `backspread`, broker kite). 3-leg entry:
  BUY 2×OTM FIRST (RMS-gated) → SELL 1×ATM (covered 2:1, gate=False) → SELL-fail = rollback longs;
  exit closes SHORT first; exits at ±frac of sold-ATM premium; skip-expiry via risk_gate.is_expiry_day.
  Signal parity 40/40 AFTER fixing OR-cutoff `<`→`<=` (backtest uses tt<=or_end — 02/03 traders may
  have the same off-by-one, flagged as separate task). Deploy: 0 open positions, KillMode=process,
  4 trader PIDs survived restart, run-test clean, 0 phantom orders. Monday 9:10 scheduler auto-starts
  (5 paper strategies now: straddle/dvert/orbst/chainzone/backspread).
- **✅ #05 = Ratio Backspread @ Mid-day ORB BUILT (2026-07-11), pending user approval.** BS Sharpe 1.55,
  p=0.002, +29.9%, DD −2.0%, 1124 trades. `runs/ratio_backspread/` (build_s5_backspread.py; osx DIRECTIONAL
  "ratio_backspread" + bs_off knob + |side|-scaled charges + sold-ATM-premium tp/sl ref + chain_zone as
  directional signal). VPS hub 10 rows. **⚖️ Compare page + CORRELATION MATRIX added** (compare.html +
  build_compare.py → runs/compare.json, wired into run_hunt): ORB-family inter-corr 0.32-0.66,
  chain_zone_longatm 0.13-0.26 vs sab = best diversifier. **Brother's DOM data found:**
  /root/order_book_deploy — June-9→today tick 20-level depth NIFTY FUT+ATM CE/PE (no OI/IV, 1 strike) —
  usable for BS-model validation + real-spread slippage; analysis offered, pending user OK.
- **▶ NEXT ACTION:** user approval on #05 (then live paper trader 05_backspread_trader.py); Monday watch
  chainzone_v1 + other 3 paper logs; Track-B theta strategies when collector data matures (the true
  inverse-correlation leg for the portfolio). Candidate pool for #06+: Track-A
  long/debit structures on NEW signals (donchian/supertrend/gap variants on structures, tod windows),
  since plain-signal screen showed edge only in the ORB family. Monday: watch all 3 paper logs +
  health_check 9:20. (Short-vol Track-B collector-gated; Long Strangle FAILED p=0.072;
  mean-reversion family negative — see table.)
- **✅ #3 FOUND (2026-07-10 night): ORB + Supertrend confirm (15m, directional ATM long-option).**
  BS pass: Sharpe **2.06**, net **+49.7%**, maxDD **−1.0%**, win 44%, trades 1024, **p=0.000** (1000
  perms), robust 0.96 (both 15m AND 5m significant). Params: or_min=60, orb_k=1.0, ST(14,3.0),
  atr_sl=1.5, rr=2.0, skip-expiry ON. Run: `runs/orb_supertrend/`. Builder: `build_s3_orbst.py`
  (pins run_hunt to orb_st). Also: skip-expiry (policy A) now applied in the DIRECTIONAL path too
  (`intraday_engine.backtest`, default ON) — parity with option_structures.
- **📕 Full methodology + glossary:** `OPTION_STRATEGY_PLAYBOOK.md` (read for the "why/how/terms" +
  fresh-clone resume steps). Dashboards now self-document (📖 Philosophy panel + Pass/term notes).
- **DATA EXTENDED 2018→2026 (8.5yr, was 4.5yr).** Dhan serves NIFTY 1-min from 2018 (2017 = nothing).
  `nifty_1min.csv` merged (788,409 rows) + committed. `_extend_dl` (VPS) is the download method.
- **✅ EXPIRY FIX + POLICY A DONE (2026-07-10, 8.5yr, vrp=1.2, 500-perm, skip-expiry ON).** Corrected
  weekday (Thu→Tue eff 2025-09-01, official circulars) AND policy A applied = skip NEW entries on the
  (correct) expiry day (0DTE-inflation guard). Removing the expiry-lottery trades LOWERED headline %
  but IMPROVED honesty — and pushed the straddle over the significance line. Both `runs/<slug>/`
  refreshed. BS pass = deployable numbers:
  - **#2 Debit Vertical @ ORB (15m)** — Sharpe **1.67**, net **+38.6%**, maxDD −2.1%, win 51%,
    trades 1227, fees ₹1,33,779, **p=0.0000 ✅ SIGNIFICANT**. params: tp=100%, SL=100%, OR=15, k=1.0,
    wing_off=10, skip_expiry. STRONGEST/cleanest. 🧪 awaiting user approval.
  - **#1 Long Straddle @ ORB (5m)** — Sharpe **3.55**, net **+81.7%**, maxDD −1.0%, win 43%,
    trades 1625, fees ₹1,96,639, **p=0.0360 ✅ SIGNIFICANT (now passes — was 0.054 pre-policy-A)**.
    params: tp=50%, SL=100%, OR=15, k=0.5, skip_expiry. LIVE paper trader running. 🧪 awaiting approval.
- **Findings (unchanged):** only LONG-vol structures positive on BS premium; short straddle/fly
  −95..−100% → Track-B gated. Mid-day straddles FAIL rotation (p≈0.25 — cheap-premium artifact).
- **Infra built:** `option_structures.py` (multi-leg BS engine + `_precomp` cache 4x) +
  `run_structure.py` (structure hunt + `write_run()`) + `vrp_mult` honest premium stress +
  `verdict_straddle.py` / `build_straddle_run.py`.
- **Blocked on user:** APPROVE/REJECT #1 Long Straddle, #2 Debit Vertical, #3 ORB+Supertrend.
- **Strategies shipped:** 0 / 10 (3 built + validated, all pending approval)
- **Collector:** ✅ LIVE on VPS (`algo-optionchain` systemd, 1-min, NIFTY+BNF ATM±10 + India VIX).
  Data → `_TRADING_DATA/OptionChain/<SYM>/<SYM>_YYYY-MM-DD.csv`. Verified 2026-07-10 11:20 IST:
  NIFTY spot 24172 / VIX 12.48 / 21 strikes, full OI+chgOI+IV+greeks. Accumulating forward.

---

## Per-strategy = validate + PAPER-deploy, FULLY done before the next (user 2026-07-10)
No separate "Phase B" and no "pick after 10". EACH strategy is completed end-to-end in one go:
  1. design → backtest → 3-pass dashboard (`runs/<slug>/`)
  2. build its live trader (`strategies/live/<name>_trader.py`, execution_gateway + order_store,
     NEW_STRATEGY_CHECKLIST) → deploy PAPER on VPS → appears in dashboard Log tab
  3. user sees both → approve → NEXT strategy
All 10 run on PAPER from Monday (user paper-trades them live, doesn't post-hoc select).
Reference live trader: `strategies/live/orb_trader.py`. Long Straddle's = `straddle_trader.py`
(config `straddle_v1`, `STRATEGIES["straddle"]`) — 2-leg ATM CE+PE, combined-premium % exit + 3:15 EOD,
restart-recovery, half-open rollback. Deployed + running PAPER 2026-07-10.

## Data-regime policy (user's sharp Q, 2026-07-10) — judge on the CURRENT regime
Data now spans 2018-2026 (8.5yr) — includes very different regimes (pre-COVID, COVID crash, the
2022+ retail-options boom). Two guardrails so old-regime data can't mislead:
- **Lot-size / SEBI changes DON'T corrupt the edge:** we report % / Sharpe / DD / significance, all
  INVARIANT to lot size (multiplying every trade's ₹ by a constant changes nothing). We apply TODAY's
  lot size throughout → the ₹ + charge-drag are forward-realistic. (Real historical lot/premium only
  matters for Track B, which is why we collect real chain data forward.)
- **OOS = the RECENT window is the PRIMARY judge.** min(train,OOS) ranking already forces the params
  to work recently; if a strategy is great on Full-2018 but weak on OOS-recent, we REJECT it. The
  straddle's OOS Sharpe (4.51) BEAT its train (4.14) → it works BETTER in today's regime, not worse.
  Full-2018 is stress/robustness context, never an override of a strong recent edge.

## Ground rules (locked with user 2026-07-10)

- **One strategy at a time.** Design → mockup → user approves → backtest → 3-pass dashboard → user
  approves → NEXT. Never batch.
- **Underlying = NIFTY 50** for all backtests (4.5yr 1-min on disk). BANKNIFTY history too thin to
  backtest; BNF only participates in the forward collector.
- **Data window:** 2022-01-03 → today (~4.5yr), reuse `scratch/nifty_trend/nifty_1min.csv`.
- **Direction:** long / short / both — doesn't matter, pick what validates.
- **Intraday only:** force-exit 15:15, no entry after 15:15, max 2 trades/day, no overnight.
- **No leverage (1x):** notional ≤ capital. Risk 1.5%/trade. Start capital ₹10,00,000.
- **Naked short legs** → sized on REAL Dhan margin, and every naked short needs a defined hedge/wing.
  Pure short straddle/strangle are tested as the *engine* but flagged "wings required for live".
- **Costs:** real Zerodha F&O charges on EVERY leg, EVERY side. Report gross AND net.
- **Never fabricate option-chain data.** BS-modeled premium is allowed for spot-signal strategies;
  true vol-arb (gamma / IV-crush) is NOT validated until the real collector has data.
- **Lot size** from Dhan scrip master — never hardcode.

## Validation gates (ALL must pass to ship)
- Sharpe ≥ 1 · Max DD ≤ 20% · Trades ≥ 100
- Statistical significance p < 0.05 (rotation/permutation test) — fail = luck/beta, iterate design.
- Optimize with train/OOS split, rank by **min(train, OOS)** Sharpe (TRAP #103), never OOS alone.
- Monte Carlo: original sits near MEDIAN, not top 5%.
- 1x result must beat NIFTY buy-&-hold on risk-adjusted basis.

## Pipeline (reuse ORB path — do NOT build new)
- Add design → `scratch/nifty_trend/intraday_engine.py` (`DESIGN_GRID` + `design_signals()`).
- Title → `run_hunt.py DESIGN_TITLE`. Multi-leg option structures → new `option_engine.py` layer that
  wraps spot signals into legs + prices each leg via `bs_option.py` (extend `reprice()` for multi-leg).
- Run `python run_hunt.py --name <slug>` → writes `runs/<slug>/{results.js,index.html,meta.json}` +
  appends `runs/index.json` → auto-listed in `hub.html`.
- 3 passes: **① Instrument** (spot P&L) → **② +RMS** (daily caps) → **③ +Black-Scholes** (ATM
  premium, real charges = deployable). Combos keyed `"<pass>|<period>"`. Read `RESULTS_SCHEMA.md` +
  `BS_OPTION_SIM.md` before touching results.js.
- Dashboard: reuse `dashboard_intraday.html` (Pass + Period toggles). No plain HTML.

---

## TRACK A — 10 honestly-backtestable now (BS from spot + realised-vol σ)

These trade an option STRUCTURE but the edge lives in a SPOT signal → BS-repriceable today.
Candidate pool (ship the 10 that pass gates; order by promise once screened):

| # | Strategy | Type | Legs | Naked? | Status | Slug | Sharpe | MaxDD | Trades | p | Verdict |
|---|----------|------|------|--------|--------|------|--------|-------|--------|---|---------|
| 1 | (pick most-promising via screen) | — | — | — | ⏳ screening | — | — | — | — | — | — |
| 2 | Short Straddle | theta sell | ATM CE+PE sell | ⚠️ wings for live | ⬜ | — | | | | | |
| 3 | Short Strangle | theta sell | OTM CE+PE sell | ⚠️ wings for live | ⬜ | — | | | | | |
| 4 | Iron Condor | defined-risk sell | short strangle + wings | ✅ defined | ⬜ | — | | | | | |
| 5 | Iron Fly | defined-risk sell | short straddle + wings | ✅ defined | ⬜ | — | | | | | |
| 6 | Long Straddle | debit breakout | ATM CE+PE buy | ✅ | ⬜ | — | | | | | |
| 7 | Long Strangle | debit breakout | OTM CE+PE buy | ✅ | ⬜ | — | | | | | |
| 8 | Bull Call Spread | debit directional | ATM CE buy + OTM CE sell | ✅ defined | ⬜ | — | | | | | |
| 9 | Bear Put Spread | debit directional | ATM PE buy + OTM PE sell | ✅ defined | ⬜ | — | | | | | |
| 10 | Long Butterfly | pin | 1-2-1 | ✅ defined | ⬜ | — | | | | | |
| + | Expiry-day theta (0DTE) | theta sell | OTM sell + time-stop | ⚠️ | ⬜ | — | | | | | |
| + | Scalp designs (fast spot → ATM) | scalp | ATM CE/PE | ✅ | ⬜ | — | | | | | |

Legend: ⬜ not started · ⏳ in progress · 🧪 backtested (awaiting approval) · ✅ shipped+approved · ❌ failed gates (iterate)

## 🚀 TRACK B UNLOCKED EARLY (2026-07-11) — Dhan PAID API serves REAL 5yr expired-option data!

**User has Dhan's paid "Expired Options Data" add-on.** Endpoint `POST /v2/charts/rollingoption`
(NOT in dhanhq 2.0.2 lib — REST only) returns REAL 5yr per-5min: premium OHLC + **IV + OI** +
strike + spot, for rolling ATM±N CE/PE, weekly+monthly. Exact params + gotchas in memory
[[dhan_rollingoption_data]]. This makes the whole "no historical chain, must BS-model / wait
weeks for collector" premise OBSOLETE for this account. Track-B is backtestable NOW.

**Infra built:** `optchain_dl.py` (downloader → `_TRADING_DATA/OptChainLake/NIFTY/<WEEK|MONTH>/
<CE|PE>_<off>.csv`, resumable, rate-limited "account" priority, recent-first, ATM-outward) +
`optlake_load.py` (loader: atm_frame / ironfly_frame / chain_frame / iv_rank_daily, epoch→IST,
IV-outlier clean, **coverage-guard so a mid-download partial wing can't give a bogus result**) +
`real_struct.py` (REAL-premium multi-leg backtest, slippage knob, engine-shaped res) +
`analyze_shortvol.py` (slippage sweep + tail).

**BREAKTHROUGH RESULT — short-vol works on REAL premium (dead on BS):**
- Short straddle: BS-modeled **−2.24 Sharpe / −100%** → REAL premium **+10.3 Sharpe / +120%**
  (slip 0.5%, 991 trades, 5yr). The VRP (real IV > realized) is the edge BS-from-realized can't see.
- **Tail is CONTAINED** (intraday + 10:00 entry after open + 3:15 exit avoids the overnight/gap
  killer): worst day −0.75% capital, 0/991 days lose >1%. Crash day 2024-06-04 (NIFTY −8% intraday,
  1557pt range) = **+₹11,550 PROFIT** (enter post-panic → IV crush).
- **❌❌ 2026-07-11 EVENING CORRECTION — #06 RETRACTED (LESSONS TRAP #109).** The Sharpe-8.9 result
  below was a MARKING BUG: rolling-ATM columns valued the position at "current ATM premium", not the
  HELD contract — hiding intrinsic losses on trend days (crash-day "+11,550" was impossible for a held
  short straddle through a 1557-pt range). Corrected HELD-STRIKE engine (`real_struct2.py`, tracks entry
  strikes through the ±10 offset grid): **iron-fly −54%/Sh −3.5, short straddle −12%, long straddle −27%,
  gamma-scalp −50% (0DTE −35%), calendar +18% but Sh 0.12 with −₹48.5k tail.** Intraday vol-trading in
  BOTH directions dies to costs on honest data. `shortvol_v1` **deactivated** same session (was paper,
  zero trades taken); run rebuilt as ❌ CORRECTED in hub/compare. #01–#05 UNAFFECTED (BS engine prices
  the held contract). **#07 outcome: gamma scalping REJECTED on both engines; C-family OI/PCR (5 designs
  × sweeps) all negative; OI-wall filter degrades #06-style selling monotonically; calendar fails gates.
  Honest result per mission rules: NO new edge to ship from the vol/OI family — engines corrected, negatives
  documented (runs/gamma_scalp, runs/shortvol_ironfly), search won't re-tread this ground.**
- **✅ #07 FOUND (take-3) = Mid-Day ORB on BANKNIFTY (new UNDERLYING = real diversification).**
  After gamma ❌ / OI-family ❌ / calendar ❌ (all honestly documented), ran the full validated
  pipeline on `bnf_1min.csv` (4.5yr) with BNF-correct market spec (`build_bnf.py`): strike step 100,
  lot 30 (labelled fallback), weekly Thu→**Wed** (NSE cir 119/2023, eff 04-Sep-2023) → weekly
  DISCONTINUED 2024-11-20 → monthly last-Thu/last-Tue — `expiry_calendar.banknifty_next_expiry()`
  (+ exact `is_banknifty_expiry_day` for policy-A skip; T verified: Wed-expiry-day 0.23d, monthly-era 22d).
  **WINNER: tod_orb @15m (OR=30, k=1.5, window 11:00–14:00, atr_sl=2.0, rr=1.5): p=0.011 ✅,
  BS pass Sharpe 1.46 / +23.6% / DD −3.0% / 342 trades / PF 1.64; train 1.50 ≈ OOS 1.47 (no decay);
  MC orig 1.55 ≈ median 1.56.** Honest rejects same hunt: BNF chain_zone negative screen; sess_rev
  p=0.399; orb_st p=0.052. `runs/banknifty_hunt/` (hub 13 rows, compare '07 BNF MidORB').
  **✅ DEPLOYED PAPER 2026-07-12** — `strategies/live/07_banknifty_trader.py` (config `banknifty_v1`
  active:true paper, STRATEGIES key `banknifty`, broker kite). BANKNIFTY spot sec_id 25, atr_rr exit
  (stop+target, unlike 03-05 stop_only), OR-cutoff `<=` + current-bar crossing threshold = **signal
  parity 50/50 + 30/30** vs backtest. skip-expiry via risk_gate.is_expiry_day (monthly-era). Deploy:
  0 open positions, KillMode=process restart (4 trader PIDs survived), run-test clean, 0 orphan.
  **Monday paper-fauj now 7 strategies** (00-05 + 07; 06 stays OFF/retracted). Hub badge 🟢 banknifty_v1.
- ~~✅ #06 BUILT~~ (RETRACTED — see above) = Short-Vol Iron-Fly ±8 (sell ATM CE+PE, buy ±8 wings), 10:00 entry, tp0.5/sl1.0, slip0.5%.**
  DEPLOYABLE (bs pass, real premium+IV+charges+slip): **Sharpe 8.9, +61%, maxDD −0.4%, worst day −₹2,469
  (−0.25% cap), win 78%, 991 trades.** Wing sweep: wider=better (±5→Sh5, ±8→Sh8.9); ±8 robust 7.33
  (train 9.78/OOS 7.33). `runs/shortvol_ironfly/` (build_shortvol.py). ±10 WEEK now complete → optional
  re-opt for marginal gain.
- **Significance — BOTH tests pass (surprised me honestly):** edge-p (daily-P&L bootstrap, mean>0)=0.000;
  timing-p (rotation)=0.000 — I expected timing-p HIGH (VRP structural) but the 10:00 entry genuinely
  beats random timing (post-open, full-day theta runway + elevated IV). So it's edge AND timing.
- **✅ CORRELATION confirms it's the INVERSE leg** (daily net P&L vs): mid_orb −0.05, orb_st −0.11,
  dvert −0.09, backspread −0.06, chain_zone −0.01, straddle +0.02. Slightly-negative to ALL breakout
  strategies = ideal diversifier (smooths book, both make money). This is the direct answer to the
  user's "we'll be stuck on ORB" worry.
- **▶ pending:** live paper trader (06_shortvol_trader.py) if user approves; optional ±10 re-opt; MONTH
  backfill finishing (~22m); Track-B OI/PCR strategies now unblocked on real OI data.

## 🧾 VOL-SELLING FAMILY — EXHAUSTED (honest, real data). Do NOT re-tread.

Tested comprehensively on REAL option premium+IV+OI (held-strike engine real_struct2, charges+slip):
| Variant | Result |
|---|---|
| Intraday short straddle (naked) | −12% |
| Intraday iron-fly ±8 | −54% (was the TRAP #109 phantom +61%) |
| Intraday gamma-scalp / 0DTE | −50% / −35% |
| Calendar (sell-weekly/buy-monthly) | +18% but Sharpe 0.12, −₹48.5k tail |
| **Positional iron-fly (weekly, 4-5d hold, hedged)** | **−8%, Sh −0.47** |
| **Positional iron-condor (weekly, hedged)** | **−7%, Sh −0.41** |
| OI/PCR/max-OI-wall/ΔOI (5 designs) | all negative |

**Conclusion:** systematic NIFTY option-PREMIUM SELLING has no edge accessible to a retail
account at these costs — intraday OR positional, naked OR hedged, weekly OR calendar. The VRP is
real in GROSS terms but 4-8-leg charges + slippage + the trend/gap weeks (short side blows through
the credit even with wings) eat it. `positional_vol.py` / `real_struct2.py` / `oi_signals.py` keep
the negatives documented. The REAL edges are DIRECTIONAL/big-winner (the 7 deployed: 00-05 + 07).
**IV-rank timing filter TESTED (2026-07-12, corrected engine):** it IS the right direction — the
ONE thing that flips positional short-vol from losing to non-losing. Sell only when IV-rank ≥ 0.5:
iron-condor −7.1%→**+1.4% (Sh 0.20)**, iron-fly −8.0%→+0.9%; IV-rank ≥ 0.85 → iron-fly +1.1% (Sh 0.28).
BUT it self-defeats on trade count: IV≥0.5 = only **36 trades/5yr**, IV≥0.7 = 19, IV≥0.85 = 10 — all
FAR below the trades≥100 gate, Sharpe 0.2-0.28 (below 1), worst week still −₹6-10k. So the VRP edge is
REAL but tiny + rare — not shippable standalone. Revisit only if: (a) brother's DOM shows real spread
<< 0.5% slip, or (b) the IV-gate is COMBINED with more qualifying-day sources (shorter IV lookback /
intraday IV spikes / IV-rich + directional-quiet) to raise trade count without killing the edge — a
new signal-design, not a knob. `positional_vol.py` has the `iv_min` gate wired for that future work.

## TRACK B — collector-gated (need REAL option-chain / VIX data first)

Built now but NOT validated until the collector has accumulated data (weeks). No fabricated edge.

| Strategy | Needs | Status |
|----------|-------|--------|
| Gamma scalping | live greeks + realized-vs-implied vol | 🔒 collector-gated |
| Delta-neutral harvest | live greeks | 🔒 collector-gated |
| VIX-crush / IV-rank sell | real India VIX + IV series | 🔒 collector-gated (trying VIX backfill from Dhan) |
| Max-OI S/R · PCR · Max-Pain · OI-buildup · ΔOI momentum · OI/price divergence | per-strike OI time-series | 🔒 collector-gated |

### 37.1 — Option-chain + VIX collector (Dhan, free, VPS daemon) ✅ LIVE
- **Scope (locked):** NIFTY + BANKNIFTY, **ATM ± 10 strikes**, snapshot **every 1 min**, market hours only.
- **Capture per strike:** CE/PE → OI, change-in-OI, IV, LTP/premium, volume, greeks (δ/θ/vega/γ). ✅
- **Also:** India VIX (sec_id 21) + underlying spot, aligned to same timestamps. ✅
- **File:** `_ops/option_chain_collector.py` (self-contained; Dhan `/v2/optionchain`; IPv4 force;
  VIX LTP via `dhan_rate_limiter` so it never 429s the live strategies). CLI: `--once` (probe),
  `--strikes N`, `--interval S`.
- **Store:** `_TRADING_DATA/OptionChain/<SYM>/<SYM>_YYYY-MM-DD.csv`, long format, cols =
  `datetime,underlying,spot,vix,expiry,strike,opt_type,ltp,oi,prev_oi,chg_oi,volume,iv,delta,theta,gamma,vega`.
  ~42 rows/underlying/min (21 strikes × CE/PE). Deep-ITM side sometimes has 0 greeks/IV (Dhan-side, recorded faithfully).
- **Deploy:** VPS systemd `algo-optionchain.service` (Restart=always, boot-enabled, re-reads token each cycle);
  unit saved to `_DEPLOY/algo-optionchain.service`. Log: `logs/option_chain_collector.log`.
- **Status:** ✅ LIVE since 2026-07-10 11:20 IST. Verified accumulating (3 snapshots in first minutes).
- **NEXT for this data:** once a few weeks accumulate → wire real premium/IV into `bs_option.py`
  (`realised_vol_map` swap-in point already flagged) and validate the Track-B strategies on it.

---

## Running comparison table (every strategy tried — even failures)

| Strategy | Best TF | Sharpe (bs\|full) | MaxDD | Trades | p-value | Net % | Verdict | Notes |
|----------|---------|-------------------|-------|--------|---------|-------|---------|-------|
| _ORB (reference, already shipped)_ | 15m | 2.37 | -1.8% | — | 0.000 | +39.2% | ✅ live | baseline bar to beat |
| **Chain-Zone (user's Pine Auto-Rev-Chain) — ATM long option** | 5m | **1.95** | −3.1% | 1806 | **0.000** ✅ | +69.1% | ✅ significant, all gates pass | 2026-07-11. User's own strategy ported: PDH/L/C+pivots+chain-H/L → pattern zone → breakout. Train 1.91 / OOS 2.04 (no decay), MC orig 2.64 ≈ median 2.66. `runs/chain_zone_longatm/`. Entry sig on 5m/7m/1m independently. Pending approval. |
| Chain-Zone — NAKED ATM sell | 5m | −0.62 | −18.2% | 1806 | 0.000 (entry) | −16.2% | ❌ structure kills the edge | Same trades; short-gamma caps winners (avg win 5398→1677 spot→prem). `runs/chain_zone_naked/`. |
| Chain-Zone — credit spread (bull-put/bear-call, wing 10) | 5m | −1.00 | −25.6% | 1806 | 0.000 (entry) | −24.5% | ❌ structure kills the edge | Same trades; winner-cap + 4-leg charges. `runs/chain_zone_credit/`. LESSON: this edge = big-winner profile → BUY options, never SELL premium on it. |
| **#05 Ratio Backspread @ Mid-day ORB** | 5m | **1.55** | −2.0% | 1124 | **0.002** ✅ | +29.9% | ✅ all gates pass | 2026-07-11. Sell 1 ATM + BUY 2 OTM (bs_off=2) — net long-gamma, defined risk, no hedge needed. tp=1.5×/sl=0.75× ATM-prem, OR=30 h0=10-13. orb_break also sig (p=0.008, robust 1.29); chain_zone-signal variant FAILED sig (robust 0.90/OOS 1.96 but p=0.378 — random-timed backspreads match it: the structure itself profits in a trending regime, timing adds nothing there; also would've correlated with #04 anyway). Fee model: 2-lot leg = 2× turnover charges. `runs/ratio_backspread/`. Corr vs chain-zone 0.21, vs ORB-family 0.41-0.60. Pending approval. |
| **Chain-Zone POSITIONAL (monthly ATM long)** | 15m | 0.97 (OOS **1.10**) | −6.9% | 717 | **0.008** ✅ | +52.1% | ⚠️ pass-with-caveat | 2026-07-11. Multi-day hold (max 3d), gap-aware stops, MONTHLY option (weekly theta bleeds), monthly RMS overlay. Winner: touch_tol=0, max_cs=60, lookback=10, ATR×1.5 stop_only. Full-window BS Sharpe 0.97 (hair under gate) but OOS/recent 1.10 ✅ (primary judge per data-regime policy), MC orig 1.34≈median 1.31, win 22% big-winner profile, spot robust 1.14/OOS 1.44. Weaker than intraday (1.95) + overnight gap risk → intraday remains the primary form. `runs/chain_zone_positional/` (build_chain_positional.py, positional_engine.py, bs.reprice_positional). VPS hub pushed (9 rows). |
| **#2 Debit Vertical @ ORB** | 15m | **1.67** | −2.1% | 1227 | **0.000** ✅ | +38.6% | ✅ significant (policy-A) | 8.5yr, vrp 1.2, wing 10, skip-expiry. STRONGEST/cleanest. Pending approval. |
| **#1 Long Straddle @ ORB** | 5m | **3.55** | −1.0% | 1625 | **0.036** ✅ | +81.7% | ✅ significant (policy-A; was 0.054) | 8.5yr, skip-expiry pushed it over the line. LIVE paper trader running. Pending approval. |
| **#3 ORB + Supertrend (ATM long-opt)** | 15m | **2.06** | −1.0% | 1024 | **0.000** ✅ | +49.7% | ✅ significant (policy-A) | 8.5yr, directional ATM CE/PE via BS. or_min=60, k=1.0, ST(14,3.0), SL 1.5×ATR, RR 2.0. `runs/orb_supertrend/`. Pending approval. |
| Long Strangle @ ORB | 5m | 4.08 | −0.8% | 1686 | 0.072 ❌ | +90.0% | ❌ failed sig gate | OTM legs = lottery-ish; timing edge not distinguishable from random at 5%. `runs/long_strangle_orb/` kept for reference (significant:false). |
| _Mid-Day ORB re-hunt (dup)_ | 15m | 1.99 | −1.4% | — | 0.000 ✅ | +38.9% | 🔁 duplicate of deployed `mid_orb_nifty` | 2026-07-10 hunt re-found it on 8.5yr+skip-expiry — good REVALIDATION of the deployed strategy, but not a new edge; run folder removed. |
| _Mean-reversion family (rsi_rev/bb_fade/sess_rev/gap_fade)_ | all | <0 | — | — | — | — | ❌ negative robust on screen | NIFTY intraday edge lives in the ORB/breakout family only (2026-07-10 screen, 15m/5m/3m). |
| Long Straddle @ mid-day lull | 15m/5m | 5.57 raw | — | ~1100 | 0.25 ❌ | — | ❌ failed sig | cheap-premium artifact — rotation test caught it |
| Short Straddle @ mid-day | 15m | −2.24 | −100% | 974 | — | −100% | ❌ dead on BS | short-vol needs real IV → Track B |
| Iron Fly @ mid-day | 15m | −4.74 | −96% | 1111 | — | −95% | ❌ dead on BS | same — Track B |
| | | | | | | | | |

---

## ⚠️ OPEN ISSUES — see `KNOWN_ISSUES.md`
- ✅ **Weekly-expiry day FIXED (2026-07-10, home machine)** — `expiry_calendar.py` filled from official
  circulars (NIFTY weekly Thu→**Tue eff 2025-09-01**; monthly same; Monday-2025 move was withheld/never
  effective; BNF weekly discontinued 2024-11). `bs_option._next_weekly_expiry` wired to it, `build_final.py`
  re-run. Only Sep-2025→present window changes (Thu→Tue). Live side (`risk_gate.is_expiry_day`) verified
  already correct. **⬜ STILL open (user call):** skip-expiry-day entry POLICY (0DTE inflation guard).
- Dashboard bugs fixed 2026-07-10: duration (bars×15→×tfMin, was 3× for 5m), Side label (long straddle
  showed "SHORT"→"LONG-VOL"). Deployed.

## Process-gap log (things I flagged, keep honest)
- 2026-07-10: BS reprice can't validate vol-arb (gamma/VIX-crush) — no independent implied-vol series.
  Those 4 moved to Track B (collector-gated). User approved "10 honest set".
- 2026-07-10: BANKNIFTY history too thin for 4.5yr backtest → NIFTY-only backtests, BNF forward-only.

## Phase log
- [ ] Phase 0 — infra: this tracker + collector built/tested/deployed
- [ ] Phase 1..10 — one per shipped strategy (design → backtest → dashboard → approve)
- [ ] Phase B — revisit Track B once collector has enough real data
