# ADR-008 — Capital Priority Reservation (mission vs discretionary tiers)

**Date:** 2026-07-17 · **Status:** backend done+verified; UI wiring pending · **Branch:** `feat/range-strangle-mission`

## Context

Naye "dessert" strategies (jaise IV-filtered range-strangle) SELL-side hain → **bhaari SPAN margin**
(~₹1.2-2L/lot), jabki mission ORB family LONG hai → **sasta** (~₹41k poori family). Aur dessert
**9:20 pe pehle enter** karti hai (ORB ke 11:00 signal se pehle) — toh scaling pe wo mission ka
capital **block kar sakti hai** kyunki temporally pehle aati hai. Simple "mission pehle" ordering
kaam nahi karta (dessert already andar hai jab tak mission signal aata).

1 lot pe abhi block nahi hota (sab ₹10L cap me fit), par **lots badhne pe** ye asli constraint banega,
aur ye dessert-strategy ko **live** karne ki pre-req hai.

## Decision

Per-strategy **tier** + ek **discretionary pool cap** (`risk_gate.py`):

- `strategy_tier(sid)` → `'mission'` (default) | `'discretionary'`. Config:
  `nifty_config._risk.per_strategy[<sid>].tier`. **Unmarked = mission** (safe default — nayi strategy
  galti se restrict nahi hoti).
- `discretionary_pool_cap()` → `nifty_config._risk.global.discretionary_pool_rs`. None/0/blank = **feature OFF**.
- `check_capital()` me nayi branch (per-strategy + global caps ke BAAD): agar entering strategy
  `discretionary` hai AND pool set hai → `(_tier_in_use('discretionary') + needed) ≤ pool` warna BLOCK
  (`"discretionary pool ₹X hit — mission capital protected"`). **Mission strategies is branch ko poora
  SKIP** karti hain (sirf existing caps dekhti).
- `_tier_in_use()` wahi `_group_capital` basket-costing reuse karta hai (Rule 6B) — hedged disc
  structure over-charge nahi hoti.

**Guarantee:** discretionary use kabhi `pool` se aage nahi ja sakta → mission ke liye hamesha
`(global_cap − pool)` headroom bacha rehta, chahe dessert pehle enter kare.

## Consequences

- **User-controlled (UI pending):** pool number RMS Risk tab me + per-strategy tier RMS Per-Strategy
  Override table me — user khud bharega. Default sab OFF/mission → **kuch nahi badalta jab tak set na ho.**
- **Fail-open** (exception pe allow) — existing caps ke jaisa; ek restriction hai, fail-open = "restrict
  nahi karta", consistent + surakshit.
- **Sirf nayi entries gate** — open positions untouched (check entry-time pe hota hai).
- Cross-tier netting claim NAHI (over-estimate rehta, under kabhi nahi) — `capital_in_use` ke jaisa.
- Alternative (reserve-based: mission ko fixed reserve do) ke against **pool-cap** chuna kyunki wo user
  ke "discretionary pool" mental model se seedha match karta aur simpler hai (ek number = dessert ki max limit).

## Verified

7-scenario sim (`risk_gate` monkeypatched config/positions): mission ₹2.5L+₹5L kabhi disc-pool se
block nahi; disc ₹2.5L/cumulative-over-pool block; disc under-pool OK; feature-off pe sab OK;
unmarked=mission. `py_compile` clean. **Live-verify pending UI + market-hours paper.**
