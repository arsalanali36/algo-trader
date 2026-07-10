# 📕 OPTION-STRATEGY PLAYBOOK — methodology, philosophy & glossary

**Purpose:** Everything learned while building the option strategies, so ANY session (or a fresh
clone on another machine) can resume without starting from a blank slate. Read this + the tracker
`OPTION_STRATEGY_MISSION.md` first.

> Companion files: `OPTION_STRATEGY_MISSION.md` = live STATE (what's shipped, what's next).
> This file = KNOWLEDGE (why, how, terms). If they ever disagree, the tracker wins on state.

---

## 0. Resume from a fresh clone (another machine)

```bash
git clone https://github.com/arsalanali36/algo-trader.git
cd algo-trader/scratch/nifty_trend      # inside the CODE3B repo
pip install numpy pandas                # only deps the research pipeline needs
# data is committed: nifty_1min.csv (NIFTY 1-min) + bnf_1min.csv. No re-download needed.
python run_structure.py --screen        # sanity: prints the structure screen table
```
Then open `OPTION_STRATEGY_MISSION.md` → "🔴 RESUME HERE" for the current phase.

---

## 1. The mission (locked with user 2026-07-10)

Build **10 robust, thoroughly-validated option strategies**, ONE AT A TIME (design → backtest →
3-pass dashboard → user approves → next). Underlying NIFTY. Intraday only (exit 15:15, max 2/day,
no overnight). 1x, no leverage. Real Zerodha charges. Deadline-goal Monday, but RELENTLESS — keep
going until 10 ship.

**Two tracks (this is the single most important insight):**
- **Track A — buildable NOW.** Strategies whose edge is a **spot signal** expressed through options
  (long straddle at a breakout, debit spreads, directional ATM buys). The premium is modeled with
  Black-Scholes from spot + a volatility proxy. Honest, because the edge lives in the underlying's
  movement, which we have 4.5+ years of.
- **Track B — collector-gated.** Strategies whose edge is **implied-vol vs realized-vol** (short
  straddle/strangle/condor/fly, gamma scalp, VIX-crush, OI/PCR/Max-Pain). These CANNOT be honestly
  backtested yet — see §3. The live option-chain collector (`_ops/option_chain_collector.py`, running
  on the VPS since 2026-07-10) is accumulating the real OI/IV/greeks data they need. Revisit in weeks.

---

## 2. The pipeline (reuse it — don't rebuild)

```
1-min NIFTY  →  resample to TF  →  ENTRY SIGNAL  →  BACKTEST  →  3 PASSES  →  dashboard runs/<slug>/
                (5m/15m/…)         (breakout etc)    (per-trade)   (see §5)     (Pass+Period toggles)
```

**Files (scratch/nifty_trend/):**
| File | Role |
|------|------|
| `intraday_engine.py` | SINGLE-leg directional backtester + 9 spot signal designs (`DESIGN_GRID`). |
| `option_structures.py` | MULTI-leg structure backtester (straddle/strangle/condor/fly + directional debit spreads). BS-priced per bar, per-leg Zerodha charges. Has `_precomp` cache (4× faster) + `vrp_mult` premium stress. |
| `bs_option.py` | Black-Scholes pricing + `calc_charges` (real Zerodha F&O) + `realised_vol_map` (σ proxy) + weekly-expiry T + lot size. |
| `run_hunt.py` | Directional hunt → 3-pass `runs/<slug>/`. Reused helpers: `_combo_from_res`. |
| `run_structure.py` | STRUCTURE hunt (screen→optimize→significance→`write_run` 3-pass). The sibling of run_hunt for multi-leg. |
| `intraday_optimize.py` | `split()` train/OOS, optimize, significance for the directional path. |
| `montecarlo.py` | Trade-bootstrap Monte-Carlo (overfit stress). |
| `dashboard_intraday.html` | THE reusable dashboard — Pass toggle (①②③) + Period toggle (Full/Train/OOS). Every run copies it. Do NOT build new HTML. |
| `hub.html` | Auto-lists everything in `runs/index.json`. |

**To add a new structure strategy:** add legs to `option_structures.STRUCTURES` (or a directional
form to `DIRECTIONAL`), then `python run_structure.py --name <slug>`. It screens, optimizes, gates on
significance, writes the 3-pass folder, and registers in `runs/index.json`.

---

## 3. ⚠️ The honest finding that shapes everything (why some strategies are Track B)

We screened all spot signals on NIFTY 4.5yr at 1x with real charges. Result:
- **Only breakout (ORB family) has an edge.** Every mean-reversion / range signal LOSES
  (Sharpe −0.4 to −3).
- Therefore **short-premium structures cannot show a real edge on modeled premium.** A short
  straddle's whole edge is "IV > realized vol" (you sell rich premium, it decays). But we PRICE the
  premium from realized vol itself (`realised_vol_map`), so its theta earned ≈ its gamma cost **by
  construction** → edge ≈ zero minus charges. Backtests confirmed: short straddle / iron fly at the
  mid-day lull returned **−95% to −100%**. This is not a bug — it's mathematically forced. Their real
  edge needs REAL implied-vol data → **Track B (the collector)**. NEVER fabricate a chain to fake it.
- **Track-A option edge = directional / long-vol:** buy premium when the underlying is about to move
  (breakout). Long straddle @ breakout (direction-agnostic) and debit spreads (directional) both work.

**Rotation-significance catches the subtle fakes too.** A long straddle at the *mid-day lull* looked
great (Sharpe ~5.5) but FAILED the significance test (p≈0.25) — its "profit" was just the modeled
premium being too cheap, not any edge in the timing. The rotation test (§6) caught it. Ship only what
passes.

