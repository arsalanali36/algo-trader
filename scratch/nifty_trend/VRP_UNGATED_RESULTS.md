# Task 6 — VRP UNGATED weekly condor/fly — real-lake backtest results

**Date:** 2026-07-20 · **Script:** `vrp_ungated_backtest.py` · **Data:** real WEEK option lake
`_TRADING_DATA/OptChainLake/NIFTY/WEEK/` (5-min held-strike premium, NOT Black-Scholes),
span **2021-07-01 → 2026-07-10**, lot 65, 1× no leverage. Real Zerodha date-aware charges
(`bs_option.calc_charges`) + ADR-005 DOM-calibrated slippage (`bs_option.slip_cost_leg`).
Engine = `positional_vol`/`real_struct2` (same real-lake infra ADR-006 measured on).

## What was tested (and why it's a distinct cell)

The untested MIDDLE case between two known results:

| | cadence | gate | hold | verdict |
|---|---|---|---|---|
| ADR-006 (gated) | weekly | IV-rank > 0.80 | to expiry | edge real (n=15, PF 4.4, p=0.0002) but **SHELVED** — ~3/yr |
| TRAP #109 (intraday) | daily | none | intraday, no overnight | **FAILED** — round-trip cost > 1 day theta |
| **Task 6 (this)** | **weekly** | **NONE** | **to expiry** | **← measured here** |

A fresh weekly ATM iron fly / condor **every cycle**, regardless of IV, held to expiry,
defined-risk wings mandatory. **One entry per weekly expiry cycle** (double-count guard
built into the engine — confirmed: n=249 cycles over the 5yr window for `cycle_start`,
n=197 for `dte4`, i.e. ~1 per weekly expiry, not more).

**Both "week-start" interpretations tested** (user request):
- `cycle_start` — enter the **first trading day of the new cycle** (day after prev expiry, max DTE)
- `dte4` — enter at **T-4 DTE (~Monday)** — the ADR-006 / live `vrp_straddle_trader` shape

Entry fixed **09:20 IST** (not random). Exits: 50%-of-credit target, ADR-006 expiry-day
ITM guard + 2:55 forced close; defined-risk wing = the loss floor (no separate % stop).

---

## 6a — Ungated headline (the result)

| week-start \| structure | n | PF | Sharpe | net % | maxDD | win % | p-value | train net | OOS net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cycle_start \| iron_fly | 249 | **0.88** | −0.42 | **−5.5%** | −6.6% | 46 | 0.82 | −5% | −0% |
| cycle_start \| iron_condor | 249 | **0.95** | −0.15 | **−2.5%** | −4.7% | 64 | 0.62 | −2% | −0% |
| dte4 \| iron_fly | 197 | **0.97** | −0.11 | **−1.1%** | −3.9% | 47 | 0.58 | −0% | −1% |
| dte4 \| iron_condor | 197 | **0.99** | −0.03 | **−0.4%** | −3.9% | 66 | 0.53 | 0% | −1% |

**Is it net-positive after real costs?** **No.** Every configuration — both week-start
timings, both structures — is **PF < 1.0 and net-negative** after real charges + slippage.
The best case (`dte4 | iron_condor`) is PF 0.99 / net −0.4% — a rounding error away from
breakeven, but still on the wrong side of zero, and it does **not** improve out-of-sample.

**Does it clear the significance bar (p < 0.05)?** **No — nowhere close.** Every config has
a bootstrap p-value of 0.53–0.82 (i.e. the mean weekly P&L is ≤ 0 in the majority of
resamples). There is no edge to be significant about.

### Side-by-side vs the gated version (same engine, apples-to-apples)

Running the identical engine with the IV-rank > 0.80 gate turned back on reproduces
ADR-006's direction — the gate is where the entire edge lives:

| | ungated (cycle_start\|condor) | **gated ≥0.80 (cycle_start\|condor)** | ADR-006 (reported) |
|---|---:|---:|---:|
| n | 249 | **12** | 15 |
| PF | 0.95 | **1.81** | 4.4 |
| Sharpe | −0.15 | **1.70** | — |
| net % | −2.5% | **+1.5%** | — |
| p-value | 0.62 | (n too small to trust) | 0.0002 |

