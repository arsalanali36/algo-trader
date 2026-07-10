# 🎯 OPTION-STRATEGY MISSION — 10 Robust Strategies

**Owner:** Arsalan · **Started:** 2026-07-10 (Fri) · **Deadline goal:** Mon 2026-07-13
**Rule:** RELENTLESS. Ye mission tab tak zinda hai jab tak 10 validated strategies ship na ho jayein.
Har naye session mein: neeche **"RESUME HERE"** padho, current phase se aage badho.

> This file is mission-control. It survives context resets. Update it after EVERY strategy + every phase.

---

## 🔴 RESUME HERE (single source of truth for "where are we")

- **Current phase:** Phase 0 DONE (collector live). Phase 1 = Strategy #1 NEXT.
- **Next action:** Run the Strategy-#1 quick screen (I pick the statistically-strongest premium
  structure), present its design + 3-pass dashboard mockup, PAUSE for user approval before backtest.
- **Blocked on user:** nothing right now.
- **Strategies shipped:** 0 / 10
- **Collector:** ✅ LIVE on VPS (`algo-optionchain` systemd, 1-min, NIFTY+BNF ATM±10 + India VIX).
  Data → `_TRADING_DATA/OptionChain/<SYM>/<SYM>_YYYY-MM-DD.csv`. Verified 2026-07-10 11:20 IST:
  NIFTY spot 24172 / VIX 12.48 / 21 strikes, full OI+chgOI+IV+greeks. Accumulating forward.

---

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
| | | | | | | | | |

---

## Process-gap log (things I flagged, keep honest)
- 2026-07-10: BS reprice can't validate vol-arb (gamma/VIX-crush) — no independent implied-vol series.
  Those 4 moved to Track B (collector-gated). User approved "10 honest set".
- 2026-07-10: BANKNIFTY history too thin for 4.5yr backtest → NIFTY-only backtests, BNF forward-only.

## Phase log
- [ ] Phase 0 — infra: this tracker + collector built/tested/deployed
- [ ] Phase 1..10 — one per shipped strategy (design → backtest → dashboard → approve)
- [ ] Phase B — revisit Track B once collector has enough real data
