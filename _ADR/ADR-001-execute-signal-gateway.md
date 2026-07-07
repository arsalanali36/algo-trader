# ADR-001: execution_gateway — har strategy ka entry/exit EK gateway se

## Status: Decided (2026-07-07)

## Context

Teen strategy files (`_TRADERS/01_rsi_v1.py`, `range_trader.py`,
`universe_trader.py`) apna entry-sequence khud likhti thin — har jagah wahi
chain (marketable_price → strategy_safety.gate_entry → RMS default SL tags →
smart_order.execute) halke-alag versions mein. Exits mein bhi: teen alag
inline pre-exit flat-checks. Ye exactly wahi drift-recipe hai jisne TRAP #15
(hand-rolled RMS per strategy) aur TRAP #75 (5-site flat-check audit) banaya.

Saath hi Task 5 ka confirmed edge case: 2 strategies EXACT same contract
(sec_id) hold karein to system unhe alag track nahi karta —
`is_flat_fresh()` sirf broker-NET dekhta tha (strategy_id param hi nahi),
`_pending_group_close` sirf sec_id se key hota tha, aur
`broker_sync._known_broker_keys()` sirf presence-SET tha (qty compare nahi
— TRAP #58 ka untracked-scan same-contract scenario mein defeat).

## Decision

**Naya module `execution_gateway.py`** — do functions, bas:

- `execute_signal(strategy_id, symbol, side, lots, lot_size, sec_id, trad_sym,
  ...)` — entry: no-premium skip (TRAP #1) → gate_entry (fail-closed RMS) →
  default SL tags (sirf gated entries pe — hedge legs pe nahi) → smart_order.
  `gate=False` = protective leg (hedge BUY): RMS-block se naked SELL nahi banta.
- `execute_exit(...)` — fresh pre-exit flat-check (strategy-aware) →
  smart_order `is_exit=True`. `status="skipped_flat"` = order nahi gaya,
  state saaf karo (phantom-opposite-position guard).

**Task 5 gateway ke andar baked:**
1. `broker_sync.is_flat()/is_flat_fresh()` ab `strategy_id` lete hain —
   pehle order_store se "MERI qty is contract mein open hai?" (confident-0
   sirf tab jab closed round-trip evidence ho; record hi na ho to None →
   broker-level check pe fall through, kabhi jhootha "flat" nahi).
2. `_pending_group_close` key ab `"strategy:sec_id"` (pop mein old bare-key
   fallback — restart-recovered purani entries ke liye).
3. `_known_broker_keys()` ab `{key: total_qty}` dict — untracked scan broker
   qty vs known qty compare karta hai; zyada ho to "partially untracked".

Strategy files ab sirf condition + gateway call: rsi entry/exit direct
gateway; range apna file-local `place_order()` thin-wrapper rakhta hai
(callers unchanged) jo gateway ko delegate karta hai (`gate=False`, kyunki
range SELL+hedge pairing ki wajah se gate pehle khud chalata hai); universe
ke chaaron order-sites gateway pe. `01_rsi_v1.py` ka dead legacy raw
`place_order()` DELETE; dashboard ka `/api/kite-test-order` bhi ab
smart_order se (test order order_store mein — SL/EOD protected).

## Consequence

- Audit: **0 FAIL, 0 baselined** — repo mein ab koi raw broker order-call,
  inline risk-check ya duplicate indicator nahi; pre-commit hook naya aane
  hi nahi dega (baseline ratchet 0 pe).
- Nayi strategy ka order-code = 2 gateway calls. NEW_STRATEGY_CHECKLIST
  updated (Task 4).
- Behavior preservation: sequence/logic bit-for-bit wahi (tags, size-down,
  CAPITAL_BLOCKED ghost-row, gating_status short-circuit, log lines same
  shape); 15-scenario isolation test `_test_gateway_isolation.py` PASS.
- Trade-off: exits pe ab consistent fresh flat-check (5s-cache shared) —
  range_trader ke exits ko ye guard pehli baar mila (pehle sirf
  rsi/universe pe tha); ek extra order_store lookup per strategy-aware
  flat-check.
- Rollout: task-list rule ke mutabik pehle 1 din paper-mode validation,
  phir live (VPS deploy + monitoring alag decision).
