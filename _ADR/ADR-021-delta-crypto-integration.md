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

## Update (2026-08-25) — UNIFIED INFRA: crypto IN order_store, in INR (supersedes the "Delta does NOT get order_store netting" trade-off above)

User's architecture call, LOCKED IN: **do NOT fork infra per asset class.** One
balance / order_store / RMS / reconcile system for NSE + crypto (+ future
commodity/futures), everything in INR — so all NSE hardening (netting, reconcile,
safety guards) carries over for free. Isolation, if ever needed, is a `segment`
filter later, not a separate system. This reverses this ADR's original "keep crypto
out of order_store" stance.

**Implemented:** every Delta fill mirrors into `order_store.record(...)` with
`broker="delta"`, `segment="crypto"`, `mode="paper"`, price = `premium_usd ×
contract_value × usd_inr(85)` (INR **per lot**, qty=lots → existing `(exit−entry)×qty`
gross math = correct INR P&L). Crypto now shows in Broker Orders app-list, Open
Positions (4-leg grouped, live MTM), Completed, Stats; broker dropdown auto-gains
"delta". Live MTM via a crypto branch in `/api/positions-ltp` (delta_feed marks →
INR-per-lot). Reconcile stays broker-scoped (kite/dhan) → ignores delta. Commission
(INR, ~0.03% notional) in `calcCharges`/`_zerodha_charges`; auto-liquidation recording
(`reconcile_liquidations`); Run-Up/Down (`_update_runup_tags`, match by group_id).

**MANDATORY RULE for any asset class sharing the NSE infra** (crypto/commodity/futures):
it inherits netting/reconcile/RMS for free, **but every Dhan/Kite-specific resolver
MUST have an early `broker=='delta'`/`segment=='<asset>'` skip** — scrip-master lookups
(`get_expiry_for_sec_id`/`get_lot_size_by_sec_id` = full 26MB O(n) scan on no-match),
`risk_gate.position_margin` (Kite `order_margins`/Dhan SPAN network call), the Dhan LTP
feed, and SL computation. Without the skip each does a full-scan/timeout PER RENDER —
measured 13.4s stall on `/api/orders` from `position_margin` alone (LESSONS #187).

**Liquidation reality (LESSONS #186):** on Delta **Isolated margin** an iron-fly is NOT
margin-defined-risk — a short leg going ITM is auto-liquidated ALONE at a bad price
(wings don't net it). Fix = **Portfolio Margin** (account setting; nets the hedge →
50-lot fly margin ~$0, no liquidation). Interim on isolated = small lots.

## Related
LESSONS TRAP #185 (Windows CMD heredoc + testnet dailies + credential-safe delivery),
#186 (isolated-margin leg liquidation → Portfolio Margin), #187 (crypto-legs-skip-NSE-
broker-calls perf trap). Memory `project_delta_crypto_options`.
