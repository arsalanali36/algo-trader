# ADR-021 — Delta Exchange (crypto) integration: second exchange, own order path, testnet-validated

**Date:** 2026-08-25
**Status:** Accepted (PAPER + testnet forward-test; real money gated on Step-3)

## Context
User has a Delta Exchange India account with algo API and does option-selling on
BTC/ETH crypto options (24/7, cash-settled, fractional 0.001-BTC lots). Goal: run
CODE3B-style option strategies (starting with a daily iron-fly) on Delta, surfaced in
the same dashboard (order page, registry, Lab) with the same "backtest → paper →
validate → live" discipline.

Two hard differences from the NSE (Dhan/Kite) world the whole codebase assumes:
1. **24/7 market** — no weekend/holiday/3:15-squareoff. `market_calendar` /
   `exit_time_config` / `execute_signal`'s trading-day gate are all NSE-specific and
   would wrongly block or mis-time a crypto strategy.
2. **Different instrument/margin/auth model** — Delta symbols (`C-BTC-<strike>-<DDMMYY>`),
   contract_value 0.001 BTC, HMAC-SHA256 signed REST, cash-settlement at 12:00 UTC.

## Decision
**Delta is a second exchange with its own isolated order path, NOT plumbed through the
NSE `smart_order`/`execution_gateway`/RMS/`order_store` stack.**

- `brokers/delta_broker.py` = `DeltaBroker` (BaseBroker-shaped, so it *could* plug into
  generic consumers) with credential-optional public data + HMAC-signed private calls +
  mainnet/testnet base-URL switch (`DELTA_TESTNET`).
- `_ops/delta_feed.py` = display-only chain/spot/iron-fly builder (mainnet, `/crypto` page).
- `_ops/delta_ironfly_trader.py` = standalone 24/7 trader with THREE execution modes:
  `sim` (internal simulation), `testnet` (real Delta testnet orders + reconcile),
  and (future) `live`. Own state store `data/delta_paper_trades.json`. It deliberately
  does **not** call `execute_signal` (would apply NSE gates/lots) and keeps its own
  entry/exit/settlement logic.
- Strategy registered as **11.01** (new family "11 Crypto (Delta)"); Lab artifact under
  `scratch/nifty_trend/runs/delta_ironfly_btc/` from the REAL Delta-premium backtest.
- `architecture_audit` `RAW_ORDER_ALLOW` includes the Delta files: the NSE RAW-ORDER
  guard (which forces `smart_order.execute()` for RMS safety) does not apply to a
  separate exchange with its own path — documented, not a silent bypass.

**Validation ladder (before real money):**
sim (no creds) → **testnet** (real Delta testnet matching engine, paper money, fills
visible on Delta's own platform + reconciled app↔testnet) → live (Step-3).

## Consequences
- **Accepted trade-off:** Delta does NOT get CODE3B's RMS/capital-gate/order_store
  netting/kill-floor. For crypto the risk control is the strategy's own defined-risk
  structure (iron-fly = bounded max loss) + reconcile + PAPER/testnet-first. If crypto
  grows, a crypto-side RMS is a later decision — we did NOT retrofit the NSE RMS onto a
  24/7 fractional-lot exchange.
- **Safety invariants:** `_testnet_broker()` returns None unless `broker.testnet` →
  the trader can NEVER place a mainnet real order (only sim or testnet) until an explicit
  `live` path is built in Step-3. Entry is unwind-on-reject (never left naked).
- **Metrics honesty:** the Lab number is REAL Delta premium + seller (trustworthy per
  RESULTS_SCHEMA, not a BS-buyer mirage), but Sharpe is red-flag-high (defined-risk +
  daily-freq) → flagged PAPER/forward-validate; net_pct = return-on-defined-risk-cycled
  (not the inflated per-trade-risk denominator), maxDD = DD₹/total-risk.
- **Credential discipline:** Claude never entered/transcribed the API key; user delivered
  it themselves (Notepad→scp), Claude only stripped stray `<>` server-side without viewing.

## Related
LESSONS TRAP #185 (Windows CMD heredoc + testnet dailies string-sort + credential-safe
delivery + RESULTS_SCHEMA required fields). Memory `project_delta_crypto_options`.
