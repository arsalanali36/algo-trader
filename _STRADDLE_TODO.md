# Auto-straddle — pending fixes + diagnostics (Arsalan, 2026-07-24)

## ✅ ALL 3 FIXED 2026-07-24 (next session: fresh session picked up handoff). Code-complete, offline-verified, audit 0 FAIL. **VPS deploy pending** (market was open — deploy post-close to avoid interrupting live pos_monitor; PAPER so no urgency). Live PAPER market-hours fire = final verify (pre-mortem shape #2: built≠verified for the batched-LTP timing).

| # | Issue | Root (confirmed) | Fix SHIPPED | Where |
|---|-------|------------------|-------------|-------|
| 1 | **Straddle fire 1:30+ min SLOW** | hedge-first resolves BOTH wings by walking up to `max_search=30` OTM strikes, fetching EACH strike's LTP one-by-one via `quote_fn`/`_lltp` (cache-miss → rate-limited REST ~1/sec) → up to 60 serial fetches | ✅ New `_prewarm_option_ltps()` — pre-resolves ALL candidate wing-strikes (CE+PE, offsets base..base+30) + ATM SELL legs via `dhan_master.get_option_contract` (cache-only, fast), fetches ALL premiums in ONE batched `/v2/marketfeed/ltp` call → `shared_ltp_cache.put_many`. The existing walk then hits cache (`max_age=20`) per strike = instant. `compute_hedge_target` strike-selection UNTOUCHED. Fail-safe: prewarm fail → cache cold → walk REST-falls-back (correctness same). | `trader_dashboard._prewarm_option_ltps` + prewarm block in `_fire_auto_straddle` before hedge loop |
| 4 | **"Ek baar me ek" blocks even when position CLOSED** | `auto_straddle.has_open()` trusts ONLY record `status=="open"`. SL/target/EOD/external close squares the legs + records exit in order_store while the record lags "open" → false-blocks a fresh straddle. TRAP #62 class. | ✅ New pure `auto_straddle.reconcile_open(symbol, leg_open_fn, log)` — self-heals a stale-open record to "closed" ONLY when order_store CONFIRMS all SELL legs flat (`broker_sync._my_open_qty==0`, injected callback keeps module pure); any >0/None-uncertain stays open (conservative). Caller: `_fire_auto_straddle` uses it in place of `has_open`. **Offline-tested 7/7** (`scratchpad/test_reconcile_open.py`). | `_ops/auto_straddle.py:reconcile_open()` + `trader_dashboard._straddle_leg_open` + gate at `_fire_auto_straddle` |
| 6 | **Quick-order straddle chart slow + NOT loading** | (a) `combined` only built when `len(maps)==2` — hedge-first made straddles **4-leg** → guard never matched → chart permanently **blank**. (b) `_load_premium_ohlc_candles` is disk-only; PAPER legs never hit the broker `/v2/orders` daemon → disk empty → blank even after (a). | ✅ (a) Sum ONLY the 2 SELL legs (the CE+PE credit that entry_credit/tp/sl track). (b) New `_leg_premium_candles()` — disk-first, **live Dhan intraday fallback** (cached 45s so the 10s poll doesn't hammer). Payoff diagram left over ALL legs (hedged shape correct). | `trader_dashboard._leg_premium_candles` + combined block in `api_straddle_chart_data` |

## ✅ DIAGNOSED — no action (answered to user)
- **#2 "manual straddle 12:25 pe execute"** = NOT a new fire. It was the **SL-EXIT** of the BankNifty manual straddle the user fired at 10:47 (`[straddle] BANKNIFTY SL @ 795 (-61pt)` at 12:25). Auto basket-exit on combined-credit SL. `source="manual"` ONLY comes from the Quick Order button — nothing auto-creates it.
- **#3 "10:19 VRP setup pe order nahi"** = auto-straddle fires on option-ALERTS (gamma_spike/straddle_pop/crush), NOT on a chart VRP setup. At 10:19 either no qualifying alert, or a BNF straddle was already open (skip "already open"). No direct chart-VRP → order link (by design).
- **#5 "alert straddle no hedge"** = FIXED. All post-hedge-first straddles today are 4-leg (2 SELL + 2 hedge). Confirmed on alert:gamma_spike (09:42) + both manual straddles.

## Config note (VPS runtime)
`_auto_straddle`: enabled_920=True, enabled_alert=True, alert_triggers=None (→ default [pop,crush,gamma_spike]), max_per_day=None (→ default 2/symbol). That's why alert-fires log "max/day (2) reached" after 2 straddles/symbol.

## State of the day
5 straddles fired today, ALL closed by EOD (2 schedule_920, 1 alert, 2 manual). Hedge-first + basket-margin gate + one-shot marker all deployed + working (commit 498e6cc earlier today). PAPER hard-lock intact.

Related: `project_code3b_auto_straddle` memory, ADR-012 (+ addendum), `_CURVES_TODO.md` (separate curves worklist).
