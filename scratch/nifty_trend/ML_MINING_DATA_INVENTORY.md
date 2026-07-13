# Task 0a — NIFTY option-chain data inventory (ML strategy mining)

**Generated:** 2026-07-13 · tool: `ml_data_inventory.py` · snapshot: `ml_data_inventory.json`
**Master lake:** VPS `_TRADING_DATA/OptChainLake/NIFTY/` (566 MB) · **mining copy:** local, lockbox-split (see below)

## What we have

| Source | Fields | Range | Granularity | Coverage |
|---|---|---|---|---|
| Option-chain lake (Dhan paid `rollingoption`, ADR-004) | premium OHLC, volume, **IV**, **OI**, strike, spot | **2021-07-01 → 2026-07-10** | 5-min | 84 series = {WEEK, MONTH} × {CE, PE} × {ATM, ATM±1..±10}; **1246 trading days each, 0–1 missing days per series**; ~93k bars/series (~75/day) |
| Spot candles `nifty_1min.csv` | OHLCV | 2018-01-01 → 2026-07-09 | 1-min | 788k bars; local + VPS copies in sync |
| DOM order-book calibration (`dom_spread_calib.json`, ADR-005) | real ATM spread ~0.13%/leg | point-in-time measurement (2026-07) | n/a | applied as parametric cost, **not** a historical bid-ask series |

## Real vs synthetic (per field)

| Field | Status |
|---|---|
| Premium OHLC, volume, OI, strike, spot | **REAL** (exchange data via paid add-on) |
| IV | **REAL** (Dhan-computed) but see zero-IV gradient below |
| Bid-ask spread | **NOT RECORDED — synthetic.** Only the DOM-calibrated haircut exists (~0.13%/leg ATM). Any "liquidity/spread" feature is a *proxy* (volume, OI, distance-from-ATM), never real historical spread. Do not present it as real. |
| Greeks | not provided — compute from REAL IV via BS formulas (ADR-004 rule) |

## Model-bias gaps (flagged per Task 0a)

1. **Rolling-ATM semantics (TRAP #109).** Each series is "the contract that is ATM±n *right now*", re-mapped bar by bar. Fine as **state features** (what does the current chain look like). **Never** use a rolling series to P&L a held position — held-strike engine (`real_struct2`) only. Any ML target that involves holding a structure must be labeled with held-strike prices.
2. **Zero-IV gradient into deep ITM.** iv=0 rows: ATM ≈ 0.1–0.6%, but WEEK CE_ATMm10 = **43%**, PE_ATMp10 = 26%, MONTH wings 8–24%. Deep-ITM IV is unreliable → IV-based features must stay within ~ATM±5 or explicitly mask zeros (optlake_load already NaNs them). A model fed ±10 IV would silently learn the *missingness pattern*, not vol.
3. **Volume=0 gradient** on far wings (up to 27% on MONTH m10) — thin quotes; treat far-wing premium ticks with suspicion.
4. **No strike survivorship** (relative-offset download is symmetric every day) — but the chain is *truncated at ±10 offsets* (±500 pts): far-tail OI/skew is invisible to features.
5. **Spot exists from 2018, chain only from 2021-07** → any chain-feature model trains on 2021-07+ only; don't mix the spot-only era in.
6. **Bid-ask never real** (see table) — cost realism comes from the DOM haircut at backtest time, not from data.

## Lockbox (Task 0d) — enforced

- **Cutoff `LOCKBOX_START = 2026-04-15`** (last ~3 months ≈ 60 trading days, 4.5k bars/series).
- Local mining copy physically split by `ml_lockbox_split.py`: mining lake ends 2026-04-14; lockbox rows live in `_TRADING_DATA/OptChainLake_LOCKBOX/` (verify: CLEAN).
- VPS master untouched (downloader keeps appending); re-sync → re-run split.
- Spot csv is shared with the hand-designed pipeline so it is **not** physically split — mining code must call `ml_gate.trim_lockbox()` and loaders assert via `ml_gate.assert_no_lockbox()`.
- Final one-shot eval only through `ml_gate.lockbox_frame()` — every access is audit-logged to `ml_lockbox_access.log`.

## Gate calibration (Task 0b/0e result, 2026-07-13)

`ml_gate_check.py`: old gate (rotation p<0.05) vs new deflated-Sharpe gate at the
trial count a historical hunt actually burned (N=1227, cross-trial variance measured
from 404 reconstructed optimizer trials, time-weighted full-window statistic):

| Candidate | old gate | DSR prob | @0.95 bar |
|---|---|---|---|
| mid_orb_nifty (shipped) | p=0.000 pass | 0.871 | fail |
| orb_supertrend (shipped) | p=0.000 pass | 0.898 | fail |
| best-of-1000 pure noise (ann Sharpe 1.71!) | *looks shippable* | 0.344 | fail (correct) |

The gate **discriminates** (separation 0.53) and kills the exact failure mode ML mining
would create. Honest finding: the shipped ORB pair is *not provably* above a 1227-trial
search bar at 95% — consistent with the DOM-validation "marginal" verdicts. **Policy:
0.95 stays the bar for new ML-mined candidates; legacy strategies are grandfathered
(kept deployed on their existing evidence) — flag to user, their call to revisit.**
Trial correlation is real (mean pairwise ρ≈0.72, Li-Ji N_eff ratio 0.31 on a 36-trial
sample — `ml_gate.effective_trials()` available) but even N_eff-adjusted they stay <0.95.