---

## 4. 📖 GLOSSARY — every term, plain

- **Train (in-sample)** — the earlier half of history (e.g. 2018–2023). The strategy is tuned here.
- **OOS (out-of-sample)** — the later half (e.g. 2024–2026) the strategy has NEVER seen. If it's
  still profitable here, the edge is real, not curve-fit. **We rank by `robust = min(train, OOS)`**
  Sharpe — whatever survives BOTH halves — never by OOS alone (ranking on OOS just curve-fits the OOS
  window; LESSONS TRAP #103).
- **Wing (short leg)** — in a spread, the far-OTM option you SELL against the ATM option you BUY.
  "Wing 10 steps" = sold 10×50 = 500 points OTM. Wider wing = more room for the move to run before the
  cap; it also partly funds the bought leg's theta decay and makes the position defined-risk + cheaper.
- **vrp / vrp_mult (Variance Risk Premium)** — in the real market, option IV is almost always HIGHER
  than realized vol, so real premium is RICHER than our BS-from-realized-vol model. `vrp_mult=1.2`
  = "make premium 20% richer" — a stress AGAINST a buyer. **We judge Track-A long-vol strategies at
  vrp=1.2 (realistic), not 1.0 (optimistic),** and sanity-check at 1.4. If the edge survives paying
  realistic premium, it's deployable-honest.
- **Sharpe** — risk-adjusted return (return ÷ volatility, annualized). Higher = smoother gains.
  NOTE: at 1 lot on ₹10L capital, positions are tiny so Sharpe looks very high (4–6) — the *shape*
  (positive, low-DD) is the signal, the absolute Sharpe number is inflated by small sizing.
- **MaxDD (max drawdown)** — worst peak-to-trough equity dip. Gate: ≤ 20%.
- **p-value / significance** — probability the result is luck. p<0.05 required. See §6.
- **Monte Carlo** — shuffle the trade order 1000× to see if the equity curve was luck of ordering.
  The real result should sit near the MEDIAN of the simulations, not the lucky top 5%.
- **3-pass** — the same strategy shown at three honesty levels (§5).

---

## 5. The 3-PASS model (every dashboard has this toggle)

- **① Instrument** — raw signal P&L, NO daily caps, NO charges. "Is there an edge at all?"
- **② + RMS** — same trades + account daily loss/profit caps (the real risk overlay).
- **③ + Black-Scholes** — real option-premium P&L + **real Zerodha per-leg charges** = the
  **deployable truth**. This is the number that matters. (For structures all three are already
  option-premium; ① and ② just drop the charges/caps.)

Combos in `results.js` are keyed `"<pass>|<period>"` (e.g. `bs|oos`). Contract: `RESULTS_SCHEMA.md`
+ `BS_OPTION_SIM.md`.

---

## 6. Validation gates (ALL must pass to ship)

| Gate | Threshold | Why |
|------|-----------|-----|
| Sharpe (bs\|full) | ≥ 1 | risk-adjusted edge exists |
| Max Drawdown | ≤ 20% | survivable |
| Trades | ≥ 100 | statistically meaningful sample |
| **Significance p** | **< 0.05** | not luck. **Rotation test:** re-run the SAME structure with entry timing shifted to random offsets 1000×; p = fraction of random-timed runs that beat the real one. If random timing beats it often → the "edge" is just "the market moves enough on average," not your signal. |
| Monte Carlo | orig near median | not lucky trade-ordering |
| vs NIFTY buy-hold | beats it, 1x | worth trading over just holding |
| **VRP stress** (long-vol) | still profitable at vrp 1.2 | survives realistic (richer) premium |

**Directional edge came from spot; structure edge must be re-tested in premium space** — never assume
a spot signal survives once theta/charges are in. That's exactly what killed the mid-day-lull straddle.

---

## 7. Strategies built so far

See the running table in `OPTION_STRATEGY_MISSION.md`. Summary as of 2026-07-10:
- **Long Straddle @ ORB breakout (5m)** — passed (vrp 1.2: Sharpe 4.54, net +59%, DD −2.3%, p=0.043,
  survives +40% stress). `runs/long_straddle_orb/`.
- **Debit Vertical (Bull-Call/Bear-Put) @ ORB (15m, wide wing)** — strong screen (robust 1.17),
  significance/dashboard being finalized. `runs/debit_vertical_orb/`.
- Rejected honestly: short straddle / iron fly (dead on modeled premium → Track B), mid-day-lull
  straddle (failed significance).

---

## 8. Data

- **NIFTY 1-min:** `nifty_1min.csv` (committed). Dhan serves 1-min from **2018** (2017 and earlier
  return nothing). Extended back to 2018 on 2026-07-10 (`_extend_dl` job on VPS) → merge script keeps
  it as one file. More history = stronger train/OOS.
- **BANKNIFTY 1-min:** `bnf_1min.csv`. Thinner; backtests are NIFTY-only.
- **Option-chain + India VIX (live, forward):** `_ops/option_chain_collector.py` → VPS systemd
  `algo-optionchain` → `_TRADING_DATA/OptionChain/<SYM>/<SYM>_YYYY-MM-DD.csv` (OI/chgOI/IV/greeks per
  strike + VIX + spot, every 1 min). This is Track B's fuel. Swap-in point in `bs_option.realised_vol_map`.

---

## 9. Cost-control note

Dhan API is free (no per-call cost). The heavy backtests run on the USER'S CPU (local), not tokens —
long backtests are cheap. Only design/analysis uses model tokens.
