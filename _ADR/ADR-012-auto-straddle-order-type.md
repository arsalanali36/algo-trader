# ADR-012 — Auto ATM straddle order type (3 entry sources, combined basket exit)

**Date:** 2026-07-23
**Status:** Accepted (PAPER-only; live requires an explicit later decision)

## Context

User wanted a short-ATM-straddle order (sell ATM CE + ATM PE) with a combined
target/SL, entered three different ways:
- **A** — automatically at 9:20 every day (NIFTY + BANKNIFTY),
- **B** — manually from the Quick Order panel,
- **C** — automatically when an option-alert fires (straddle pop/crush / gamma spike).

This is a **new money-path feature** (places real orders). It is NOT backtested — it is
a discretionary order type, so no validated Sharpe/number is claimed (Rule 10 flagged to
the user). The user explicitly asked for PAPER-first.

Three tensions to resolve:
1. **One codepath or three?** All three share the same execution (short straddle) but
   differ in *how the entry is triggered*.
2. **How is the exit defined?** "30/30 points" — per leg, or on the combined credit?
3. **Distinguish A/B/C** in Orders/Stats/RMS without violating the naming rules (ADR-007).

## Decision

- **One fire helper, three sources.** `trader_dashboard._fire_auto_straddle(symbol, lots,
  tp_pt, sl_pt, source, ...)` is the single entry path. A/B/C only differ in *who calls it*:
  the 9:20 time-check in `auto_straddle_loop`, the `/api/auto-straddle/fire` route, and the
  `on_option_alert` callback wired into `option_alerts.watch_loop(on_fire=...)`. The alerts
  module stays pure/read-only — the order decision lives in the caller's callback.

- **Combined-credit basket exit.** `entry_credit = ce_fill + pe_fill`; live `credit =
  ce_ltp + pe_ltp`; exit when `entry_credit − credit >= tp_pt` (target) or `<= −sl_pt` (SL).
  Both legs square off together. Pure decision in `_ops/auto_straddle.py.check_exit()`
  (standalone-tested; **freezes on incomplete data — a leg ≤ 0 never fires**, TRAP #1 shape).
  Per-index defaults: NIFTY 30/30, BANKNIFTY 60/60 (`per_symbol` config — BNF moves more
  per point).

- **Distinct strategy id per source** (`_straddle_strategy_id`): A=`straddle_920`,
  B=`straddle_manual`, C=`straddle_alert` — registered 02.06/07/08 (family 02 Volatility).
  Per ADR-007 the *entry idea* is the identity, and 9:20-timed vs alert-driven vs
  discretionary ARE different entry ideas (they share only the short-straddle execution).
  So separate ids is correct — Orders/Stats/RMS distinguish them + per-source P&L. The id is
  stored on the straddle and used for BOTH entry legs AND the basket exit (same id → no
  netting / flat-check mismatch).

- **Reuse everything.** Both legs via `execution_gateway.execute_signal` (RMS-gated,
  order_store-recorded, ₹0-price skip, group_id-tagged); exit via `execute_exit` (fresh
  flat-check); spot via `_trigger_spot_now`; live legs warm via `ltp_poller`; the basket
  loop lives in `monitor_daemon` next to the poller. No new order-placement code.

- **Naked-leg guard.** If leg-2 fails after leg-1 fills, leg-1 is unwound immediately (never
  leave a naked short leg). If the unwind also fails → loud `notify.error`, no phantom.

- **PAPER hard-lock.** `mode` is forced to `"paper"` in `_fire_auto_straddle` /
  `_auto_straddle_cfg` / the config route. Going live is a deliberate one-line change + user
  confirmation, not a config toggle.

## Consequence

- A/B/C are one maintained execution path — a fix to the straddle applies to all three.
- Stats can compare the three sources' P&L independently (separate ids).
- The combined-credit exit needs both legs' live LTP each cycle; if a leg's price is stale
  the basket freezes (doesn't misfire) and the 3:15 EOD squareoff in `pos_monitor` is the
  backstop (it closes the legs individually — so a straddle can exit via EOD without the
  basket loop, which is fine, but the store may still read `open` until day-rollover).
- The Orders 📊 Payoff button needs an OPEN position; once a straddle EOD-closes it shows
  "no open rows". The dedicated straddle chart's 📊 Payoff tab computes the payoff from the
  straddle's own stored strike/credit/qty, so it renders open OR closed.
- Not backtested — if this graduates to live, its numbers must be forward-validated on paper
  first (Rule 10). Registering it (02.06-08) also means per-strategy RMS caps can be set.

Related: ADR-007 (identity/naming), option-alerts (drives C), price_triggers (same
fire-via-gateway pattern). Memory: `project_code3b_auto_straddle`.
