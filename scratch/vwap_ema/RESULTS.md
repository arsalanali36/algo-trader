# VWAP + 10 EMA Failure/Rejection strategy — backtest results

**Branch:** `feat/vwap-ema-failure` · **Date:** 2026-07-18
**Source:** user's YouTube-transcript strategy (5-min equity intraday, NSE large-caps).

## What was tested
Mechanical core of the strategy:
- 5-min bars (resampled from 1-min lake), session-reset VWAP + EMA(10).
- **SHORT** when a bar closes below VWAP & EMA10 with EMA10<VWAP (failure), EMA/VWAP close together (coil). **LONG** = mirror. Enter next-bar open (no lookahead).
- SL = signal-bar high/low. Exit = fixed R:R **or** EMA-trail. Golden period entries (09:15–11:30), force-exit 15:15, max 2–3 trades/day/symbol.
- Cost = real Zerodha **equity intraday (MIS)** round trip (`equity_charges.py`).
- Universe = 49–51 Nifty-50 large-caps ("fixed watchlist of movers"). Period Dec-2024 → Jul-2026 (373 trading days). Sizing ₹1L notional/trade.

## Results (full universe, full period)

| Config | Trades | Win% | Gross | Avg R (gross) | Net (after cost) | PF | Sharpe |
|---|---|---|---|---|---|---|---|
| Fixed RR=2 | 13,197 | 34.2% | +₹1.64L | +0.06R | **−₹9.15L** | 0.67 | −7.2 |
| EMA-trail | 13,816 | 22.6% | +₹3.59L | +0.29R | **−₹7.70L** | 0.64 | −6.4 |
| + NIFTY market-dir filter (trail) | 13,697 | 22.5% | +₹3.43L | +0.28R | **−₹7.77L** | 0.63 | −6.5 |
| A+ proxy (mkt + tight coil + max2) | 8,840 | 23.1% | +₹2.52L | +0.36R | **−₹4.70L** | 0.66 | −5.6 |

Honest gate (base runs): p=1.000, TRAIN net<0, OOS net<0. **Fails every slice.**
Completeness: **0/49 symbols net-positive** (trail); 2/49 barely positive in tight variant = noise. No pocket, no subset survives.

## Verdict: NO net-of-cost edge (mechanical form)

- The gross edge is **real but tiny** (+0.06 to +0.36R/trade). Round-trip cost (~₹82/trade) is a **large fraction of the tight 5-min-bar stop's risk** → it erases the edge. Classic "cost scales with trade-count, edge doesn't" (same shape as Crabel-ORB, LESSONS memory).
- What the data DID confirm about the video's instincts:
  - **EMA-trail doubles gross vs fixed target** (+0.29R vs +0.06R) → "let it run / trail the EMA" is directionally right; fat tails matter.
  - **Fewer + tighter setups raise per-trade edge** (+0.36R best) → the "2–3 A+ trades" discipline is directionally right.
  - Neither clears the cost hurdle mechanically.

## Honest caveats (why this is NOT the final word on the video)
1. The strategy is **discretionary**. Mechanized here: VWAP+EMA failure, coil-distance, market-direction, trade cap. **NOT mechanized:** "avoid dojis/huge candles/big gaps", "previous-day-high rejection = A+", subjective rejection reading, and above all **picking the best 2–3 of ~35 daily signals**. This test measures the *average* signal; a skilled trader cherry-picking A+ setups is a different (untested) distribution.
2. Universe grew in coverage over time (2026 dominates trade count) — magnitude, not sign.
3. Equity, not options (as the video recommends).

## Files
- `vwap_ema_failure.py` — engine (CLI: `--rr --trail --mkt --dist --maxtr --cutoff --long-only/--short-only --from/--to --out`)
- `equity_charges.py` — Zerodha equity-intraday cost
- `analyze.py` — p-value (daily bootstrap), train/OOS, monthly
- `full_*.csv` — trade logs