(My gated PF 1.81 is lower than ADR-006's 4.4 because ADR-006's straddle-shape and my
condor differ, and n=12–15 is far too small for either number to be trusted on its own —
the point is only the **sign flip**: gated is net-positive, ungated is not.)

---

## 6b — IV-rank sensitivity (DIAGNOSTIC ONLY — not a new gate to ship)

Bucketing the ungated `cycle_start | iron_condor` trades by entry IV-rank shows **where**
the ungated result bleeds — and it is exactly where ADR-006 already knew it would:

| entry IV-rank | n | PF | win % | net % |
|---|---:|---:|---:|---:|
| 0.0 – 0.2 | **126** | **0.71** | 63 | **−8%** |
| 0.2 – 0.4 | 52 | 1.36 | 67 | +3% |
| 0.4 – 0.6 | 34 | 0.87 | 65 | −1% |
| 0.6 – 0.8 | 21 | 1.54 | 76 | +2% |
| 0.8 – 1.0 | 12 | 1.81 | 50 | +2% |

The gradient is up-with-IV-rank (not perfectly smooth — 0.2–0.4 is a noisy positive
pocket), but the decisive fact is the **cliff at the bottom**: **half of all cycles
(126/249) fall in the lowest IV-rank bucket, and that bucket alone loses ~8%** — it drags
the whole ungated portfolio negative. This is precisely why ADR-006 gated on IV-rank, and
precisely why removing the gate fails. *Diagnostic only — the headline result stays the
full ungated number above; this is not a threshold to retro-fit and re-ship.*

---

## 6c — Wing-width sensitivity (DIAGNOSTIC ONLY)

Swept on iron_fly (ATM straddle + wings) so wing 10 = offset ±10 fits the lake's ±10
coverage (matches the live `vrp_straddle_trader` default `wing_off=10`). A condor
(short_off=3) + wing 8/10 would need offset ±11/±13 — beyond the lake grid.

| wing (strikes) | n | PF | Sharpe | net % | max-loss / lot |
|---|---:|---:|---:|---:|---:|
| 5 | 249 | 0.88 | −0.42 | −5.5% | ₹16,250 |
| 8 | 249 | 0.92 | −0.27 | −6.2% | ₹26,000 |
| 10 | 249 | 0.92 | −0.25 | −6.9% | ₹32,500 |

Wider wings cut variance (Sharpe −0.42 → −0.25) but pay more premium for the hedge, so
**net gets slightly worse, never positive**. No wing width rescues the ungated edge — it
is not a wing-sizing problem. *Diagnostic only.*

---

## Verdict

**Ungated weekly VRP condor/fly is NOT supported by real data** — it is net-negative after
real costs (PF 0.88–0.99, all < 1.0), fails the significance bar (p 0.53–0.82) at every
week-start timing, structure, and wing width tested, and the entire edge is confined to the
top IV-rank cycles that ADR-006's gate already isolates.

The user's hypothesis — that fixed defined-risk economics make it net-positive even without
selective entry — **does not hold on this lake**: the ~57% breakeven win-rate is real
(condor win-rates are 64–66%), but the average winner is too small relative to the average
breach to overcome real costs across the many low-IV cycles where premium is thin.

Nothing wired to any live trader; no `active:true` set; `vrp_straddle_trader.py` and
ADR-006's shelved status untouched. Backtest-only, negative result — no follow-up spec.

---

## DONE LOG

- **2026-07-20** — Task 6 complete (script `vrp_ungated_backtest.py`, artifacts
  `vrp_ungated_trades.csv` / `vrp_ungated_results.json`). **NEGATIVE:** ungated weekly VRP
  condor/fly is net-negative after real costs (best PF 0.99 / net −0.4%), non-significant
  (p 0.53–0.82) across both week-start modes (cycle_start + dte4), both structures
  (fly + condor), and wings {5,8,10}. Edge lives only in high-IV-rank cycles (6b: bottom
  IV-rank bucket = 126/249 trades, net −8%) — i.e. the gate ADR-006 already applies. Gate
  reproduced on the same engine (iv_min 0.80 → PF 1.81, net-positive) confirms the sign
  flip. Diagnostics (6b/6c) exploratory only, not shipped.
