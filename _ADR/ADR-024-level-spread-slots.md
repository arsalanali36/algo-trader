# ADR-024 — Level Spread Slots: discretionary key-level entries on the existing execution/exit infra

**Date:** 2026-09-02 · **Status:** accepted (PAPER hard-lock) · **Registry:** 03.02 `level_slot`

## Context
User trades key levels by hand: pick a level (spot or an option's premium), wait for price to
reach it, wait for a candle pattern (engulf / hammer / inside) AT the level, then enter only
when the NEXT candle breaks that candle's low (bearish) / high (bullish) — as a credit spread
(sell ATM, buy a ~0.25Δ wing). Every building block already existed in the repo
(price_triggers watch-loop idiom, Trade Manager exit rules, `wing_by_delta`, hedge-first
entry pattern, `execute_basket_exit`), but nothing joined them into a per-level "slot".

## Decision
1. **Pure state module + separate live module** (`_ops/level_slots.py` = state machine with
   zero broker/candle imports; `_ops/level_slots_live.py` = candles/spot/fire/exit-arm). Same
   split as `price_triggers` / `auto_straddle` / `strangle_live` — standalone testable
   (34 checks) and the money path is one file.
2. **Slots are day-scoped runtime on persistent config.** Config (level/zone/exit) survives;
   armed state resets every IST day. A key level is a *today* decision — an old level firing
   next morning unattended is the failure to design against, not a convenience to keep.
3. **Entry confirmation on CLOSED candles only**, default = close beyond the pattern candle's
   extreme (wick optional). Pattern must be on a candle that overlaps the zone; the break must
   be the *immediately next* candle, else the pattern resets and the level is re-watched.
4. **No new order/exit code.** Entry = `execution_gateway.execute_signal` hedge-first with a
   single whole-structure gate (`gating_status` + `affordable_lots`) and unwind-on-fail; exit =
   `position_exit_rules.set_rule` with a frozen `entry_spot` — the central monitor squares off
   (shorts first). The circled Trade Manager block in the user's screenshot IS the exit engine.
5. **BTC (Delta) = paper-only, index-level slots only**, with its own tiny exit check —
   `ltp_poller`/Trade Manager cannot price Delta legs (ADR-021 own-path rule), and Delta serves
   no per-option candle series for a premium slot.
6. **PAPER hard-lock in code** (`MODE = "paper"`), `mode` from the UI is ignored. Discretionary
   → not backtested → Rule 10: no validated number exists to protect, and none is claimed.

## Consequences
- + Any underlying the scrip master lists options for works (index, F&O stock) with no per-symbol
  code; new underlyings are a tab, not a strategy file.
- + One shared exit engine → a future Trade Manager improvement lands here for free.
- − Prem-slot candles cost one extra Dhan intraday call per armed slot per ~20s (cached);
  many armed premium slots = more candle traffic (rate-limiter gated).
- − BTC exits are wick-mode only and live in this module (a second, smaller exit engine);
  acceptable because paper, but must be folded into a Delta-aware Trade Manager before any
  real-money crypto use.
- − Going live needs: `MODE` change + user go + `strategy_registry` status flip + per-strategy
  RMS caps (currently global fallback like `manual_trigger`).
