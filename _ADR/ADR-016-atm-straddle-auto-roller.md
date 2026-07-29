# ADR-016: Auto-Rolling ATM Straddle (strategy 02.09)

**Date:** 2026-07-29
**Status:** Accepted — shipped PAPER + disabled-by-default (NOT backtested — see Consequences)

> Note: the design was handed over as a spec titled "ADR-004: ATM Straddle Auto-Roller",
> but on-disk `ADR-004` is already the real-option-chain datalake (numbers are permanent,
> like registry IDs). So this decision is recorded under the next free number, **016**.

## Context

Ek deployed ATM short-straddle ko manually roll karna padta hai jab spot ~1 strike
(50pt NIFTY) shift ho jaaye. Manual rolling = missed rolls; par har tick pe roll =
zyada charges + whipsaw. Chahiye: roll **sirf jab zaroori ho**, ek deterministic,
anti-whipsaw, charge-aware gate ke through.

## Decision

Naya self-contained strategy `_ops/atm_straddle_roller.py` (registry **02.09**,
config_key `atm_straddle_roller`, Volatility family). Design `auto_straddle.py` ka
mirror (Rule 6B — wahi proven patterns):

1. **Pure decision + state, alag money-path** — `RollerState` (disk-persist, day-scoped)
   aur `should_roll()` me koi broker/order/Dhan import nahi → standalone-testable
   (`python _ops/atm_straddle_roller.py` = full 6-rule self-test). Orders SIRF
   `execution_gateway.execute_signal/execute_exit` se (ADR-001, Rule 6/6B) — koi raw order nahi.

2. **6-rule gate (priority order, ADR spec)** — `should_roll()`:
   1. Min strike distance (hard gate) — `abs(current_atm − deployed_atm) >= 50`
   2. Confirmation — naya ATM `N` lagataar 5-min candles (default 3) confirm ho
   3. Cooldown — aakhri roll se `>= X` min (default 30)
   4. Premium benefit — current straddle abhi bhi new ATM ka `>= 70%` hold kare to skip
   5. Time-of-day — window ke andar hi (default 09:30–15:00)
   6. Max rolls/day — hard cap (default 3)
   Koi ek fail → `(False, reason)`; sab pass → `(True, reason)`. Har candle DECISION LOG.

3. **Exit-first-then-enter roll** — `execute_roll()` PEHLE current legs buy-back
   (`ROLLER_ROLL_EXIT`), PHIR naya ATM sell (unwind-safe: doosra leg fail → pehla
   turant unwind, kabhi naked short nahi).

4. **No new polling loop** — `on_candle_close()` MAUJOODA `auto_straddle_loop`
   (`monitor_daemon`, ~3s) me hook hota hai, per fresh 5-min candle-bucket ek baar.
   Spot + premiums MAUJOODA `shared_ltp_cache` se (ltp_poller warm) — ZERO extra Dhan
   call. Bad/missing data → FREEZE (TRAP #1 shape, kabhi adhoore data pe fire nahi).

5. **Self-contained** — roller apna PEHLA straddle bhi khud kholta hai (`deploy_initial`,
   entry window me ek/din), phir roll karta hai. Position SIRF `RollerState` me track
   hoti hai (auto_straddle store me nahi) → 30/30 basket-exit se koi double-manager
   conflict nahi. Per-leg SL + 3:15 EOD `execute_signal(gate=True)` ke default RMS tags
   + `pos_monitor_loop` se milte hain (ADR spec: "reuse existing pos_monitor pattern").

6. **SL/EOD re-open guard** — `verify_still_open()` har candle order_store se confirm
   karta hai ki deployed straddle abhi bhi genuinely open hai; agar SL/EOD/manual ne
   band kar diya → `mark_flat()` (aaj dobara deploy/roll nahi). Warna roller ek band
   position ko "roll" (= re-open) kar deta, SL ke turant baad (money-path footgun).

## Consequences

- **Config `nifty_config.json["atm_straddle_roller"]`** (ADR spec ne `strategy_config.json`
  kaha tha — repo me saari config `nifty_config.json` me hai, CLAUDE.md). Default
  **`enabled: false`, `mode: "paper"`** → wiring live loop me hai par jab tak enable na
  ho, poora no-op (zero risk).
- **Rule 10 (backtest-fidelity):** is strategy ka koi 3-pass backtest number NAHI hai —
  isliye PAPER + OFF ship. Live/real-money se pehle backtest (roll rules ka edge) ya
  forward-paper validation chahiye. Enable karna = deliberate.
- **Naked ATM straddle** (ADR-004 roller = plain straddle; hedge spec me nahi). RMS
  capital-gate + default SL tags + pos_monitor protect karte hain. Hedged wings chahiye
  to alag follow-up (roll ke saath hedge bhi roll karna padega).
- **Charge estimate** (`estimate_roll_cost`) sirf LOGGING ke liye (brokerage 4 orders ×
  ₹20 + sell-side STT 0.15% + exchange) — asli economic decision Rule 4 (%-benefit) karta
  hai. (ADR text ne ₹40 likha tha par uska apna math 4×₹20 = ₹80 tha; ₹80 use kiya, configurable.)
- Exit reasons `ROLLER_*` `order_store._EXIT_REASON_PREFIXES` + app-06 badge me registered
  (Rule 9 — koi exit blank nahi).
