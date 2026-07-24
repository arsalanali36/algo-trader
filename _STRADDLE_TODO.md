# Auto-straddle — pending fixes + diagnostics (Arsalan, 2026-07-24 EOD)

Resume here next session. All money-path (fire path) — do carefully, PAPER-locked so no real ₹, but the fire path is shared.

## 🔧 PENDING FIXES (fire-path, careful)

| # | Issue | Root (investigated) | Fix approach | Where |
|---|-------|---------------------|--------------|-------|
| 1 | **Straddle fire 1:30+ min SLOW** ("saara price nikal jayega") | hedge-first resolves BOTH wings by WALKING up to `max_search=30` OTM strikes, fetching EACH strike's LTP one-by-one via `quote_fn` (rate-limited ~1/sec) → 30-60 serial fetches | **Batch** the candidate strikes' LTPs in ONE Dhan `/v2/marketfeed/ltp` call (like ltp_poller), OR read the option-chain collector snapshot (`_ops/option_curves`/`OptionChain/` has ALL strikes' premiums already — instant, zero extra Dhan). Pick wing from that. | `trader_dashboard._fire_auto_straddle` hedge-resolve loop (`compute_hedge_target` calls) + `_core/strategy_safety.compute_hedge_target` (the per-strike quote_fn walk) |
| 4 | **"Ek baar me ek" blocks even when position is CLOSED** | `auto_straddle.has_open()` checks ONLY the straddle-RECORD `status=="open"`. If SL closed the position but the record wasn't updated yet (or is stale), it false-blocks a new fire. Same class as TRAP #62 stale-record. | `has_open()` should ALSO verify against order_store (are the straddle's 2 SELL legs actually open?). If record says open but order_store flat → treat as not-open. | `_ops/auto_straddle.py:has_open()` (+ maybe reconcile stale record status) |
| 6 | **Quick-order straddle chart slow + NOT loading** | not yet investigated | investigate `/straddle-chart` route + `/api/straddle-chart-data` (per-leg premium fetch — likely same rate-limit slowness as #1; may need batch or option-chain snapshot) | `trader_dashboard.py` `/straddle-chart` + `/api/straddle-chart-data`, `templates/straddle_chart.html` |

## ✅ DIAGNOSED — no action (answered to user)
- **#2 "manual straddle 12:25 pe execute"** = NOT a new fire. It was the **SL-EXIT** of the BankNifty manual straddle the user fired at 10:47 (`[straddle] BANKNIFTY SL @ 795 (-61pt)` at 12:25). Auto basket-exit on combined-credit SL. `source="manual"` ONLY comes from the Quick Order button — nothing auto-creates it.
- **#3 "10:19 VRP setup pe order nahi"** = auto-straddle fires on option-ALERTS (gamma_spike/straddle_pop/crush), NOT on a chart VRP setup. At 10:19 either no qualifying alert, or a BNF straddle was already open (skip "already open"). No direct chart-VRP → order link (by design).
- **#5 "alert straddle no hedge"** = FIXED. All post-hedge-first straddles today are 4-leg (2 SELL + 2 hedge). Confirmed on alert:gamma_spike (09:42) + both manual straddles.

## Config note (VPS runtime)
`_auto_straddle`: enabled_920=True, enabled_alert=True, alert_triggers=None (→ default [pop,crush,gamma_spike]), max_per_day=None (→ default 2/symbol). That's why alert-fires log "max/day (2) reached" after 2 straddles/symbol.

## State of the day
5 straddles fired today, ALL closed by EOD (2 schedule_920, 1 alert, 2 manual). Hedge-first + basket-margin gate + one-shot marker all deployed + working (commit 498e6cc earlier today). PAPER hard-lock intact.

Related: `project_code3b_auto_straddle` memory, ADR-012 (+ addendum), `_CURVES_TODO.md` (separate curves worklist).
