# 📦 MODULES — auto-generated module index

> **AUTO-GENERATED — is file ko haath se mat likho.** Source of truth = code ke
> module-docstrings. Regenerate: `python _TOOLS/gen_module_docs.py` (pre-commit hook
> har commit pe chalta hai). Wiring/flow (pieces kaise judte) = `_DOCS/ARCHITECTURE.md`.
> Research/backtest engine (`scratch/nifty_trend`) = `_DOCS/BACKTEST.md` (yahan nahi).
**158 modules documented** across 10 folders.


## Folders
- [`_core`](#core) — RMS + order/execution money-path (sabse critical)
- [`_data`](#data) — Broker/data plumbing (Dhan/Kite, feed, cache, rate-limit)
- [`brokers`](#brokers) — Broker abstraction (Dhan/Kite place-order/quote/funds)
- [`_CHARTING`](#CHARTING) — Reusable indicators / zones / pattern detection
- [`strategies/signals`](#strategiessignals) — Single-source entry signals (backtest+live dono call karte)
- [`strategies/live`](#strategieslive) — LIVE trader loops (har strategy ka apna process)
- [`strategies/backtest`](#strategiesbacktest) — Pluggable backtest strategies (evaluate/backtest contract)
- [`_ops`](#ops) — Standalone ops/reporting/display-page builders (display-only, no order path)
- [`_TOOLS`](#TOOLS) — Dev tools (audit, backtest engine, doc-gen, validation)
- [`(root entrypoints)`](#root) — Entrypoints (systemd yahin se: dashboard/monitor/health)

<a id="core"></a>
## `_core` — RMS + order/execution money-path (sabse critical)

### `_core/broker_sync.py`
broker_sync.py — Ghost position detector + reconciler (TRAP #44)

- 🔧 `force_sync` — Manual trigger — bypass cooldown (for /api/sync-positions route).
- 🔧 `is_flat` — Pre-exit check in _do_squareoff: is this position already flat at broker?
- 🔧 `is_flat_fresh` — Pre-exit check with a FRESH broker positions() call (TRAP #73 family, hedge-
- 🔧 `reconcile_if_due` — Call from pos_monitor_loop every tick (mirrors the other two scans'
- 🔧 `reconcile_manual_trades` — Button-triggered (not part of the 30s auto-sync loop, deliberately —
- 🔧 `sync_if_due` — Call from pos_monitor_loop every tick.
- 🔧 `untracked_scan_if_due` — Call from pos_monitor_loop every tick (mirrors sync_if_due's cadence).

### `_core/daily_state.py`
daily_state.py — persists daily counters to disk so service restarts don't reset them.

- 🔧 `get` — …
- 🔧 `get_all` — …
- 🔧 `inc` — …
- 🔧 `reset` — …
- 🔧 `set_val` — …

### `_core/dashboard_auth.py`
dashboard_auth.py — single-password login gate for the trader dashboard.

- 🔧 `get_internal_token` — Loopback token for this app's OWN background threads to call its own HTTP
- 🔧 `get_secret_key` — Persistent Flask secret_key — generated once, reused across restarts
- 🔧 `is_configured` — True once a username+password has been set.
- 🔧 `set_credentials` — Create or overwrite the single login. Password is stored hashed.
- 🔧 `username` — …
- 🔧 `verify` — Constant-time-ish credential check.

### `_core/execution_gateway.py`
execution_gateway.py — THE single gateway every strategy calls to enter/exit. (Task 3 — see _ADR/ADR-001-execute-signal-gateway.md, CLAUDE.md Rule 6B)

- 🔧 `execute_basket_exit` — ORDERED square-off of a (possibly hedged) basket. Closes the SHORT
- 🔧 `execute_exit` — Ek EXIT leg. `reason` = exit-reason tag (e.g. "RSI_MIDLINE_EXIT",
- 🔧 `execute_signal` — Ek ENTRY leg — RMS-gated, order_store-recorded. `lots * lot_size` = qty

### `_core/exit_claim.py`
In-process idempotency guard for EXIT orders — stops two concurrent exit engines from both firing a close on the SAME (strategy, contract, side) within a few seconds.

- 🔧 `claim` — Atomically claim the right to fire an exit on (strategy, sec_id, side).
- 🔧 `release` — Release a claim — call when the close order did NOT place / failed, so a

### `_core/leg_collision.py`
leg_collision.py — keep two strategies off the SAME option contract.

- 🔧 `clear_leg` — Resolve an option leg at `offset` that is NOT in `avoid` (a set of sec_ids
- 🔧 `occupied_sec_ids` — sec_ids currently OPEN at the broker for any strategy OTHER than

### `_core/market_calendar.py`
market_calendar.py — NSE trading-day / market-open SINGLE SOURCE OF TRUTH.

- 🔧 `is_holiday` — Holiday name for that date, else None. (Does NOT count weekends — use
- 🔧 `is_market_open` — True when `now` (naive IST datetime; default = ist_now()) is a trading day
- 🔧 `is_trading_day` — True only on a normal NSE trading day: Mon–Fri AND not a listed holiday.
- 🔧 `ist_now` — Naive IST datetime (UTC+5:30). Same convention traders use.
- 🔧 `trading_days_between` — Count NSE trading days STRICTLY AFTER d0, up to and including d1 (default

### `_core/notify.py`
notify.py — CODE3B ka notification centre (single source for "kuch galat hua").

- 🔧 `clear` — History wipe. Deliberate user action only — ✕/dismiss ISKO NAHI bulata
- 🔧 `error` — …
- 🔧 `info` — …
- 🔧 `listing` — History do (nayi → purani) + unread count.
- 🔧 `mark_read` — ids=None → sab read. Warna sirf di gayi ids.
- 🔧 `push` — Ek notification record karo. Hamesha safe — kabhi raise nahi karta.
- 🔧 `resolve` — Problem khatam ho gayi → uske notifications ko read + `resolved` mark karo.
- 🔧 `warn` — …

### `_core/order_store.py`
order_store.py — persistent trade database (SQLite) for CODE3B.

- 🔧 `delete_by_source` — Saare orders delete karo jinka source == given (health_check --fire-test
- 🔧 `distinct` — Distinct values for a column (for filter dropdowns).
- 🔧 `init_db` — …
- 🔧 `ist_now_str` — …
- 🔧 `mark_externally_closed` — Mark a DB row as externally_closed (manually closed at broker / ghost position).
- 🔧 `open_legs_in_group` — Net-open legs of ONE placement group, resolved from the group's OWN ledger
- 🔧 `purge_old_blocked` — DELETE status='blocked' rows older than keep_days (IST date). Blocked rows
- 🔧 `query` — …
- 🔧 `record` — Insert one order leg. Best-effort — never raises into the caller.
- 🔧 `stats_summary` — Aggregate Profit Factor / Expectancy / Sharpe over closed trades in a
- 🔧 `trades_for` — Net entry/exit legs into completed trades + open positions for a date.
- 🔧 `trades_for_range` — Same as `trades_for()` but over an inclusive date range (for multi-day
- 🔧 `trades_for_range_chrono` — Like trades_for_range() but nets each contract CHRONOLOGICALLY across ALL
- 🔧 `update_fill` — Update a previously-recorded row's price/status/tags in place.
- 🔧 `update_tag_fields` — Atomically MERGE tag changes into an order's CURRENT DB tags (read-modify-
- 🔧 `update_tags` — Updates the tags JSON string for a specific order ID.

### `_core/payoff.py`
payoff.py — multi-leg option position payoff / zone analytics.

- 🔧 `analyse` — One call -> everything the Payoff panel needs. Never raises: any piece
- 🔧 `attach_ivs` — Derive each leg's implied vol from its live LTP (falls back to entry px).
- 🔧 `basket_margin` — Real hedged margin for the structure + the standalone per-leg sum, for
- 🔧 `build_legs` — order_store open-position rows -> leg dicts. Skips anything that isn't a
- 🔧 `exit_day_days` — Fractional days from now until TODAY's square-off time — i.e. when an
- 🔧 `expiry_of` — Earliest real expiry across the legs, from the scrip master by sec_id.
- 🔧 `parse_leg_sym` — (strike, opt_type) from a Dhan option trad_sym. Strike + CE/PE ARE
- 🔧 `payoff_expiry` — Total ₹ P&L at expiry if the underlying settles at S.
- 🔧 `payoff_today` — Total ₹ P&L if the underlying were at S right now (T years left), each leg
- 🔧 `position_greeks` — Net + per-leg Delta / Vega for a position GROUP (display-only). Per-leg IV
- 🔧 `prob_of_profit` — P(spot at expiry lands in a profit zone), lognormal terminal distribution.
- 🔧 `profit_zones` — Contiguous [a, b] spot intervals where P&L > 0. Handles ANY structure
- 🔧 `structure_tax` — Total Zerodha round-trip transaction cost (brokerage+STT+txn+SEBI+stamp+GST) to
- 🔧 `structure_tax_breakdown` — Same round-trip cost as structure_tax() but itemised (brokerage/STT/exch/GST/
- 🔧 `tte_years` — Years to expiry (expiry 15:30 IST). <=0 once expired.

### `_core/risk_gate.py`
risk_gate.py — capital allocation gate (RMS Stage 1).

- 🔧 `advance_target_sl` — One monitor-tick of the Default Target/SL profile. Pure — caller owns
- 🔧 `advance_trailing_lock` — One tick of the shared arm+gap+confirm+2-reading-peak trailing-lock
- 🔧 `affordable_lots` — SMART SIZE-DOWN — the largest lot-count in 1..max_lots whose REAL margin
- 🔧 `allow_overnight` — OPT-IN ONLY (default False) — whether THIS strategy's option positions may
- 🔧 `basket_margin_enabled` — Kill-switch for the basket (hedge-benefit) capital estimate below.
- 🔧 `broker_real_margin` — Margin estimate from the EXECUTING broker (TRAP #90's lesson: when
- 🔧 `capital_headroom` — Remaining ₹ capital for `strategy` before its own cap OR the global cap is
- 🔧 `capital_in_use` — ₹ capital currently deployed (margin-adjusted for SELL legs — see
- 🔧 `capital_mode` — 'reject' (default, Stage 1 behavior — block the whole entry) or
- 🔧 `cash_headroom` — Zerodha's cash-backed F&O-writing capacity, mirrored from the live account.
- 🔧 `cash_margin_gate_enabled` — RMS toggle for the cash-margin mirror (default ON). LIVE-only. Set
- 🔧 `check_broker_funds` — LIVE mode only — does the broker's actual available balance cover this
- 🔧 `check_capital` — Would adding qty@price (side BUY/SELL) to `strategy` breach its allocation
- 🔧 `check_capital_needed` — Cap-comparison core of check_capital(): would committing `needed` ₹ of
- 🔧 `check_capital_option` — Like check_capital() but fetches the real option premium first (capital
- 🔧 `check_concentration` — Would this entry push combined exposure to `symbol`'s underlying (across
- 🔧 `check_drawdown` — Global circuit breaker — once today's cumulative P&L (realized completed
- 🔧 `clear_manual_veto` — Remove one veto (or ALL today's if both None) — for a 'let it re-enter
- 🔧 `daily_loss_breached` — SUPREME check: True if `strategy` has lost >= its unified daily cap today
- 🔧 `daily_max_trades_hit` — True if `strategy` has already taken its max entries today (RMS cap).
- 🔧 `daily_profit_target_hit` — True if today's combined P&L (realized + unrealized) has hit the profit
- 🔧 `default_broker` — Returns the globally configured default live broker (e.g. 'dhan', 'kite').
- 🔧 `default_instrument_sl_tags` — Default stop-loss & target tags, applied automatically to EVERY NEW position at entry time.
- 🔧 `default_sl_profile` — Unified per-trade default SL/Target selector (2026-07-07 — merge of the
- 🔧 `default_target_sl_config` — Default Target/SL exit profile (2026-07-04, user-designed) — a GLOBAL,
- 🔧 `dhan_real_margin` — Real NSE-grade margin (SPAN + exposure) for ONE leg, straight from Dhan's
- 🔧 `discretionary_pool_cap` — ₹ cap on the TOTAL capital ALL discretionary-tier strategies may hold at once
- 🔧 `effective_daily_loss_cap` — The unified ₹ daily-loss cap for a strategy (always returns a positive
- 🔧 `effective_daily_profit_target` — ₹ profit target for the day. Returns None if not configured (off).
- 🔧 `effective_max_trades_per_day` — Max entries a strategy may take in one day. per_strategy overrides global;
- 🔧 `entries_today` — How many entries `strategy` has ALREADY taken today, read from order_store
- 🔧 `exit_time_config` — SINGLE SOURCE OF TRUTH for intraday square-off + no-entry-after times.
- 🔧 `expiry_auto_squareoff_enabled` — Kill-switch for the EXPIRY-day auto-squareoffs (EXPIRY_EOD 2:55 + EXPIRY_ITM)
- 🔧 `exposure_by_underlying` — ₹ margin-adjusted capital currently deployed per underlying, across ALL
- 🔧 `gating_status` — Consolidated "can this strategy take a NEW entry right now?" answer — used
- 🔧 `get_broker_balance` — {available, collateral, total_margin, ok} for 'dhan'/'kite', cached
- 🔧 `hedge_config` — Auto-hedge settings for naked option-SELL strategies (currently
- 🔧 `is_expiry_day` — True if today is the expiry date of this F&O contract.
- 🔧 `is_manual_close_vetoed` — True if the user manually closed (strategy, symbol) today → no re-entry.
- 🔧 `kill_floor_config` — Account-level trailing kill-floor settings (2026-07-02, user-requested).
- 🔧 `kill_floor_fired_today` — True if the account-level kill-floor (or aggregate trailing lock — same
- 🔧 `kill_floor_flag_path` — Today's kill-floor flag file — the ONE place this path is built.
- 🔧 `kite_basket_margin` — Real Zerodha margin for a MULTI-LEG structure via Kite's
- 🔧 `kite_real_margin` — Real Zerodha margin for ONE leg via Kite Connect's order_margins API —
- 🔧 `liquidity_filter_enabled` — ON by default everywhere — strategy_safety.check_contract_liquidity()
- 🔧 `margin_breakdown` — Display helper (payoff panel): {hedged, standalone, benefit} — hedged =
- 🔧 `mark_kill_floor_fired` — Raise today's account-level entry block. Returns True if written.
- 🔧 `mark_manual_closed` — Record that the user closed (strategy, symbol) today → block re-entry.
- 🔧 `max_hold_days` — Bounded positional hold in trading days for `strategy`, or None (= no cap,
- 🔧 `max_premium_cap_for` — Resolve the applicable per-index premium cap (₹) for an underlying root
- 🔧 `max_premium_config` — Per-index max option-premium entry cap (₹), user-requested 2026-07-07.
- 🔧 `option_is_itm` — True if a SHORT option position (SELL) is currently In-The-Money.
- 🔧 `per_instrument_lock_config` — Per-position trailing lock settings (2026-07-02) — same arm+gap+confirm
- 🔧 `position_margin` — THE canonical capital-in-use for a position / leg-group: the broker's REAL
- 🔧 `reconcile_funds` — Read-only health_check.py-style comparison: our own capital_in_use(None)
- 🔧 `shadow_live_enabled` — Diagnostic mode — when True, a PAPER entry also fires a REAL broker order
- 🔧 `sized_lots` — For capital_mode='size_down': how many of the requested `lots` (each
- 🔧 `sized_lots_option` — sized_lots() but fetches the real option premium first, like
- 🔧 `smart_size_enabled` — Is smart lot size-down ON for this strategy? Per-strategy `smart_size`
- 🔧 `strategy_tier` — 'mission' (default — sees the full global cap) or 'discretionary' (a low-priority
- 🔧 `target_sl_level` — Pure math: given the confirmed PEAK favourable MTM (₹, whole position)

### `_core/safe_mode.py`
SAFE MODE — jab broker/token bharosemand na ho to LIVE entry band, exit jaari.

- 🔧 `active` — {reason: {since, detail}} — khaali dict = system healthy.
- 🔧 `blocks_live_entry` — Gateway ke liye: (block: bool, reason_text: str).
- 🔧 `clear` — Ek reason (ya sab, reason=None) hatao. Aakhri hatte hi 'fixed' jaata hai.
- 🔧 `is_tripped` — …
- 🔧 `note_order_result` — LIVE order ka nateeja record karo. Lagatar N fail -> trip.
- 🔧 `status` — …
- 🔧 `trip` — Safe mode ON kar do is reason ke liye. Idempotent (since preserve hota).

### `_core/singleton_guard.py`
singleton_guard.py — OS-level "one process per strategy --id" lock.

- 🔧 `acquire_singleton` — Try to become the sole live process for `strategy_id`.

### `_core/skipped_store.py`
Skipped / RMS-blocked entry-signal recorder.

- 🔧 `categorize` — …
- 🔧 `daily_counts` — Per-day per-strategy per-reason skip counts (quick sanity / UI).
- 🔧 `init_db` — …
- 🔧 `query` — Read-only fetch for replay / dashboard. Returns list of dicts.
- 🔧 `record_skip` — Ek blocked entry-signal record karo. FAIL-SAFE: koi bhi error swallow +

### `_core/smart_order.py`
smart_order.py — marketable-limit execution with paper==live parity + shadow.

- 🔧 `execute` — Execute one entry/exit.
- 🔧 `marketable_price` — Return (price, src). BUY=ask*(1+buf), SELL=bid*(1-buf); fallback LTP±buf.
- 🔧 `place_hedge_if_configured` — Resolve (via strategy_safety.compute_hedge_target — the ONE place hedge

### `_core/strategy_registry.py`
strategy_registry.py — the SINGLE source of truth for strategy identity.

- 🔧 `add` — Register a NEW strategy under `family` (creating the family row is a
- 🔧 `bucket_labels` — Non-strategy order_store.strategy values (`unknown`/`default`/`manual`/'')
- 🔧 `by_config_key` — …
- 🔧 `families` — …
- 🔧 `family_of` — (family_id, family_record) for a strategy ID, else (None, None).
- 🔧 `get` — Record for an exact ID, else None.
- 🔧 `hidden_identifiers` — Set of identifiers that must never be rendered AS a strategy (lowercased).
- 🔧 `is_hidden` — True if this identifier should be kept out of strategy lists/pickers.
- 🔧 `label` — Har display surface ke liye SINGLE gate. Registered → "NN.MM - Name".
- 🔧 `load` — Load (and cache) the registry dict.
- 🔧 `next_family` — Next free family number (append-only).
- 🔧 `next_member` — Next free 'family.MM' ID (append-only — highest existing member + 1).
- 🔧 `resolve` — Return the canonical ID for anything a strategy is known by — its ID,
- 🔧 `resolve_base` — config-id (e.g. "vrp_condor_v1") → uska trader BASE key (e.g. "vrp_condor").
- 🔧 `strategies` — …
- 🔧 `tree` — Nested {family_id: {name, desc, members:[{id, ...record}]}} for display.

### `_core/strategy_safety.py`
strategy_safety.py — THE shared "backoffice" layer for every strategy that sells naked options. Every safety/RMS concern that used to be copy-pasted separately into range_trader.py / webhook_executor.py / universe_trader.py (and silently drifted apart — see LESSONS.md TRAP #15) lives here ONCE.

- 🔧 `check_contract_liquidity` — Live market-depth liquidity gate for ONE option CONTRACT (not the
- 🔧 `compute_hedge_target` — Resolve the auto-hedge BUY contract for a SELL leg that already went
- 🔧 `gate_entry` — Pre-trade RMS gate for any entry (option SELL or BUY, equity, etc.) —
- 🔧 `wing_by_delta` — Pick the protective BUY-hedge wing whose |Black-Scholes delta| is CLOSEST

### `_core/telegram_notify.py`
telegram_notify.py — trade ENTRY/EXIT ka Telegram alert (screen ghoorne se aazadi).

- 🔧 `detect_chat_ids` — getUpdates se chat_id(s) nikaalo (user ne bot ko message bheja ho to).
- 🔧 `flush` — Pending push threads ko nikalne do.
- 🔧 `get_config_masked` — UI ke liye config — token masked (kabhi poora token wire pe nahi bhejenge).
- 🔧 `is_enabled` — …
- 🔧 `notify_blocked` — RMS ne entry block ki — optional alert (default off). Fail-safe.
- 🔧 `notify_entry` — Ek entry ka Telegram alert. execution_gateway se call hota hai. Fail-safe.
- 🔧 `notify_exit` — Ek exit ka Telegram alert. Fail-safe.
- 🔧 `save_config` — UI se aaya patch merge karke likho. bot_token khaali/masked aaye to purana
- 🔧 `send_raw` — Koi bhi custom text (health-check summary etc.). Dedup lagta hai.
- 🔧 `send_test` — UI ka Test button — abhi seedha bhejo (dedup ke bina, sync so error dikhe).

### `_core/webhook_executor.py`
webhook_executor.py — TradingView webhook → Dhan/Kite order executor (MULTI-STRATEGY).

- 🔧 `handle_signal` — Process one TradingView alert. Returns {ok, msg}.
- 🔧 `ist_now` — …
- 🔧 `monitor_tick` — Called every few seconds by the dashboard daemon. Trails SL, hits
- 🔧 `release_position` — Mark a webhook position closed because something OUTSIDE this module
- 🔧 `status` — Snapshot for the UI: per-strategy meta + open positions + counters + day P&L.
- 🔧 `webhook_secret` — Shared secret token for /api/webhook/tv auth (from global block).

<a id="data"></a>
## `_data` — Broker/data plumbing (Dhan/Kite, feed, cache, rate-limit)

### `_data/dhan_feed.py`
dhan_feed.py — live bid/ask store via Dhan WebSocket Full packet.

- 🔧 `add` — Subscribe one more instrument at runtime; loop rebuilds connection cleanly.
- 🔧 `best_ask` — …
- 🔧 `best_bid` — …
- 🔧 `get_quote` — Latest WebSocket tick for sec_id, or {} if none.
- 🔧 `start` — Start the feed thread. creds={jwt_token,client_id}. sec_tuples=[(seg,sec_id),...].
- 🔧 `stop` — …

### `_data/dhan_master.py`
dhan_master.py — daily Dhan scrip-master download + option/equity contract resolver.

- 🔧 `build_cache` — …
- 🔧 `build_equity_cache` — …
- 🔧 `download_master_if_needed` — …
- 🔧 `get_equity_info` — (sec_id, seg, instrument) for an equity/index symbol from master CSV.
- 🔧 `get_expiry_for_sec_id` — Exact expiry date for a sec_id — needed because Dhan's trad_sym for
- 🔧 `get_lot_size_by_sec_id` — Lot size for an OPTION contract by its sec_id, straight from the scrip
- 🔧 `get_monthly_option_contract` — Same ATM±offset resolution as get_option_contract, but on the NEAR-MONTH
- 🔧 `get_next_monthly_option_contract` — Same ATM±offset resolution as get_monthly_option_contract, but on the
- 🔧 `get_option_contract` — …
- 🔧 `get_option_contract_ex` — Same contract resolution as get_option_contract() but ALSO returns the
- 🔧 `get_option_contract_for_expiry` — ATM±offset contract resolution on a SPECIFIC expiry. `expiry` = a full cache
- 🔧 `get_option_type_by_sec_id` — 'CE' / 'PE' for an OPTION contract by its sec_id, read from the scrip
- 🔧 `get_sec_id_for_trad_sym` — Resolve sec_id for an exact trading symbol, picking the nearest NON-expired
- 🔧 `get_trad_sym_for_sec_id` — Dhan trad_sym for an OPTION contract by its sec_id (reverse of
- 🔧 `ist_now` — …
- 🔧 `list_expiries` — Every listed option expiry for `symbol` that is still >= today, each with
- 🔧 `trading_days_to_near_monthly_expiry` — NSE trading days from TODAY up to (and including) the near-month monthly

### `_data/dhan_rate_limiter.py`
dhan_rate_limiter.py — single cross-process throttle + priority gate for EVERY Dhan API call (LTP, candles, funds/margin, orders) from EVERY process: range_trader, rsi_trader, universe_trader, webhook_executor, manual order, bulk order, dashboard LTP polling, debug routes — all of them.

- 🔧 `acquire` — Block (briefly) until a Dhan call slot is free for this priority.
- 🔧 `get_events` — Recent throttle/429 events across ALL processes, newest first.
- 🔧 `note_429` — Call this right after seeing a 429/DH-904 from Dhan. Shrinks the
- 🔧 `set_context` — Call once per symbol/operation, before any acquire()/note_429() for
- 🔧 `throttled_post` — requests.post wrapped with the priority gate + 429 feedback loop.

### `_data/fno_universe.py`
fno_universe.py — the NSE F&O stock universe (~200 liquid names), derived straight from the Dhan scrip master already on disk.

- 🔧 `build` — …
- 🔧 `extract_fno_symbols` — Return (sorted_symbols, {sym: nse_eq_sec_id}) from the scrip master.
- 🔧 `get_fno_symbols` — Cached list for other modules; rebuilds from scrip master if JSON absent.
- 🔧 `main` — …

### `_data/kite_rate_limiter.py`
kite_rate_limiter.py — cross-process throttle + priority gate for every Zerodha Kite Connect API call (orders, margins), same pattern as dhan_rate_limiter.py but with Kite's own (separate, stricter) limits and its own sqlite file — Dhan and Kite quotas are independent accounts/APIs and must never share one gate.

- 🔧 `acquire` — …
- 🔧 `note_429` — …

### `_data/ltp_poller.py`
ltp_poller.py — single batched Dhan LTP poller (worklist P7, TRAP #2 family).

- 🔧 `request_watch` — Ask the poller to include these (sec_id, segment) pairs in its batched
- 🔧 `start` — Idempotent — safe to call from multiple init paths in one process.

### `_data/opt_hist.py`
_data/opt_hist.py — shared Dhan `rollingoption` (paid Expired-Options add-on) fetcher.

- 🔧 `fetch_rolling` — One rollingoption call.
- 🔧 `held_strike_series` — Reconstruct ONE fixed contract's intraday bars on `date_str`.
- 🔧 `series_slug` — filesystem-safe offset slug for lake filenames ('ATM','ATMp1','ATMm2').
- 🔧 `strike_label` — offset int -> Dhan 'strike' param ('ATM', 'ATM+1', 'ATM-2', ...).

### `_data/shared_candle_cache.py`
shared_candle_cache.py — cross-PROCESS intraday-candle cache (file-backed), same pattern as shared_ltp_cache.py but for /v2/charts/intraday.

- 🔧 `get` — Return cached candle rows (list of dicts) for sec_id+interval+days if
- 🔧 `put` — Record freshly-fetched candle rows so other processes/loops reuse them.

### `_data/shared_ltp_cache.py`
shared_ltp_cache.py — cross-PROCESS LTP cache (file-backed, not just in-memory).

- 🔧 `get` — Return cached ltp for sec_id if fresher than max_age seconds, else None.
- 🔧 `get_after` — Like get(), but ALSO requires the cached tick to be timestamped strictly
- 🔧 `get_index` — Cached index spot for NIFTY/BANKNIFTY, read from the poller-warmed cache.
- 🔧 `get_stale` — Wider-tolerance read for last-resort fallback when a live call just failed.
- 🔧 `put` — Record a freshly-fetched ltp so other processes can reuse it.
- 🔧 `put_many` — Batch write — one file rewrite for a whole poller cycle's results

### `_data/universe.py`
universe.py — symbol universe + security-id / option routing resolvers.

- 🔧 `equity_secid` — …
- 🔧 `equity_secids` — {symbol: sec_id} for symbols that exist as NSE_EQ. Skips unknowns.
- 🔧 `index_option_atm` — ATM (+offset) option on NIFTY/BANKNIFTY. Returns (sec_id, trading_symbol).
- 🔧 `index_spot_secid` — …
- 🔧 `resolve_universe` — Return list of symbols for a universe name (or custom list).
- 🔧 `stock_option_atm` — ATM (+offset) option on a STOCK. Returns (sec_id, trading_symbol).

<a id="brokers"></a>
## `brokers` — Broker abstraction (Dhan/Kite place-order/quote/funds)

### `brokers/base_broker.py`
base_broker.py — abstract broker interface.

- 📦 `BaseBroker` — …

### `brokers/delta_broker.py`
delta_broker.py — Delta Exchange India broker plugin (crypto options/futures).

- 📦 `DeltaBroker` — Delta Exchange India broker. Mirrors BaseBroker (duck-typed).

### `brokers/dhan_broker.py`
dhan_broker.py — Dhan implementation of BaseBroker.

- 📦 `DhanBroker` — …

### `brokers/kite_broker.py`
kite_broker.py — Zerodha Kite Connect order placement

- 🔧 `dhan_sym_to_kite` — Convert Dhan trading symbol to Kite format (STRING-GUESS fallback only —
- 🔧 `exchange_request_token` — request_token → access_token exchange karo aur config.json mein save karo.
- 🔧 `get_ltp` — Live LTP fetch karo.
- 🔧 `get_positions` — Zerodha pe open positions fetch karo (intraday MIS).
- 📦 `KiteBroker` — …
- 🔧 `place_order` — Kite Connect pe order place karo.
- 🔧 `resolve_dhan_from_kite_symbol` — Reverse of resolve_kite_symbol() — Kite tradingsymbol -> Dhan
- 🔧 `resolve_kite_symbol` — Exact Dhan-trad_sym -> Kite-tradingsymbol resolution via Kite's

<a id="CHARTING"></a>
## `_CHARTING` — Reusable indicators / zones / pattern detection

### `_CHARTING/indicators.py`
_CHARTING/indicators.py — SINGLE SOURCE OF TRUTH for indicator calculations.

- 🔧 `compute_indicator` — Returns a pandas Series aligned to df's index. Raises KeyError if name unknown.
- 🔧 `indicator_series_to_points` — pandas Series -> [{"time": unix_seconds, "value": float}, ...] for the chart, skipping NaN.
- 🔧 `list_available_indicators` — Name + param schema for the dashboard's 'Add Indicator' dropdown.
- 🔧 `pine_ema` — Pine ta.ema match — span-style alpha = 2/(period+1), adjust=False.
- 🔧 `wilder_atr` — Wilder ATR — Pine ta.atr uses Wilder's RMA (alpha = 1/period), NOT EMA span.
- 🔧 `wilder_rsi` — Wilder RSI — Pine ke ta.rsi() se exactly match karta hai.

### `_CHARTING/patterns.py`
_CHARTING/patterns.py — Candle pattern detection (hammer/engulfing/harami).

- 🔧 `bear_engulfing` — …
- 🔧 `bear_harami` — …
- 🔧 `bull_engulfing` — …
- 🔧 `bull_harami` — …
- 🔧 `detect_pattern_tags` — Walk df once, tag every bar that matches any known candle pattern.
- 🔧 `green_hammer` — …
- 🔧 `inv_red_hammer` — …
- 🔧 `is_bearish_pattern` — …
- 🔧 `is_bullish_pattern` — …
- 🔧 `red_hammer` — …

### `_CHARTING/plot_spec.py`
_CHARTING/plot_spec.py — Pure JSON shaper for the dashboard chart.

- 🔧 `build_plot_spec` — indicators: list of {"name": str, "series": pd.Series, "type": "line"|"histogram", "color": str}

### `_CHARTING/spec.py`
_(no module docstring — add ek 1-line role add karo)_

### `_CHARTING/zones.py`
_CHARTING/zones.py — Pivot/key-level building + chart-renderable zone shapes.

- 🔧 `build_key_levels` — Build all key levels: pivot + prev-day HLC + high/low chain.
- 🔧 `levels_to_chart_zones` — Turn build_key_levels()'s (price, level_type) tuples into renderable
- 🔧 `traditional_pivots` — Traditional pivot points from prev day H/L/C.
- 🔧 `zone_box_from_state` — Turn one active GREEN/RED zone (as tracked by run_signal_engine's state

<a id="strategiessignals"></a>
## `strategies/signals` — Single-source entry signals (backtest+live dono call karte)

### `strategies/signals/chain_zone.py`
SINGLE SOURCE — Chain-Zone signal (user's "Ars_Auto_Rev_Chain").

- 🔧 `candle_patterns` — Vectorised bullish/bearish key-candle flags (engulf/hammer/harami), Pine parity.
- 🔧 `chain_zone_signal_last` — Point-in-time chain-zone for the LIVE trader — signal for the last CLOSED bar.
- 🔧 `chain_zone_signals` — Vectorised chain-zone long/short entry arrays — EXACT intraday_engine logic.
- 🔧 `daily_levels` — date -> dict(res, sup, neutral). Levels from the PREVIOUS completed day (no lookahead).

### `strategies/signals/m_pattern.py`
m_pattern.py — SINGLE SOURCE of the "IV-pop -> M-rollover" signal (ADR-010 / Rule 6E).

- 🔧 `detect` — First M-rollover of `series`. Returns (x_rollover, spike_ratio) or None.

### `strategies/signals/orb.py`
SINGLE SOURCE OF TRUTH — ORB (opening-range breakout) signal.

- 🔧 `orb_signal_last` — Point-in-time helper for the LIVE trader. Given a continuous multi-day df, return
- 🔧 `orb_signals` — Vectorised ORB long/short over a (possibly multi-day) bar series. ONE function for
- 🔧 `orb_st_signal_last` — Point-in-time orb_st for the LIVE trader — signal for the last CLOSED bar.
- 🔧 `orb_st_signals` — ORB breakout CONFIRMED by Supertrend direction (design 'orb_st'). No time window —
- 🔧 `st_dir` — Supertrend DIRECTION (+1/-1) — exact copy of the backtest intraday_engine.supertrend
- 🔧 `wilder_atr` — Wilder RMA of True Range — identical to engine.atr / _CHARTING.wilder_atr.

<a id="strategieslive"></a>
## `strategies/live` — LIVE trader loops (har strategy ka apna process)

### `strategies/live/01_rsi_v1.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `close_position` — Open option position band karo (EXIT order).
- 🔧 `dhan_headers` — …
- 🔧 `fetch_candles` — Symbol name → Dhan intraday OHLC DataFrame (aaj ka din)
- 🔧 `get_signal` — Ek symbol ke candles dekhkar batao: kya karna hai?
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — No new entry at/after RMS no_entry_after — stops '3:15 ke baad entry ->
- 🔧 `ist_now` — UTC → IST (UTC+5:30). Hamesha IST use karo.
- 🔧 `load_config` — nifty_config.json se yeh block padho:
- 🔧 `load_creds` — …
- 🔧 `run` — …

### `strategies/live/02_debit_vertical_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_breakout` — Directional ORB breakout on the last CLOSED bar. Returns dict(direction, spot, atr)
- 🔧 `fetch_nifty` — Continuous `tf_min` NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85).
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/03_orbst_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_signal` — ORB breakout CONFIRMED by Supertrend direction, on the last CLOSED bar.
- 🔧 `fetch_nifty` — Continuous `tf_min` NIFTY spot bars for the last `days` (ATR+ST warm-up, TRAP #85).
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/04_chainzone_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_signal` — Emit a signal only if the LAST CLOSED bar fired the chain-zone breakout.
- 🔧 `fetch_nifty` — Continuous `tf_min` NIFTY spot bars for the last `days` (chain needs prior
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/05_backspread_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_breakout` — Mid-day ORB breakout on the last CLOSED bar — matches intraday_engine 'tod_orb'
- 🔧 `fetch_nifty` — Continuous `tf_min` NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85).
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/06_shortvol_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `fetch_spot` — last NIFTY spot via index LTP (marketfeed) — cheap, no candle needed for a time-entry.
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/07_banknifty_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_signal` — Mid-Day ORB breakout on the last CLOSED bar. OR cutoff `<= or_end` (backtest parity,
- 🔧 `fetch_bnf_15m` — Continuous 15m BANKNIFTY spot bars (ATR warm-up, TRAP #85).
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/_test_crasher.py`
_(no module docstring — add ek 1-line role add karo)_

### `strategies/live/bnf_strangle_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `is_market_open` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/dist_ma_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `ist_now` — …
- 🔧 `load_book` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `process_day` — Run one completed trading day's decisions. Mutates `book`. `place=False`
- 🔧 `run` — …
- 🔧 `save_book` — …

### `strategies/live/nifty_ema_trader.py`
nifty_ema_trader.py — 9/20 EMA Crossover | Multi-Symbol | 1-min

- 🔧 `compute_signal` — …
- 🔧 `fetch_candles` — …
- 🔧 `hdrs` — …
- 🔧 `is_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — No new entry at/after RMS no_entry_after — stops '3:15 ke baad entry ->
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `paper_trade` — …
- 🔧 `place_order` — …
- 🔧 `run` — …

### `strategies/live/orb_overnight_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_signal` — ORB breakout on TODAY's opening range + mid-day window. Returns dict with
- 🔧 `fetch_nifty_15m` — Continuous 15m NIFTY spot bars (ATR warm-up, TRAP #85).
- 🔧 `is_market_open` — …
- 🔧 `is_nextday_exit_time` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/orb_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_signal` — Return dict(signal='long'/'short', entry_spot, atr, stop, target) or None.
- 🔧 `fetch_nifty_15m` — Continuous 15m NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85).
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/range_trader.py`
range_trader.py — Ars_Auto_Rev_Chain RANGE Strategy | 1-min

- 🔧 `fetch_1m` — Fetch intraday candles from Dhan /v2/charts/intraday.
- 🔧 `fetch_daily` — Fetch daily OHLC from Dhan /v2/charts/historical.
- 🔧 `get_state` — …
- 🔧 `hdrs` — …
- 🔧 `is_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — No new entry at/after RMS no_entry_after time — stops the '3:15 ke baad
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `main` — …
- 🔧 `place_order` — Thin file-local wrapper — asli kaam ab execution_gateway mein (Task 3,
- 🔧 `reset_daily_state` — …
- 🔧 `run_signal_engine` — Bar-by-bar zone detection + entry/exit. THE engine — live and backtest.

### `strategies/live/straddle_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_breakout` — Direction-agnostic ORB breakout on the last CLOSED bar.
- 🔧 `fetch_nifty` — Continuous `tf_min` NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85).
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/strangle_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `compute_breakout` — Direction-agnostic ORB breakout on the last CLOSED bar, gated to the
- 🔧 `fetch_nifty` — Continuous `tf_min` NIFTY spot bars for the last `days` (ATR warm-up, TRAP #85).
- 🔧 `is_force_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/universe_trader.py`
universe_trader.py — best-in-class universe scanner engine.

- 🔧 `get_state` — …
- 🔧 `is_exit_time` — …
- 🔧 `is_market_open` — …
- 🔧 `is_no_entry_time` — No new entry at/after RMS no_entry_after — stops '3:15 ke baad entry ->
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `log` — …
- 🔧 `main` — …
- 🔧 `n_open` — …
- 🔧 `order_side_for` — For equity we go long/short directly. For options we BUY the premium
- 🔧 `reset_daily` — …
- 🔧 `resolve_route` — Return (sec_id, seg, trad_sym) for where the order goes, per cfg['route'].

### `strategies/live/vrp_condor_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `fetch_spot` — …
- 🔧 `is_market_open` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/vrp_condor_weekly_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `fetch_spot` — …
- 🔧 `is_market_open` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

### `strategies/live/vrp_signal.py`
VRP panic-fade ENTRY SIGNAL — IV-rank of NIFTY (see _ADR/ADR-006).

- 🔧 `atm_iv_from_premiums` — mean of BS-inverted CE/PE ATM IV (percent). None unless BOTH legs invert to a
- 🔧 `implied_vol_pct` — Annualised IV in PERCENT implied by a market premium, or None if the price
- 🔧 `iv_rank` — rank (0..1) of today_iv vs the trailing `lookback` values in history_iv.
- 🔧 `load_history` — …
- 🔧 `rank_for` — convenience: (eligible, rank) for a given day using strictly-prior history.
- 🔧 `record_today` — append/update today's IV; returns the updated dict (does NOT auto-save).
- 🔧 `save_history` — …
- 🔧 `seed_from_lake` — (re)build the IV history from the lake's ATM IV. merge=True keeps any newer
- 🔧 `should_enter` — …

### `strategies/live/vrp_straddle_trader.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `fetch_spot` — …
- 🔧 `is_market_open` — …
- 🔧 `ist_now` — …
- 🔧 `load_config` — …
- 🔧 `load_creds` — …
- 🔧 `load_state` — …
- 🔧 `run` — …
- 🔧 `save_state` — …

<a id="strategiesbacktest"></a>
## `strategies/backtest` — Pluggable backtest strategies (evaluate/backtest contract)

### `strategies/backtest/always_buy.py`
always_buy.py — TEST strategy: BUY when flat. Used to exercise the engine's routing + order + caps path without waiting for a real crossover. Not for live.

- 🔧 `evaluate` — …

### `strategies/backtest/bb_reversion.py`
bb_reversion.py — Bollinger Bands Mean Reversion Strategy.

- 🔧 `evaluate` — …

### `strategies/backtest/rsi_v1.py`
rsi_v1.py — RSI Simple Strategy (scratch build, 2026-06-19)

- 🔧 `evaluate` — …

### `strategies/backtest/sample_ema.py`
sample_ema.py — scaffold strategy: EMA fast/slow crossover.

- 🔧 `evaluate` — …

### `strategies/backtest/user_bb1_v1.py`
Bollinger Band Bounce Strategy - Python Implementation - Buy when price bounces from lower band (closes above lower band after being below/at) - Exit when price touches upper band - Intraday only: forced exit at 15:15 IST - Bollinger Bands in Pink Color

- 🔧 `backtest` — Full backtest with pink Bollinger Bands.
- 🔧 `evaluate` — Bollinger Band bounce strategy with pink bands.

### `strategies/backtest/user_bb2_v1.py`
Bollinger Band Bounce Strategy - Buy when price bounces from lower band (closes above lower band after being below/at) - Exit when price touches upper band - Intraday only: forced exit at 15:15 IST - Bollinger Bands in thick white color

- 🔧 `backtest` — Full backtest implementation with thick white Bollinger Bands.
- 🔧 `evaluate` — Simple Bollinger Band bounce strategy with thick white bands.

### `strategies/backtest/user_bollinger_band_bounce_strategy_v1.py`
Bollinger Band Bounce Strategy - Buy when price bounces from lower band (closes above lower band after being below/at) - Exit when price touches upper band - Intraday only: forced exit at 15:15 IST

- 🔧 `backtest` — Full backtest implementation with trade tracking.
- 🔧 `evaluate` — Simple Bollinger Band bounce strategy.

### `strategies/backtest/user_temp_52_week_gemni_v1.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `backtest` — …

### `strategies/backtest/user_temp_52_week_gemni_v2.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `backtest` — …

### `strategies/backtest/vwap_ema_failure.py`
vwap_ema_failure.py — VWAP-EMA Failure Reversal Strategy (Mukul's strategy)

- 🔧 `backtest` — df: columns time/open/high/low/close/volume, sorted, possibly multi-day.

<a id="ops"></a>
## `_ops` — Standalone ops/reporting/display-page builders (display-only, no order path)

### `_ops/atm_straddle_roller.py`
atm_straddle_roller.py — ATM straddle AUTO-ROLLER (see _ADR/ADR-004 roller spec).

- 🔧 `deploy_initial` — Sell ATM CE + PE (fresh short straddle) — roller ka initial deploy. Legs
- 🔧 `estimate_roll_cost` — Ek roll ka approx cost (₹): brokerage (4 orders) + sell-side STT (naya straddle)
- 🔧 `execute_roll` — Deployed straddle ko new ATM pe ROLL karo. ADR: exit PEHLE, phir enter.
- 🔧 `load_config` — nifty_config['atm_straddle_roller'] defaults ke upar. mode default 'paper'.
- 🔧 `on_candle_close` — Har 5-min candle-close pe call ho (NO apna loop — existing cycle me hook karo,
- 📦 `RollerState` — Ek symbol ke deployed straddle + roll-bookkeeping ka disk-persisted state.
- 🔧 `should_roll` — Kya abhi roll karna chahiye? 6 rules PRIORITY ORDER (ADR-004) me — pehla jo
- 🔧 `verify_still_open` — True agar deployed straddle ke SELL legs abhi bhi genuinely open hain.

### `_ops/auto_data_downloader.py`
auto_data_downloader.py — VPS daemon that auto-downloads OHLC bars for every traded instrument.

- 🔧 `download_bars` — Download 1-min OHLC bars for one instrument on one date. Returns True on success.
- 🔧 `fetch_orders` — Fetch all orders from Dhan. Returns list of dicts with filled orders only.
- 🔧 `gap_check` — Dashboard banner alerts for GENUINELY-ACTIONABLE missing premium-chart data only.
- 🔧 `is_market_hours` — …
- 🔧 `main` — …
- 🔧 `process_order_store_trades` — Capture full-day 1-min premium bars for EVERY option contract traded today
- 🔧 `process_orders` — Download bars for all filled orders. Returns False if token error encountered.
- 🔧 `run_once` — One full cycle: fetch orders → download bars → gap check → update alerts.

### `_ops/auto_straddle.py`
auto_straddle.py — Auto ATM straddle (SHORT) order state + basket-exit decision.

- 🔧 `add` — Append a straddle row (caller has already placed both legs). Returns the
- 🔧 `cancel_all` — …
- 🔧 `check_exit` — Pure basket-exit decision for a SHORT straddle.
- 🔧 `check_exit_net` — Generic basket-exit for a flexible structure (any legs, any sides).
- 🔧 `count_today` — How many straddles fired today for `symbol` (optionally only a given source
- 🔧 `fired_920_today` — Restart-safe one-shot guard for the 9:20 scheduled fire. TRUE if we've
- 🔧 `fired_alert_today` — TRUE if an alert-driven straddle already ENTERED today for this symbol —
- 🔧 `get` — …
- 🔧 `has_open` — …
- 🔧 `list_open` — …
- 🔧 `list_today` — …
- 🔧 `mark_920` — Record a 9:20 attempt marker for `symbol` (idempotent). Call this right
- 🔧 `mark_alert` — Mark that an alert straddle ENTERED today for `symbol` (idempotent). Call
- 🔧 `net_credit` — NET premium of an ARBITRARY multi-leg structure:
- 🔧 `reconcile_open` — has_open(symbol), but SELF-HEALS a stale-open record before answering.
- 🔧 `set_status` — …

### `_ops/auto_strangle_roll.py`
auto_strangle_roll.py — POSITIONAL hedged short-strangle with roll-away + IV-gate. PURE state + decision (no broker / order / Dhan import) — standalone-testable, mirrors auto_straddle.py. Firing legs (via execution_gateway), live LTP, live IV and weekly-expiry squareoff are the CALLER's job (trader_dashboard).

- 🔧 `add` — …
- 🔧 `apply_roll` — Close side's open legs at close_prices{leg_key:price}, append new_legs (SELL+HEDGE).
- 🔧 `build_position` — `legs`: list of {opt_type,role,side,sec_id,trad_sym,strike,entry_price,qty}
- 🔧 `check_exit` — (reason|None, mtm|None). Target only — loss is managed by roll+hedge cap.
- 🔧 `entry_allowed` — True only if today's trailing IV rank clears the gate. None rank → block
- 🔧 `entry_spec` — All 4 legs' strikes for a fresh entry. Caller resolves sec_id/trad_sym/price.
- 🔧 `get` — …
- 🔧 `has_open` — …
- 🔧 `list_open` — …
- 🔧 `position_mtm` — Running P&L in points if we flatten NOW. ltp_of(leg)->float|None.
- 🔧 `rolls_needed` — Sides whose OPEN sold leg is within `trig` of spot (threatened). Threatened-only
- 🔧 `set_status` — …
- 🔧 `side_strikes` — (sold_strike, hedge_strike) for one side at `dist` from spot, hedge `wing` beyond.
- 🔧 `sold_leg` — …
- 🔧 `update` — mut(pos)->pos ; atomic read-modify-write.

### `_ops/backfill_trade_ohlc.py`
_ops/backfill_trade_ohlc.py — fill missing per-trade premium OHLC from Dhan's paid Expired-Options add-on (rollingoption), so the Stats-page "Opt Fixed/Aggr/ Aggr->EOD" what-if columns stop showing '-' for old dates.

- 🔧 `main` — …
- 🔧 `parse_trad_sym` — 'NIFTY-24Jun2026-24050-PE' / 'BAJAJ-AUTO-26Jun2026-9000-CE' ->

### `_ops/backtest_calendar.py`
backtest_calendar.py — surface backtest run results in the Stats calendar.

- 🔧 `calendar_summary` — `{summary, trades, filters, metrics, meta}` — same shape as the live
- 🔧 `combined_summary` — Portfolio-style COMBINE of multiple backtest runs. Unions each run's
- 🔧 `list_runs` — All available backtest runs from runs/index.json, newest-config first.

### `_ops/backtest_lab.py`
Multi-day options backtest engine — powers /backtest-lab (StockMock-style).

- 🔧 `intraday` — Single-day minute-by-minute combined MTM + spot (for the per-day PnL modal).
- 🔧 `lot_for` — …
- 🔧 `run` — Full multi-day backtest. Returns summary + per-day + breakups + equity + trade log.

### `_ops/backtest_live_recon.py`
BACKTEST vs LIVE reconciliation — how much does each deployed strategy's LIVE/paper result actually agree with its BACKTEST run, day by day?

- 🔧 `main` — …
- 🔧 `reconcile` — …

### `_ops/basket_notes.py`
basket_notes.py — user's own comment/note per option BASKET (pair) in Completed Trades.

- 🔧 `all_notes` — {key: {text, ts}} — every saved basket note.
- 🔧 `set_note` — Save/replace/clear one basket's note. Blank text deletes it. ts is an

### `_ops/broker_ledger.py`
broker_ledger.py — balance-over-time (ledger) store for the RMS Broker Balances panel. DISPLAY-ONLY (no order / risk / trading path).

- 🔧 `import_ledger` — Public: import a CSV ledger.
- 🔧 `import_ledger_xlsx` — Public: import an XLSX ledger (Zerodha/Dhan native download).
- 🔧 `parse_ledger_csv` — Tolerant broker-ledger CSV parser → _rows_to_ledger.
- 🔧 `parse_ledger_xlsx` — Zerodha/Dhan ledger XLSX (the native download format) → _rows_to_ledger.
- 🔧 `snapshot` — Record today's live balance for both brokers (one row/day/broker; a repeat
- 🔧 `snapshot_if_due` — Take a snapshot only if today's isn't recorded yet (once/day). Cheap guard
- 🔧 `view` — Combined payload for the RMS ledger panel — per broker: balance-over-time

### `_ops/broker_orders.py`
broker_orders.py — DISPLAY-ONLY broker order/trade book (Zerodha) + CSV match.

- 🔧 `csv_match` — Uploaded Zerodha tradebook CSV ko LIVE broker trades se per-contract MATCH.
- 🔧 `fetch` — LIVE (today's) broker order book + trade book + app-blocked entries,
- 🔧 `fetch_app` — App ke APNE order records (order_store) — PAPER + REAL, kisi bhi date ke.

### `_ops/bs_shadow.py`
bs_shadow.py — DAILY Black-Scholes shadow of the REAL paper/live trades.

- 🔧 `build_day` — …
- 🔧 `expiry_dt` — …
- 🔧 `main` — …
- 🔧 `sigma_for` — …
- 🔧 `spot_series` — Return {'YYYY-MM-DD HH:MM': close} for the index, cached to disk.

### `_ops/capture_vrp_condor.py`
DRY-RUN capture of the VRP Overnight Condor's intended entry.

- 🔧 `main` — …

### `_ops/chain_pcr.py`
chain_pcr.py — NIFTY daily option-chain KPIs (PCR + max-pain) from NSE FO bhavcopy.

- 🔧 `backfill` — …
- 🔧 `backfill_parallel` — …
- 🔧 `compute_day` — Return summary dict for date d, or None.
- 🔧 `daily` — …
- 🔧 `main` — …
- 🔧 `new_session` — …

### `_ops/config_drift_check.py`
config_drift_check.py — deployed live config == validated backtest params?

- 🔧 `check` — -> list of per-strategy dicts {config_key, slug, mismatches[], info[]}.
- 🔧 `main` — …

### `_ops/daily_report.py`
daily_report.py — one-scroll EOD "Daily Report" data builder. DISPLAY-ONLY.

- 🔧 `available_dates` — Sorted list of dates (YYYY-MM-DD) that actually have completed-trade data,
- 🔧 `build` — …
- 🔧 `get_settings` — Report settings for the ⚙ modal — capital (for net %) + per-strategy
- 🔧 `save_settings` — …

### `_ops/delta_feed.py`
delta_feed.py — Delta Exchange India crypto data feed for the /crypto page.

- 🔧 `chain` — Option chain for one expiry: ATM +/- n strikes, CE & PE with live data.
- 🔧 `expiries` — Distinct expiry dates (from live option symbols), soonest first.
- 🔧 `ironfly_setup` — Validated daily Iron-Fly: SELL ATM CE+PE, BUY OTM wings (defined risk).
- 🔧 `spot` — Live underlying spot (perpetual mark).

### `_ops/delta_ironfly_trader.py`
delta_ironfly_trader.py — Delta Exchange India daily BTC Iron-Fly (PAPER, forward-test).

- 🔧 `enter` — …
- 🔧 `enter_testnet` — …
- 🔧 `live_mtm` — Live mark-to-market P&L of the open iron-fly using CURRENT option marks
- 🔧 `maybe_exit` — …
- 🔧 `maybe_exit_testnet` — …
- 🔧 `position_pnl` — Net P&L in points (per-BTC) at given spot (settlement or live).
- 🔧 `reconcile_liquidations` — Detect legs Delta auto-liquidated (in our store, GONE from the broker) and
- 🔧 `run_loop` — …
- 🔧 `settle_value` — Cash-settlement intrinsic per-BTC for one leg.
- 🔧 `should_enter` — True if it's the entry window, feature on, and no open position today.
- 🔧 `tick` — …

### `_ops/delta_testnet_check.py`
delta_testnet_check.py — validate Delta India TESTNET auth + order plumbing.

- 🔧 `main` — …

### `_ops/deploy_vps.py`
deploy_vps.py — CODE3B ko VPS pe push karo (tarball via SCP, ek command me)

- 🔧 `collect_files` — …
- 🔧 `main` — …
- 🔧 `run` — …

### `_ops/dist_ma_daily_update.py`
dist_ma_daily_update.py — keep the daily-equity lake fresh for dist_ma_trader.

- 🔧 `creds` — …
- 🔧 `fetch_daily` — Recent daily OHLCV from Dhan /v2/charts/historical (same call as
- 🔧 `main` — …
- 🔧 `merge_write` — Merge df_new into the lake CSV (union on Date, prefer new; never truncate).

### `_ops/download_equity_history.py`
download_equity_history.py — Equity 1-min history backfiller (volume included)

- 🔧 `backfill` — …
- 🔧 `creds` — …
- 🔧 `fetch_chunk` — Return dict{date_iso: DataFrame} for [frm,to]; '' on rate-limit/error.
- 🔧 `is_populated` — …
- 🔧 `main` — …
- 🔧 `symbol_list` — …
- 🔧 `trading_weekdays` — …

### `_ops/download_nifty50.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `run_bulk_download` — …

### `_ops/eod_digest.py`
eod_digest.py — EOD digest across all mission strategies ("aapki 7 jodi aankhein").

- 🔧 `discover_ids` — SAB strategies — current + future, koi hardcoded list nahi.
- 🔧 `expected_mode_for` — Per-strategy expected mode — config ka `mode` key (TRAP #57 fix isse persist
- 🔧 `ist_today` — …
- 🔧 `judge` — Return (colour, [reasons]) — colour in GREY/RED/YELLOW/GREEN.
- 🔧 `load_expectations` — …
- 🔧 `main` — …
- 🔧 `mode_is_explicit` — config me is strategy ka `mode` key literally present hai?
- 🔧 `parse_log` — Return a dict of everything the day's log tells us for one strategy.
- 🔧 `store_section` — …

### `_ops/eod_report.py`
eod_report.py — EK LINK me poora din: "aaj algo ki health kaisi thi?"

- 🔧 `blocked_whatif_html` — RMS-block hui entries ka "agar li hoti to kya hota" — skipped_replay engine se
- 🔧 `bt_live_html` — …
- 🔧 `bt_live_match` — Per-strategy BACKTEST ↔ LIVE agreement over a trailing window (read-only —
- 🔧 `collect` — …
- 🔧 `esc` — …
- 🔧 `ist_today` — …
- 🔧 `main` — …
- 🔧 `pnl_bar_svg` — …
- 🔧 `pos_neg` — …
- 🔧 `render` — …
- 🔧 `update_index` — …

### `_ops/error_watch.py`
error_watch.py — poori app ka har error ek hi jagah (🔔) pe le aata hai.

- 🔧 `check_services` — Koi zaroori systemd service gir gaya? (Linux/VPS only.)
- 🔧 `check_strategies` — Config me active par process gayab = chup-chaap mari hui strategy.
- 🔧 `scan_logs` — logs/*.log ke naye ERROR/CRITICAL/Traceback → notify.error. Wapas: count.
- 🔧 `scan_once` — Ek poora cycle. Har hissa apne guard me — ek fail doosre ko na roke.

### `_ops/export_trade_log.py`
export_trade_log.py — read-only human-readable export of the live trade log.

- 🔧 `main` — …

### `_ops/fii_flow.py`
fii_flow.py — FII/DII "big player" positioning data-lake (NSE, free, EOD).

- 🔧 `cmd_backfill` — …
- 🔧 `cmd_daily` — …
- 🔧 `cmd_rebuild_master` — Recompute master in ONE pass (O(n), not O(n^2)).
- 🔧 `compute_kpis` — Per-participant contracts -> one flat dict of the KPIs we care about.
- 🔧 `download_oi_day` — Return raw CSV text for date d, or None if no data (holiday/weekend/missing).
- 🔧 `fetch_cash` — Current-day FII/DII cash. Returns {'date','fii_net','dii_net',...} or None.
- 🔧 `main` — …
- 🔧 `new_session` — …
- 🔧 `parse_oi` — Raw participant-OI CSV text -> {participant: {col: int}}. Robust to spaces.
- 🔧 `raw_oi_path` — …
- 🔧 `upsert_master_from_raw` — Read one raw_oi file, compute KPIs, upsert into master. Returns True if written.

### `_ops/fii_flow_view.py`
fii_flow_view.py — DISPLAY-ONLY reader for the FII/DII flow dashboard (/fii-flow).

- 🔧 `series` — Return {'cols': [...], 'rows': [[...], ...], 'meta': {...}}.

### `_ops/gex_profile.py`
gex_profile.py — Gamma-Exposure (GEX) profile per strike, from the on-disk option-chain snapshots. Display-only (no order/risk/live path).

- 🔧 `available_dates` — Sorted (oldest->newest) list of dates that have a captured chain CSV for
- 🔧 `latest` — Just the most recent snapshot (live auto-refresh).
- 🔧 `latest_date` — …
- 🔧 `profile` — Return {ok, underlying, date, expiry, expiries[], snaps[], source, smooth} for one day.

### `_ops/goal_planner.py`
goal_planner.py — "mujhe ₹X chahiye Y date tak" → lots ka basket, aur usko system pe apply.

- 🔧 `active_plan` — …
- 🔧 `apply_plan` — Plan ko nifty_config me likho. Sirf lots + capital_rs.
- 🔧 `funding_check` — Plan ko user ke ASLI broker funds ke against tolta hai.
- 🔧 `preview_apply` — Kya-kya badlega — koi write nahi. Har row: lots old→new, cap old→new, mode (untouched),
- 🔧 `rollback` — Pichle apply ka config backup wapas — plan store bhi peeche.
- 🔧 `scenarios` — Safe / Balanced / Aggressive — teeno me DONO cheezein badalti hain: kitna risk lena
- 🔧 `solve` — Greedy integer-lot allocation:

### `_ops/heartbeat.py`
heartbeat.py — DEAD-MAN SWITCH: "koi khabar na aana" bhi ek khabar hai.

- 🔧 `check_dashboard` — (alive, detail). None = pata nahi chala (jhootha alarm nahi bajana).
- 🔧 `check_units` — [(unit, active, detail)] — systemd se. Linux ke bahar khaali list.
- 🔧 `main` — …
- 🔧 `new_supervisor_events` — Pichli baar ke baad ke naye supervisor events (respawn / give-up).
- 🔧 `run` — …
- 🔧 `safe_mode_status` — …

### `_ops/idea_vault.py`
idea_vault.py — Quick idea/strategy/bug video capture store (display-only).

- 🔧 `add` — Save an uploaded werkzeug FileStorage into the clips dir + a store entry.
- 🔧 `clip_path` — …
- 🔧 `delete` — …
- 🔧 `get` — …
- 🔧 `list_ideas` — …
- 🔧 `load` — …
- 🔧 `save` — …
- 🔧 `update` — …
- 🔧 `video_mime` — …

### `_ops/intervention_report.py`
intervention_report.py — "manual cut" counterfactual: for each position the USER closed by hand, what would the STRATEGY's own exit have given? Quantifies whether manual intervention was net + or − for the day (live AND paper).

- 🔧 `analyze` — {ok, date, mode, cuts:[...], strategy_exits:[...], net_impact, helped, hurt,
- 🔧 `available_trade_dates` — Distinct dates that have any order_store rows (candidate report dates).
- 🔧 `build_all` — Pre-warm: compute + store every available date (idempotent). The LATEST date
- 🔧 `build_and_store` — Compute + persist data/intervention/<date>.json (for the EOD timer + trend).
- 🔧 `chart_bars` — Full-OHLC premium bars for the intervention chart popup: [{t,o,h,l,c}] with
- 🔧 `overview` — Per-period intervention aggregate across ALL available dates, filtered by
- 🔧 `trend` — Last n stored days' net_impact (for the report's trend strip).

### `_ops/invariant_guard.py`
invariant_guard.py — PROACTIVE "does the app match reality + do the always-true rules hold?" sentinel.

- 🔧 `check_all` — …
- 🔧 `inv_app_matches_broker` — #1 — app's live net == Kite's real net, per contract. Catches phantom /
- 🔧 `inv_mtm_sane` — No open position whose implied notional is absurd (phantom ₹-lakh, TRAP #92-94).
- 🔧 `inv_no_bad_price` — No filled open position at price<=0 or qty<=0 (₹0-fill → fake P&L, TRAP #1).
- 🔧 `inv_no_blank_symbol` — No open live position without an option symbol (the nameless-order bug).
- 🔧 `inv_no_duplicate_trade_id` — No single broker trade-id recorded on more than one row (double-count source).
- 🔧 `main` — …
- 🔧 `run` — Check + (optionally) fire loud alerts. Returns the violation list.
- 📦 `Violation` — …

### `_ops/lake_pull_to_pc.py`
lake_pull_to_pc.py — build the 1-min expired-option lake ON THE VPS (fresh token + coordinated rate-limiter = safe during live market hours) and MIRROR it to this PC, one underlying at a time, freeing VPS disk as we go.

- 🔧 `dl_cmd` — …
- 🔧 `download_one` — Launch + supervise the VPS download of one underlying until DONE.
- 🔧 `is_done` — …
- 🔧 `log` — …
- 🔧 `main` — …
- 🔧 `proc_alive` — …
- 🔧 `pull_one` — scp the underlying folder to the PC, verify, then delete from the VPS.
- 🔧 `ssh` — …

### `_ops/m_pattern_ironfly.py`
m_pattern_ironfly.py — PURE state + decision for the IV-pop M-rollover IRON-FLY (02.18). No broker / order / Dhan import -> standalone-testable. Firing legs (execution_gateway), live LTP, the M-signal read (option_curves) and squareoff are the CALLER's job (m_pattern_ironfly_live).

- 🔧 `add` — …
- 🔧 `build_position` — …
- 🔧 `check_exit` — …
- 🔧 `fired_today` — …
- 🔧 `get` — …
- 🔧 `has_open` — …
- 🔧 `hold_expired` — True once `max_hold_days` trading days have elapsed since entry_date (inclusive of
- 🔧 `list_open` — …
- 🔧 `mark_fired` — …
- 🔧 `position_mtm` — …
- 🔧 `set_status` — …
- 🔧 `update` — …

### `_ops/m_pattern_ironfly_live.py`
m_pattern_ironfly_live.py — LIVE wiring for the IV-pop M-rollover IRON-FLY (02.18). Self-contained (keeps trader_dashboard.py thin). PAPER hard-locked, OFF by default.

- 🔧 `cfg` — …
- 🔧 `detect` — …
- 🔧 `fire_ironfly` — Enter one iron-fly (HEDGE legs first -> never naked). Returns pos|None.
- 🔧 `live_series` — [(hhmm, atm_combined_premium)] for today from the /curves collector. Zero extra Dhan.
- 🔧 `mpfly_loop` — ~20s: M-rollover entry + 50%-credit target + (+max_hold_days) time-exit + expiry backstop.

### `_ops/morning_brief.py`
morning_brief.py — subha ek-nazar market snapshot (display-only, Rule 10).

- 🔧 `build_brief` — …
- 🔧 `get_crypto` — …
- 🔧 `get_events` — …
- 🔧 `get_flows` — …
- 🔧 `get_gift` — GIFT Nifty value + change (Moneycontrol). gap = GIFT's own change; falls back to
- 🔧 `get_india` — NIFTY/BANKNIFTY prev-session close + day change + VIX from collector lake.
- 🔧 `get_news` — …
- 🔧 `get_reddit_buzz` — …

### `_ops/opt_pnl.py`
opt_pnl.py — per-trade "what-if" P&L under the two OPTIMISED SL/Target profiles that the grid-search picked (best-fixed + best-aggressive).

- 🔧 `compute_for_trades` — `trades` = order_store `details` list (completed trades only used).

### `_ops/opt_whatif.py`
opt_whatif.py — manual options "what-if" backtest from REAL chain data.

- 🔧 `available_dates` — Dates offering data — collector (recent) ∪ lake (historical), newest first.
- 🔧 `chain_at` — Option-chain snapshot AT a backtest date+time — for the What-If chain-GRID picker.
- 🔧 `intraday_series` — Per-minute combined premium (cost-to-close) + net position delta over the ENTRY
- 🔧 `iv_coverage` — Since-when REAL IV is available. IV is model-sensitive, so ONLY the broker's own
- 🔧 `leg_prices_at` — Per-leg REAL premium AT time `hm` on `date` (the BACKTEST price at that moment,
- 🔧 `list_expiries` — Expiries with STORED backtest data for this date, each {date, monthly}. What-If is
- 🔧 `payoff_at` — Payoff + KPI for the whatif2 Strategy Builder — legs priced at their ENTRY
- 🔧 `run` — legs = [{side:'SELL'|'BUY', strike:float, type:'CE'|'PE'}]. `expiry` = a specific

### `_ops/option_alerts.py`
option_alerts.py — real-time "unusual option behaviour" watcher.

- 🔧 `evaluate` — Pure: returns a list of {key, level, msg} for the current state. No side effects.
- 🔧 `read_log` — Fired alerts for a day (for the /curves markers). Display-only.
- 🔧 `replay_day` — Regenerate the fired-alert log for a STORED day by replaying evaluate() minute
- 🔧 `watch_loop` — Daemon loop — evaluate every `interval`s during market hours, fire alerts.

### `_ops/option_chain_collector.py`
option_chain_collector.py — live NIFTY + BANKNIFTY option-chain + India-VIX snapshot collector.

- 🔧 `fetch_chain` — …
- 🔧 `fetch_expiry_list` — …
- 🔧 `fetch_vix` — India VIX LTP via marketfeed/ltp (shared account bucket). Cooperates with the
- 🔧 `hdrs` — …
- 🔧 `load_creds` — …
- 🔧 `log` — …
- 🔧 `main` — …
- 🔧 `market_open` — …
- 🔧 `resolve_vix_sec_id` — Find India VIX sec_id from the Dhan scrip master (never hardcode — no-assumptions rule).
- 🔧 `run_snapshot` — …
- 🔧 `snapshot_underlying` — Return (rows, spot) for one underlying's ATM±N strikes at time dt.
- 🔧 `write_rows` — …

### `_ops/option_curves.py`
option_curves.py — Sensibull-style intraday option curves from the on-disk option-chain snapshots.

- 🔧 `available_dates` — All dates with a stored option-chain CSV for this underlying (sorted).
- 🔧 `chain_snapshot` — LATEST per-minute snapshot as a per-strike CE/PE map — the Quick Order
- 🔧 `curves` — Return {ok, underlying, expiry, expiries[], points[]} for one expiry's day.
- 🔧 `curves_multi` — Concatenate the last `days` available option-chain days (<= end_date) into one
- 🔧 `legs_series` — Combined per-minute premium for a FIXED-STRIKE straddle/strangle held all day
- 🔧 `legs_series_multi` — Multi-day legs_series — concatenate the last `days` stored days' held straddle/
- 🔧 `oi_heatmap_series` — OI-change heatmap grid: rows = strikes (ATM±N over the day), cols = `bucket_min`
- 🔧 `skew_series` — Per-minute strike-wise IV smile: for each timestamp, CE-IV and PE-IV across ATM±N
- 🔧 `strike_series` — Per-minute premium series for ONE strike+type (for the /curves right-click

### `_ops/param_stability.py`
Days-since-core-params-last-changed, per strategy — from the config audit log.

- 🔧 `compute` — -> {config_key: {days, since, ever_changed, tracked_since, history}}.

### `_ops/pine2python_drift.py`
Pine2Python drift — 04.04 DirectWebhook (TV pine) vs 04.03 Pine2Python (python).

- 🔧 `align` — Pair by bar (±TOL_BARS). Same bar + same direction = MATCH.
- 🔧 `ist_today` — …
- 🔧 `main` — …
- 🔧 `py_replay` — What 04.03's OWN engine says, bar-by-bar, on that day's real candles.
- 🔧 `py_signals` — What range_v1 actually decided that day, from its own order_store rows.
- 🔧 `tv_signals` — What TradingView SENT for the reference strategy — plus what we skipped.

### `_ops/pnl_journal.py`
pnl_journal.py — Monthly P&L journal (grid) + per-trade comments + media store.

- 🔧 `add_media` — …
- 🔧 `all_media` — …
- 🔧 `build_month` — …
- 🔧 `delete_media` — …
- 🔧 `get_notes` — …
- 🔧 `list_media` — …
- 🔧 `media_keys` — Trade/day keys that have at least one media item — for grid attachment dots.
- 🔧 `media_mime` — …
- 🔧 `media_path` — …
- 🔧 `set_note` — …
- 🔧 `update_media_note` — …

### `_ops/position_carry.py`
position_carry.py — per-position "carry overnight" (MIS → NRML) flag.

- 🔧 `clear_all` — …
- 🔧 `is_carried` — True agar is position (uske group ya id se) carry-overnight flagged hai.
- 🔧 `list_keys` — …
- 🔧 `set_carry` — Toggle carry for a position's GROUP (group_id) — ya empty-group leg ke liye

### `_ops/position_exit_rules.py`
position_exit_rules.py — per-GROUP combined-MTM auto-exit rule store (#02).

- 🔧 `check_exit` — Pure exit decision on a group's live combined MTM (₹, whole position).
- 🔧 `clear_rule` — …
- 🔧 `get_rule` — …
- 🔧 `list_rules` — …
- 🔧 `rule_key` — Canonical identity for a group — prefer the durable group_id link, else
- 🔧 `set_rule` — …

### `_ops/price_triggers.py`
price_triggers.py — NIFTY/BANKNIFTY spot price-trigger conditional orders.

- 🔧 `add_trigger` — Validate + append a new trigger. Returns (ok, trigger_or_errmsg).
- 🔧 `cancel_all` — Clear every trigger (EOD / market-close). Returns count removed.
- 🔧 `claim` — Atomic one-shot claim: if the row is still armed & not fired, flip it to
- 🔧 `due_triggers` — Return armed, not-yet-fired triggers whose condition is met given a
- 🔧 `list_triggers` — …
- 🔧 `remove_trigger` — Delete a trigger by id (armed or already-fired row). Returns True if found.
- 🔧 `set_result` — Record the fire outcome on an already-claimed row (no fired-guard).
- 🔧 `suggest_direction` — Default direction from where spot is NOW relative to level.
- 🔧 `would_fire` — Pure predicate: is `spot` on the trigger side of `level`?

### `_ops/rate_limit_verify.py`
rate_limit_verify.py — automated market-open verification of the TRAP #95 DH-904 rate-limit architecture fix.

- 🔧 `analyse` — …
- 🔧 `main` — …

### `_ops/reconcile_broker.py`
reconcile_broker.py — AUTHORITATIVE live reconciliation (WIP, read-only planner).

- 🔧 `app_live_rows` — order_store LIVE rows for date/broker: known broker_order_ids / trade_ids (from
- 🔧 `apply` — Make the app's LIVE ledger match the broker trade book, authoritatively.
- 🔧 `broker_orders` — {order_id: {sym, side, qty, avg, trade_ids[], contract}} from the broker trade book.
- 🔧 `mirror_if_due` — ROUTINE (called every pos_monitor tick, own ~2.5min cooldown): make the app's
- 🔧 `plan` — READ-ONLY. Returns external broker orders the app never recorded + a per-contract

### `_ops/reconcile_csv.py`
reconcile_csv.py — reconcile the app's LIVE ledger to an uploaded Zerodha tradebook CSV.

- 🔧 `apply` — Make each contract's app net match the CSV (broker) net by recording the missing
- 🔧 `kite_to_trad_sym` — Kite F&O tradingsymbol → the app's trad_sym (ROOT-MonYYYY-STRIKE-CE/PE). Handles
- 🔧 `parse_zerodha_tradebook` — Rows → [{trade_id,time,side,kite_sym,root,trad_sym,product,qty,price}]. Skips header /
- 🔧 `plan` — READ-ONLY. Parse CSV → per-contract broker(CSV) net vs app net → what's out of sync.

### `_ops/registry_economics.py`
Registry economics — per-run lot-independent P&L / charge / capital model.

- 🔧 `all_economics` — {slug: econ}. Default = every registry strategy with a backtest run (so each is
- 🔧 `economics` — Per-run economics (lot-independent), cached on results.js + registry mtime. The run

### `_ops/report_notes.py`
report_notes.py — server-side observation notes for the Daily Report page.

- 🔧 `add_note` — …
- 🔧 `delete_note` — …
- 🔧 `image_dir` — …
- 🔧 `list_notes` — …
- 🔧 `update_note` — …

### `_ops/roadmap.py`
roadmap.py — per-strategy LIVE growth tracker (display-only, no order/risk path).

- 🔧 `actual_equity` — Cumulative equity curve (book + running net) from start_date → today.
- 🔧 `build` — …
- 🔧 `list_strategies` — [(sid, label)] for the picker — configured + deployed order.
- 🔧 `lot_step` — …
- 🔧 `lots_at` — …
- 🔧 `month_idx` — …
- 🔧 `next_lot_equity` — Equity at which lots go current → current+1.
- 🔧 `status` — …

### `_ops/roadmap_daily.py`
roadmap_daily.py — active plan ka ROZ ka report card (display-only, no order/risk path).

- 🔧 `actual_by_day` — {iso: net} — plan ke members ka combined REAL net, exit-date pe bucket.
- 🔧 `build` — Active plan ka daily log payload.
- 🔧 `snapshot` — Roz ka snapshot disk pe (audit ke liye) — timer isse call karta hai.

### `_ops/roadmap_portfolio.py`
roadmap_portfolio.py — PORTFOLIO-level forward projection (display-only, no order/risk path).

- 🔧 `build` — Page payload.
- 🔧 `evaluate` — Kisi bhi lot-vector ka distribution — SAME bootstrap se (dobara simulate nahi).
- 🔧 `load_cfg` — data/roadmap_portfolio.json — membership + book + blocked list.
- 🔧 `members` — Har member ka merged view: config + registry label + live runtime state.
- 🔧 `per_lot_series` — {dates: [iso], nets: [per-lot net Rs], meta: {...}} — backtest ke apne trades se,
- 🔧 `project` — specs me har member ka `lots` — simulate + evaluate ka thin wrapper.
- 🔧 `simulate_per_lot` — Bootstrap ek baar — har member ka PER-LOT path-total.
- 🔧 `trading_days_between` — Aaj (exclusive) se target date (inclusive) tak ke asli trading din (NSE calendar).

### `_ops/signal_replay.py`
signal_replay.py — TRAP #108 detector: "kya LIVE trader ne wahi kiya jo uska apna signal-code kehta hai?"

- 🔧 `apply_gates` — Live loop ke config-gates offline signals pe bhi lagao — warna har
- 🔧 `diff` — Signals ↔ log entries match. Mutates verdicts; returns leftover EXTRA entries.
- 🔧 `fetch_day_df` — Trader ke apne fetch_* path se candles — same API, same resample.
- 🔧 `gate_in_position` — Single-position-hold strategies (chainzone/straddle/condor/backspread ride one
- 🔧 `ist_today` — …
- 🔧 `main` — …
- 🔧 `parse_log_events` — Return dict of log events + run window from logs/{sid}.log for date.
- 🔧 `replay_signals` — Bar-by-bar: har forming-bar tak ka df de kar signal fn chalao —
- 🔧 `resolve_strategy` — (module, sig_fn, dir_key) — script health_check se, fn auto-detect.
- 🔧 `run_for` — Ek strategy ka poora replay pipeline — CLI aur eod_report dono isi se.

### `_ops/skipped_replay.py`
Offline replay of RMS-blocked entry-signals -> hypothetical P&L.

- 🔧 `load_premium_path` — Return sorted [(hhmm, close), ...] for sec_id on date, at/after from_hhmm.
- 🔧 `replay_one` — One skipped-signal -> what-if dict. entry_premium recorded se, exit disk-bars
- 🔧 `run` — …

### `_ops/sm_runner.py`
Generic StockMock-style scheduled strategy runner — PURE logic + config parse.

- 🔧 `describe` — One-line human summary for logs/registry.
- 🔧 `hm_ge` — True if now_hm (HH:MM) >= target_hm.
- 🔧 `is_expiry_day` — Is `date_str` (YYYY-MM-DD) a weekly expiry day for this index? Holiday-shift aware.
- 🔧 `is_sm` — True if this nifty_config entry is a StockMock-style (_sm) strategy.
- 🔧 `parse_cfg` — Normalise nifty_config[strategy_id] → a runner config dict, or None if not _sm.
- 🔧 `should_fire_today` — Day-filter gate: all / expiry / weekday:N.

### `_ops/stat_views.py`
stat_views.py — saved strategy-group "Views" for the Stats tab.

- 🔧 `create_view` — …
- 🔧 `delete_view` — …
- 🔧 `list_views` — …
- 🔧 `update_view` — …

### `_ops/strangle_live.py`
strangle_live.py — LIVE wiring for the positional hedged short-strangle + roll + IV-gate. Self-contained (keeps trader_dashboard.py thin). PAPER hard-locked, OFF by default.

- 🔧 `cfg` — …
- 🔧 `fire_strangle` — Enter one hedged strangle. source: strangle_920 | strangle_manual. Returns pos|None.
- 🔧 `strangle_loop` — ~3s: 9:20 entry (IV-gated) + roll + 50%-credit target exit + expiry squareoff.

### `_ops/strategy_candidates.py`
strategy_candidates.py — "system khud chun kar bataye kaunsi strategy basket me aani chahiye".

- 🔧 `eligible_members` — Solver ke liye member-shaped list — sirf gate-paas candidates.
- 🔧 `scan` — Har run ka candidate-card + gate verdict.
- 🔧 `summary` — …

### `_ops/strategy_supervisor.py`
strategy_supervisor.py — fork-based launcher for CODE3B live/paper strategies.

- 🔧 `daemon_alive` — Kya daemon chal raha hai? (dashboard fail-safe isko use karta hai)
- 🔧 `daemon_loop` — …
- 🔧 `ist_now` — …
- 🔧 `supervise_manual` — --only ka simple runner (staged rollout / smoke tests). Respawn nahi.

### `_ops/sync_data.py`
sync_data.py — local <-> VPS trading-data union sync (bidirectional)

- 🔧 `do_pull` — …
- 🔧 `do_push` — …
- 🔧 `local_index` — rel(posix) -> size for every file under LOCAL_ROOT.
- 🔧 `main` — …
- 🔧 `plan` — Return (to_pull, to_push) rel lists using 'populated beats empty'.
- 🔧 `remote_index` — rel(posix) -> size for every file under REMOTE_ROOT (one ssh call).

### `_ops/sync_optionchain.py`
Sync the live option-chain collector CSVs from the VPS down to THIS machine, so the What-If / whatif2 Strategy Builder (and /curves, /gex) can serve RECENT dates locally.

- 🔧 `main` — …

### `_ops/sync_pine.py`
sync_pine.py — Pine version store (dashboard "Pine > History") ko local (Windows) aur VPS ke beech sync karta hai. Smart UNION merge: kabhi koi version drop nahi hota — dono taraf ke saare versions mila ke DONO pe likh deta hai (ekdum identical).

- 🔧 `load` — …
- 🔧 `main` — …
- 🔧 `run` — …
- 🔧 `sync_images` — Union-sync per-version image folders (_PINE/v{N}_imgs/) both ways.

### `_ops/sync_vps_to_local.py`
sync_vps_to_local.py - Download all data, configs, database, logs, and trading files from the VPS to the local repository to populate the local dashboard.

- 🔧 `main` — …
- 🔧 `run_cmd` — …

### `_ops/token_refresh.py`
Token auto-refresh — roz ka manual token kaam khatam (Dhan), aur Kite ke liye loud pre-market reminder.

- 🔧 `check` — Ek poora cycle. Wapas: dict (scheduler/health ke liye).
- 🔧 `dhan_hours_left` — …
- 🔧 `jwt_expiry` — JWT ka exp -> naive local datetime. Parse na ho to None.
- 🔧 `kite_login_url` — …
- 🔧 `kite_status` — (alive: bool|None, msg). None = check hi nahi ho paya (fail-safe).
- 🔧 `main` — …
- 🔧 `renew_dhan` — Dhan token renew. Wapas: (ok: bool, msg: str, hours_left: float|None).

### `_ops/weekly_ironfly.py`
weekly_ironfly.py — PURE state + decision for the WEEKLY POSITIONAL IRON-FLY. No broker / order / Dhan import → standalone-testable. Firing legs (via execution_gateway), live LTP, front-weekly-expiry and squareoff are the CALLER's job (weekly_ironfly_live).

- 🔧 `add` — …
- 🔧 `build_position` — …
- 🔧 `check_exit` — …
- 🔧 `entry_spec` — Iron-fly strikes: SELL ATM CE+PE, BUY wings +-wing. Caller resolves sec_id/price.
- 🔧 `get` — …
- 🔧 `has_open` — …
- 🔧 `last_expiry_seen` — …
- 🔧 `list_open` — …
- 🔧 `position_mtm` — …
- 🔧 `set_last_expiry_seen` — …
- 🔧 `set_status` — …
- 🔧 `should_enter` — Fire once on the first trading day of a fresh weekly cycle (= the day AFTER the
- 🔧 `update` — …

### `_ops/weekly_ironfly_live.py`
weekly_ironfly_live.py — LIVE wiring for the WEEKLY POSITIONAL IRON-FLY (02.17). Self-contained (keeps trader_dashboard.py thin). PAPER hard-locked, OFF by default.

- 🔧 `cfg` — …
- 🔧 `fire_ironfly` — Enter one weekly iron-fly. Returns pos|None. HEDGE legs first (never naked).
- 🔧 `ironfly_loop` — ~3s: day-after-expiry 9:20 entry + 50%-credit target exit + weekly-expiry squareoff.

<a id="TOOLS"></a>
## `_TOOLS` — Dev tools (audit, backtest engine, doc-gen, validation)

### `_TOOLS/architecture_audit.py`
_TOOLS/architecture_audit.py — Mechanical architecture audit (CLAUDE.md Rule 6B enforcer).

- 🔧 `apply_baseline` — Downgrade FAILs covered by the baseline ratchet to level BASE (non-blocking).
- 🔧 `audit` — …
- 🔧 `check_backtest_risk_bypass` — …
- 🔧 `check_core_imports_ui` — Check 8 — CORE-IMPORTS-UI: _core/ must not depend on the Flask UI module.
- 🔧 `check_dup_indicators` — …
- 🔧 `check_inline_risk` — …
- 🔧 `check_inline_signal` — …
- 🔧 `check_margin_gate` — MARGIN-GATE — _leg_capital()/kite_basket_margin() are PRIVATE to risk_gate.py.
- 🔧 `check_pe_offset_sign` — …
- 🔧 `check_raw_http_orders` — Check 7 — RAW-HTTP-ORDER: broker order endpoint hit directly over HTTP.
- 🔧 `check_raw_orders` — …
- 🔧 `check_raw_strategy_label` — Raw strategy id kisi user-visible surface pe ja raha hai?
- 🔧 `check_recover_field` — Check 10 — RECOVER-FIELD: reading an order_store open/closed row via the
- 🔧 `check_singleton_guard` — Every live trader DAEMON (its own --id CLI + a while-True order loop) must call
- 🔧 `check_state_persistence` — …
- 📦 `Finding` — …
- 🔧 `is_excluded` — …
- 🔧 `iter_display_files` — …
- 🔧 `iter_repo_files` — …
- 🔧 `load_baseline` — …
- 🔧 `main` — …
- 🔧 `parent_dir` — Immediate dir of the file relative to repo root ('' if repo root).
- 🔧 `parse` — …
- 🔧 `rel` — …
- 🔧 `staged_files` — …
- 🔧 `write_baseline` — …
- 🔧 `write_report` — …

### `_TOOLS/backtest_engine.py`
backtest_engine.py — generic date-range backtester for ANY strategy type (range / rsi / ema). Used by the dashboard's "📊 Backtest" button in the strategy Run modal.

- 🔧 `compute_indicator_for_chart` — Backs the dashboard's 'Add Indicator' dropdown — POST /api/indicators/compute.
- 🔧 `ensure_and_load_symbol` — Generic per-symbol bar loader — picks the NIFTY index store or the
- 🔧 `ensure_equity_data` — Download any missing trading-day 1-min CSVs for this symbol, same
- 🔧 `ensure_nifty_data` — Download any missing trading-day NIFTY 1-min CSVs in [date_from, date_to]
- 🔧 `load_1m_range` — …
- 🔧 `load_equity_1m_range` — …
- 🔧 `resample` — …
- 🔧 `resample_with_volume` — Same as resample() but also sums volume — needed for VWAP, which
- 🔧 `run_backtest` — …

### `_TOOLS/gen_module_docs.py`
gen_module_docs.py — auto-generate _DOCS/MODULES.md from module docstrings.

- 🔧 `build` — …
- 🔧 `main` — …

### `_TOOLS/generate_june_mfe.py`
generate_june_mfe.py — Auto-generate MFE/MAE trades from Range Chain backtest.

- 🔧 `backtest_day` — …
- 🔧 `get_option_sec_id` — Get sec_id for option. Order: scrip master → cache → Dhan probe.
- 🔧 `get_price_at_time` — Get close price at specific time (HH:MM). Falls back to nearest minute.
- 🔧 `make_daily_df` — daily_ohlc: list of {date, open, high, low, close}. Returns DataFrame.
- 🔧 `resample_5m` — …
- 🔧 `run` — …
- 🔧 `trading_days` — List of weekday dates between from/to (exclusive of weekends).

### `_TOOLS/live_vs_tv.py`
live_vs_tv.py — the LIVE engine vs TradingView, on one chart, on real data.

- 🔧 `cont5_bars` — ONE continuous 5m series across every available day — same construction
- 🔧 `html` — …
- 🔧 `live_trades` — Run the LIVE engine day by day. Returns list of closed trades.
- 🔧 `main` — …
- 🔧 `match` — Pair by entry time +-tol bars, same side. TV fills at the NEXT bar's open,
- 🔧 `stats` — Per-trade point P&L — no lots, no charges. Both sides measured the same

### `_TOOLS/optimizer.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `run_optimization_stream` — Generator that yields progress dictionary: {"progress": int}

### `_TOOLS/save_daily_summary.py`
save_daily_summary.py — Daily P&L summary from nifty_trader.log Run after market close: python save_daily_summary.py Saves to: /root/code4/results/YYYY-MM-DD.txt

### `_TOOLS/scanner_ema_52.py`
_(no module docstring — add ek 1-line role add karo)_

- 🔧 `run_scanner` — …

### `_TOOLS/validate_strategy.py`
validate_strategy.py — Phase 4 validation harness.

- 🔧 `backtest_day` — Collect every trade for ONE day, with TradingView's fill convention.
- 🔧 `daily_bars` — Daily OHLC for key levels. Prefer full Dhan daily history (nifty_daily.csv,
- 🔧 `debug_day` — Trace a single day: key levels, zone formations, entries/exits + TV trades.
- 🔧 `load_1m` — …
- 🔧 `parse_log` — Parse the consistent Pine Logs export (ZONE/SIGNAL/EXIT from ONE run) into
- 🔧 `parse_tv` — Group TV List-of-Trades rows into trades keyed by 'Trade number'.
- 🔧 `resample_5m` — …
- 🔧 `run` — …
- 🔧 `write_html` — …

<a id="root"></a>
## `(root entrypoints)` — Entrypoints (systemd yahin se: dashboard/monitor/health)

### `_paths.py`
_paths.py — central sys.path bootstrap for CODE3B.

- 🔧 `setup` — …

### `health_check.py`
health_check.py — Strategy "order bhej payegi ya nahi?" preflight.

- 🔧 `build_report` — Poora report dict banao (pretty + json dono isi se).
- 🔧 `check_strategy` — Ek strategy ka poora preflight. Returns (rows[(name,status,detail)], is_red).
- 🔧 `fire_test` — PAPER test-fire: asli order path chala ke confirm karo order DB me land hota.
- 🔧 `main` — …
- 🔧 `render_pretty` — …

### `monitor_daemon.py`
monitor_daemon.py — Background safety loops as a STANDALONE process.

### `set_password.py`
set_password.py — set / change the dashboard login.

- 🔧 `main` — …

### `trader_dashboard.py`
trader_dashboard.py — Web UI for Algo Trader Run: python trader_dashboard.py Open: http://72.61.173.32:5099

- 🔧 `api_app_orders` — App ke apne order records (order_store) — PAPER + REAL, kisi bhi date ke,
- 🔧 `api_auto_straddle_close` — …
- 🔧 `api_auto_straddle_config` — Get/set nifty_config['_auto_straddle']. Live mode is NOT settable here — paper-locked.
- 🔧 `api_auto_straddle_fire` — B — Quick Order 'Sell ATM Straddle'. PAPER.
- 🔧 `api_auto_straddle_list` — Today's straddles + live combined premium + P&L points for the UI.
- 🔧 `api_auto_straddle_preview` — Live preview for the Quick Order straddle LEG WINDOW / chain multi-leg:
- 🔧 `api_auto_strangle_fire` — Manual fire of the positional hedged short-strangle + roll + IV-gate. PAPER
- 🔧 `api_auto_strangle_list` — Open strangle positions (display).
- 🔧 `api_backtest_calendar_summary` — Same {summary, trades, filters} shape as /api/orders/calendar-summary,
- 🔧 `api_backtest_lab` — Run a multi-day options backtest. Returns summary + monthly/day-wise breakup +
- 🔧 `api_backtest_lab_intraday` — One day's minute-by-minute combined MTM + spot (per-day PnL modal).
- 🔧 `api_backtest_optimize` — …
- 🔧 `api_backtest_pine_code` — Pine source for the strategy actually run in a backtest — used by the
- 🔧 `api_backtest_progress` — Polled by the Results page while /api/backtest/run is in flight, so a
- 🔧 `api_backtest_run` — Generic date-range backtest for any strategy type (range/rsi/ema).
- 🔧 `api_backtest_runs` — List available backtest runs (from runs/index.json) for the Stats-tab
- 🔧 `api_backtest_save_config` — Edit & Re-run modal's 'Save & Run' — merge edited fields into
- 🔧 `api_backtest_saved_delete` — …
- 🔧 `api_backtest_saved_list` — Saved Results table on the Results page — only key stats + the run's
- 🔧 `api_backtest_saved_save` — …
- 🔧 `api_basket_note` — User's own comment on an option BASKET (pair) in the Completed Trades
- 🔧 `api_broker_balances` — Dhan + Kite cash/collateral/total_margin, for the header widget + RMS
- 🔧 `api_broker_ledger` — Balance-over-time (ledger) for the RMS Broker Balances panel — per broker:
- 🔧 `api_broker_ledger_upload` — Upload a broker's own ledger/statement CSV (Zerodha Console → Funds →
- 🔧 `api_broker_orders` — Live broker order book + trade book + app-blocked entries (display-only, today-only).
- 🔧 `api_broker_orders_csv_match` — Uploaded Zerodha tradebook CSV vs live broker trades → exact-match report.
- 🔧 `api_bs_shadow` — Black-Scholes shadow of the REAL trades, per day + per strategy, from the
- 🔧 `api_bt_presets_delete` — …
- 🔧 `api_bt_presets_list` — …
- 🔧 `api_bt_presets_save` — …
- 🔧 `api_bulk_order` — …
- 🔧 `api_bulk_preview` — …
- 🔧 `api_chain_fire_basket` — Quick Order chain MULTI-LEG basket (PAPER). Legs carry SIGNED index offsets
- 🔧 `api_close_position` — Close an open position — place opposite order using exact trading symbol.
- 🔧 `api_close_position_group` — Square off ALL open legs sharing a group_id together (e.g. a sold option
- 🔧 `api_config` — …
- 🔧 `api_daily_report` — …
- 🔧 `api_daily_report_dates` — Dates that actually have trade data (same mode/source/broker/strategy
- 🔧 `api_daily_report_health` — EOD system-health for the Daily Report page — the same ✅ Positives / ❌
- 🔧 `api_delete_optimization` — …
- 🔧 `api_delta_chain` — Live BTC/ETH option chain (one Delta /v2/tickers call). Display-only.
- 🔧 `api_delta_ironfly` — Validated daily Iron-Fly setup with live premiums (display-only context).
- 🔧 `api_delta_paper` — Delta paper Iron-Fly state (open + completed + config). Display-only, PAPER.
- 🔧 `api_deploy_variation` — Saved Result "🚀 Deploy" → create a NEW named strategy variation the
- 🔧 `api_downloader_alerts` — …
- 🔧 `api_fii_flow` — …
- 🔧 `api_fill_delays` — TRAP #63 monitoring data — every live order whose fill-confirm poll
- 🔧 `api_get_optimizations` — …
- 🔧 `api_get_risk_config` — …
- 🔧 `api_get_token` — …
- 🔧 `api_gex` — …
- 🔧 `api_health_app_vs_broker` — Does the app's picture of open positions match Zerodha's? ONE line, always
- 🔧 `api_health_report` — Last startup health-check ka structured report (health_check.py --json ne
- 🔧 `api_ideas_delete` — …
- 🔧 `api_ideas_list` — …
- 🔧 `api_ideas_update` — …
- 🔧 `api_ideas_upload` — …
- 🔧 `api_indicators_compute` — Compute one indicator on demand for the chart's 'Add Indicator' picker.
- 🔧 `api_indicators_list` — Backs the chart's 'Add Indicator' dropdown — name + param schema for
- 🔧 `api_intervention` — Counterfactual for the day's manually-cut positions (display-only).
- 🔧 `api_intervention_chart` — Premium OHLC bars for a cut's option (intervention chart popup). The entry /
- 🔧 `api_intervention_overview` — All-dates intervention aggregate (live/paper/both, day/week/month) — one
- 🔧 `api_intervention_rerun` — Recompute + persist today's (or a given date's) intervention report.
- 🔧 `api_journal_data` — …
- 🔧 `api_journal_media_delete` — …
- 🔧 `api_journal_media_keys` — …
- 🔧 `api_journal_media_list` — …
- 🔧 `api_journal_media_note` — …
- 🔧 `api_journal_media_upload` — …
- 🔧 `api_journal_notes` — …
- 🔧 `api_journal_set_note` — …
- 🔧 `api_kill_floor_status` — Live kill-floor state for the RMS tab's big display (2026-07-02).
- 🔧 `api_kite_exchange_token` — request_token → access_token exchange karo via Kite API.
- 🔧 `api_kite_key_status` — Kite api_key/api_secret already saved hain ya nahi (permanent creds).
- 🔧 `api_kite_login_url` — Zerodha login URL return karo — user browser mein kholta hai.
- 🔧 `api_kite_save_key` — API key + secret config.json mein save karo (one-time setup).
- 🔧 `api_kite_test_order` — NIFTY ATM CE test order (1 lot) — Kite F&O permission verify karne ke liye.
- 🔧 `api_lab_upload_xlsx` — Parse an uploaded cross-check .xlsx -> recompute metrics with the canonical
- 🔧 `api_log` — …
- 🔧 `api_lot_sizes` — …
- 🔧 `api_ltp_stream` — SSE endpoint — streams live LTP from dhan_feed WebSocket every 500ms.
- 🔧 `api_manual_order` — …
- 🔧 `api_margin_history` — …
- 🔧 `api_morning_brief` — …
- 🔧 `api_notifications` — History + unread count. ?after=<id> = sirf naye (frontend ka incremental poll).
- 🔧 `api_notifications_clear` — Poori history wipe — sirf explicit user action se (banner ka ✕ nahi).
- 🔧 `api_notifications_read` — Mark read. {"ids": [...]} ya {} = sab. Record DELETE nahi hota — sirf
- 🔧 `api_notify` — Frontend se error push karne ka raasta (window.onerror, failed apiFetch).
- 🔧 `api_option_alerts` — Fired option-chain alerts for a day → chart markers on /curves. Display-only.
- 🔧 `api_option_chain` — Quick Order CHAIN — ATM±N strikes for one expiry: per strike CE/PE
- 🔧 `api_option_curves` — …
- 🔧 `api_option_expiries` — Listed option expiries (weeklies + monthlies, >= today) for a symbol, so the
- 🔧 `api_option_legs` — Combined held-strike premium for a /curves 'Fixed strike' straddle/strangle.
- 🔧 `api_option_ltp` — CE/PE LTP for Quick Order widget. Prefers the live dhan_feed WebSocket
- 🔧 `api_option_oi_heatmap` — OI-change heatmap grid (strike × time bucket) → /curves heatmap panel.
- 🔧 `api_option_skew` — Per-minute strike-wise IV smile (CE + PE across ATM±N) → /curves skew panel.
- 🔧 `api_option_strike` — Per-strike premium series for /curves right-click 'Load strike chart'.
- 🔧 `api_orders` — Trade DB (order_store) — completed trades + open positions for a date,
- 🔧 `api_orders_book_close` — Open position ko BOOK se hatao — koi real Dhan order NAHI jaata. Sirf ek
- 🔧 `api_orders_calendar_summary` — Returns daily P&L and trade count summary for a given year/month or from_date/to_date range.
- 🔧 `api_orders_delete_image` — …
- 🔧 `api_orders_monthly_returns` — All-history month-wise NET ₹ (for the Stats V2 heatmap). Grouped by trade
- 🔧 `api_orders_note_image` — …
- 🔧 `api_orders_optimized_pnl` — Per-trade "what-if" P&L under the two OPTIMISED SL/Target profiles
- 🔧 `api_orders_stats_summary` — Profit Factor / Expectancy / Sharpe over a date range (live/paper data),
- 🔧 `api_orders_upload_image` — Attach one or more images to a trade's note — saved to disk under
- 🔧 `api_param_stability` — Days since each strategy's CORE params (entry/exit/SL/target) were last changed,
- 🔧 `api_peak_pnl_history` — Returns P&L history for any date. Accepts ?date=YYYY-MM-DD (defaults to today).
- 🔧 `api_per_instrument_lock_status` — Live per-instrument trailing-lock state for the RMS tab's display
- 🔧 `api_pine_attach_config` — Attach/update the config text stored WITH a saved script version, and
- 🔧 `api_pine_code` — …
- 🔧 `api_pine_delete` — …
- 🔧 `api_pine_desc` — …
- 🔧 `api_pine_history` — …
- 🔧 `api_pine_images_delete` — …
- 🔧 `api_pine_images_get` — …
- 🔧 `api_pine_images_upload` — …
- 🔧 `api_pine_latest` — …
- 🔧 `api_pine_save` — …
- 🔧 `api_pine_strategies` — Return unique strategies that have a py_file, for the Run tab dropdown.
- 🔧 `api_position_carry_get` — Currently carried-overnight (NRML) position keys — so Open Positions can show
- 🔧 `api_position_carry_set` — Toggle a position MIS <-> NRML. Body: {group_id, id, on}. on=True → NRML
- 🔧 `api_position_exit_rule_clear` — Clear a group's armed auto-exit rule. Query: ids=… or group_id=….
- 🔧 `api_position_exit_rule_get` — Currently-armed combined-MTM auto-exit rule for a group — so the payoff
- 🔧 `api_position_exit_rule_set` — Arm a combined-MTM auto-exit rule for a position GROUP (#02). When the
- 🔧 `api_position_greeks` — Net + per-leg Delta/Vega for a position GROUP + underlying spot move since
- 🔧 `api_position_groups` — Open + recently-closed option GROUPS (by group_id) for the payoff panel's
- 🔧 `api_position_legs_series` — Per-leg premium series + COMBINED net-structure P&L for a position group
- 🔧 `api_position_payoff` — Payoff / zone analytics for one position GROUP (DISPLAY-ONLY — describes
- 🔧 `api_position_payoff_margin` — Real HEDGED margin (Kite basket_order_margins, read-only) vs the
- 🔧 `api_positions_ltp` — Live LTP for open positions — joined on sec_id (the ONLY unique contract key),
- 🔧 `api_rate_limit_events` — Visibility into Dhan rate-limit throttling/429s — RMS Risk tab '🚦
- 🔧 `api_reconcile_csv` — Upload a Zerodha tradebook CSV → reconcile the app's LIVE ledger to it. Default is a
- 🔧 `api_reconcile_manual_trades` — Button-triggered (Completed Trades card): pulls today's real broker
- 🔧 `api_registry_economics` — Per-run LOT-INDEPENDENT economics (gross_per_lot, flat_charge, per_lot_charge,
- 🔧 `api_rename_strategy` — …
- 🔧 `api_report_note_image` — …
- 🔧 `api_report_notes` — …
- 🔧 `api_report_settings` — …
- 🔧 `api_reports_generate` — Background me eod_report.py chalao (non-blocking) — page 90s me khud reload hota.
- 🔧 `api_rms_audit_log` — Newest-first list of RMS field changes for the Risk tab's Change History panel.
- 🔧 `api_rms_reconcile` — RMS Stage 3 — read-only drift check: our own capital_in_use(None) vs the
- 🔧 `api_rms_summary` — …
- 🔧 `api_roadmap` — …
- 🔧 `api_roadmap_candidates` — Har Lab-run strategy pe deploy-gate → eligible / weak / rejected (read-only).
- 🔧 `api_roadmap_daily` — …
- 🔧 `api_roadmap_goal` — Solve only — koi write nahi.
- 🔧 `api_roadmap_plan` — …
- 🔧 `api_roadmap_plan_apply` — Config WRITE — sirf lots + capital_rs. Live member ho to typed confirm zaroori.
- 🔧 `api_roadmap_plan_preview` — Kya-kya badlega — read-only.
- 🔧 `api_roadmap_plan_rollback` — …
- 🔧 `api_roadmap_portfolio` — …
- 🔧 `api_run_status` — Return running status of all known strategy ids.
- 🔧 `api_scanner_run` — …
- 🔧 `api_set_config` — …
- 🔧 `api_set_risk_config` — …
- 🔧 `api_set_token` — …
- 🔧 `api_start` — …
- 🔧 `api_stat_views_create` — …
- 🔧 `api_stat_views_delete` — …
- 🔧 `api_stat_views_list` — Saved strategy-group Views for the Stats tab. Display/config only.
- 🔧 `api_stat_views_update` — …
- 🔧 `api_status` — …
- 🔧 `api_stop` — …
- 🔧 `api_straddle_chart_data` — Combined straddle premium (CE close + PE close) intraday + entry marker +
- 🔧 `api_strategy_equity` — Per-strategy equity curves + summary table over a date range (Task 84).
- 🔧 `api_strategy_registry` — Canonical Strategy ID registry (family.member IDs). Read-only; the frontend
- 🔧 `api_strategy_study_trades` — Completed trades for a strategy over a date range + aggregate. The study page
- 🔧 `api_symbols_search` — Backtest Results symbol picker — search Dhan's NSE equity scrip master
- 🔧 `api_sync_from_vps` — LOCAL-only: pull VPS trades.db + configs + logs to this local machine so
- 🔧 `api_sync_positions` — Force-reconcile the app's LIVE ledger against the broker's trade book,
- 🔧 `api_telegram_config` — Telegram alert settings — panel se read/save. Token kabhi wire pe poora
- 🔧 `api_telegram_detect_chat` — …
- 🔧 `api_telegram_test` — …
- 🔧 `api_timer_deploy` — …
- 🔧 `api_timer_status` — …
- 🔧 `api_token_check` — Control tab ka "Check" button — Dhan JWT abhi zinda hai ya nahi.
- 🔧 `api_trade_chart_data` — Option premium 1-min candles for one completed trade + entry/exit marker times.
- 🔧 `api_trade_chart_underlying_data` — Underlying instrument/index 1-min candles for the chart split-view's left
- 🔧 `api_triggers_add` — Arm a new price-trigger. Validates + rejects an instantly-true condition.
- 🔧 `api_triggers_delete` — Cancel/remove one trigger (armed or a fired row from the list).
- 🔧 `api_triggers_list` — Armed/fired price-triggers + live spot + distance-to-level for the UI.
- 🔧 `api_update_note` — …
- 🔧 `api_update_sl_tp` — …
- 🔧 `api_watch` — Merge all *_watch.json files — one entry per running strategy.
- 🔧 `api_watch_chart_data` — Today's 1-min candles for a watchlist symbol + its current zone
- 🔧 `api_watch_strategy` — …
- 🔧 `api_webhook_status` — …
- 🔧 `api_webhook_tv` — Receive a TradingView Pine alert (JSON) and execute via webhook_executor.
- 🔧 `api_whatif` — …
- 🔧 `api_whatif2_intraday` — Per-minute combined premium (cost-to-close) + net position delta over the entry
- 🔧 `api_whatif2_payoff` — Payoff curve (expiry + exit-day) + KPI (max P/L, breakevens, POP, net-credit,
- 🔧 `api_whatif_chain` — Option-chain snapshot AT a backtest date+time for the What-If chain-grid picker —
- 🔧 `api_whatif_coverage` — Since-when REAL IV is available (broker's own reported IV = live collector window)
- 🔧 `api_whatif_expiries` — Stored expiries for a date so the What-If page can simulate on a specific expiry
- 🔧 `api_whatif_legprice` — Per-leg REAL premium AT the entry time on the selected date (backtest price, not
- 🔧 `api_whatif_margin` — LIVE margin + current LTP for the entered legs (current market — resolves
- 🔧 `auto_scheduler` — …
- 🔧 `auto_straddle_loop` — ~3s loop (monitor_daemon): (A) 9:20 scheduled fire (one-shot/day, restart-safe,
- 🔧 `backtest` — …
- 🔧 `backtest_chart` — Full-page chart view — opened in a new tab via the Run modal's
- 🔧 `backtest_db_get` — …
- 🔧 `backtest_db_set` — …
- 🔧 `backtest_lab_page` — StockMock-style multi-day options backtest — build a leg strategy (ATM±N, per-leg
- 🔧 `broker_orders_page` — Broker Orders page — Zerodha ki tarah aaj ke saare executed/rejected orders
- 🔧 `change_password` — …
- 🔧 `crypto_page` — Delta Exchange India crypto (BTC) — live spot + option chain + validated
- 🔧 `daily_report_page` — One-scroll EOD Daily Report — KPIs, target/stat tables, per-strategy +
- 🔧 `fii_flow_page` — FII/DII participant-flow dashboard — Sensibull-style but from our own free
- 🔧 `get_mode` — …
- 🔧 `get_pid` — …
- 🔧 `get_tags_store` — …
- 🔧 `gex_profile_page` — QuantTradingApp-style Gamma-Exposure (GEX) profile — per-strike Net GEX
- 🔧 `idea_vault_page` — Quick idea/strategy/bug video capture — drag-drop a clip, tag it, it
- 🔧 `idea_video_stream` — HTTP Range streaming for an idea clip (ported from CODE7 stream_video).
- 🔧 `index` — …
- 🔧 `intervention_page` — Manual Intervention Report — 'agar haath se cut na karte to kya hota'.
- 🔧 `journal_media_stream` — Serve a journal media file — Range streaming for video, direct for image.
- 🔧 `login` — …
- 🔧 `logout` — …
- 🔧 `morning_brief_page` — One-glance morning snapshot — India indices/VIX + FII-DII flows + PCR +
- 🔧 `mtm_charts_page` — …
- 🔧 `on_option_alert` — C — option_alerts watcher callback. On a straddle-move / gamma-spike alert,
- 🔧 `option_curves_page` — Sensibull-style intraday option curves — ATM straddle premium + ATM gamma
- 🔧 `parse_pnl` — …
- 🔧 `pine_img_serve` — …
- 🔧 `pine_report` — …
- 🔧 `pnl_journal_page` — Monthly P&L journal grid (day-rows x strategy-cols), per-trade drill-down
- 🔧 `pos_monitor_loop` — Monitors open positions for SL_PCT, TP_PCT hits and tracks MAX/MIN LTP.
- 🔧 `presentations_list` — Date-wise YT presentation list — login-gated (before_request).
- 🔧 `presentations_view` — …
- 🔧 `reports_list` — Date-wise EOD report list — login-gated (before_request), self-contained page.
- 🔧 `reports_view` — …
- 🔧 `resolve_trailing_step` — Helper to determine the step size for trailing Stop-Loss/Take-Profit.
- 🔧 `roadmap_page` — Live per-strategy growth tracker — actual equity vs Monte-Carlo corridor,
- 🔧 `save_tags_store` — …
- 🔧 `serve_lab_file` — Strategy Lab — serves the NIFTY research hub + its dashboards / results.js
- 🔧 `serve_lab_hub` — …
- 🔧 `serve_lab_upload` — Upload page for a backtest cross-check workbook (built by xlsx_export.py).
- 🔧 `serve_mockup` — …
- 🔧 `serve_report_note_image` — …
- 🔧 `serve_script3` — Script 3 redesign mockup (static, no wiring yet) — iframed body-only into
- 🔧 `serve_spec_builder` — Strategy Spec Builder — master-prompt generator (static tool page, no wiring).
- 🔧 `sl_map_page` — SL Map — every RMS stop-loss mechanism + which strategy has what, LIVE from
- 🔧 `sm_runner_loop` — Daemon (monitor_daemon thread) that drives every active _sm config strategy: on a
- 🔧 `stats2` — Compact V2 redesign of the Stats page — reuses the same calendar/stats
- 🔧 `straddle_chart_page` — …
- 🔧 `strat_label` — Strategy ka DISPLAY naam — registry se. Unknown/registry-down → raw id.
- 🔧 `strategy_equity_page` — …
- 🔧 `strategy_registry_page` — Unified Strategy Registry tree view — every strategy in one place (login-gated).
- 🔧 `strategy_registry_page_v2` — Strategy Registry v2 — one sortable/filterable table (same live data as /registry:
- 🔧 `strategy_study_page` — Landscape 'study' of every trade a strategy took — one chart per trade
- 🔧 `trade_chart_page` — …
- 🔧 `trigger_watch_loop` — Dedicated ~1s loop (started by monitor_daemon next to the poller). Reads
- 🔧 `update_order_tags` — …
- 🔧 `watch_chart_page` — …
- 🔧 `webhook_monitor_loop` — Trails SL / target / 3:15 squareoff for open TradingView-webhook positions.
- 🔧 `whatif2_page` — Sensibull-style Strategy Builder (backtest) — leg builder + Add/Edit chain modal +
- 🔧 `whatif3_page` — 4-in-1 compare — ek base leg (strike + CE/PE + 0.25Δ) se 4 structures ek saath:
- 🔧 `whatif_page` — Manual options what-if backtest — pick instrument/date/entry-exit time/legs,
