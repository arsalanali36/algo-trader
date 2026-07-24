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

- **Hedge wings (2026-07-23):** to cut margin (naked short straddle ~₹1.5-2L/lot), each ATM
  SELL is paired with a cheap OTM BUY wing (~max_premium ₹2) → a hedged iron fly (basket margin
  ~₹40-80k/lot, loss capped). Wings resolved via the existing `strategy_safety.compute_hedge_target`
  (walk OTM to ≤ max_premium; added `max_premium_override` so the straddle supplies its own ₹ without
  touching the RMS tab). Best-effort — a failed wing leaves the straddle standing + a loud
  `notify.warn` (paper, so a naked leg is harmless; loud so it's visible). Legs carry `side`; the
  basket-exit still monitors only the 2 SELL legs; `_close_straddle` closes each on its own side.
  Config `_auto_straddle.hedge`.

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

---

## Addendum (2026-07-24) — HEDGE-FIRST + basket-margin gate + one-shot attempt marker

**Context:** first live paper run exposed a naked-orphan storm (LESSONS TRAP #156). At the
9:20 market-open squeeze the paper pool was ~₹9.2L/₹10L; the original fire sold the ATM CE,
then the ATM PE was blocked because `check_capital` estimated each SELL leg's **standalone
naked margin** (₹1.6-1.77L) rather than the hedged basket. A blocked/aborted fire recorded
nothing, so the "fire once at 9:20" guard (which only counted RECORDED straddles) re-fired
every 3s; a CE unwind that couldn't fetch a price left an untracked naked leg.

**Decision — the fire is now HEDGE-FIRST and gated as ONE basket:**

1. Resolve ATM CE/PE **and both OTM wings up front**; if hedge is enabled and a wing can't
   resolve → **ABORT** (never sell naked). Hedge is no longer best-effort-after-the-sell.
2. Gate the WHOLE structure **once**: `risk_gate.gating_status` (RMS) + a single
   `check_capital_needed(sid, kite_basket_margin(rows), mode)` — real hedged basket margin,
   not a sum of per-leg naked estimates. No more "CE squeezes in, PE blocks".
3. **BUY both wings first**, then SELL ATM CE + PE with `gate=False` (capital already vetted
   as a basket). Never hold a naked short even momentarily.
4. Any mid-way failure → `_unwind_all` (verified, loud `notify.error`) — no untracked orphan.
5. One-shot guard armed by the ATTEMPT: `auto_straddle.mark_920(sym)` (day-scoped `fired_920`)
   is set BEFORE the fire, so a failed/partial fire can't re-arm the 3s loop. Loop also
   re-checks the entry window with a fresh per-symbol `now`.

**Consequence:** the straddle now behaves like real money — it fires as one unit if the
hedged basket fits the (unchanged, hard) capital cap, else it **cleanly skips once** with a
"capital cap" reason (no storm, no orphan, no one-sided hedge). Tighter wings (higher
`per_symbol.hedge_max_premium`) reduce the basket margin so it fits a smaller headroom;
raising the cap is a separate lever (declined — paper is treated as real). New knob:
`risk_gate.check_capital_needed` is the reusable basket-capital entry point for any future
multi-leg structure. See LESSONS TRAP #156.
