# ARCHITECTURE LOG — CODE3B (Algo Trader)

> Rule: Claude har kaam se PEHLE yahan entry likhega.
> Status values: DONE | IN-PROGRESS | PENDING | CANCELLED
>
> Architecture layers:
> - **broker** — Dhan/Kite API, orders, feed, candles
> - **strategy** — signal logic, Pine→Python conversion
> - **execution** — smart_order, marketable-limit, paper/live
> - **universe** — Nifty-50 scanner, sec_id routing
> - **validation** — TV vs engine match score
> - **ui** — Flask dashboard, tabs, widgets
> - **config** — nifty_config.json, variation management
> - **infra** — VPS deploy, systemd, git/GitHub

---

## 2026-06-21 — TradingView Webhook → auto order engine (Phase 1)
**Status:** DONE
**Kya:** TV Pine alert → Flask webhook → Dhan paper order. TV sirf thin signal (ENTRY/EXIT + direction) bhejta hai; strike select (ATM±offset), option type, qty, paper/live — sab Python config (`webhook_v1`) decide karta hai. Strategy ek hi jagah (Pine) → zero drift. Phase 1: receiver + executor + safety (max/day, no-entry-after).
**Layer:** broker, execution, config, infra
**Files:** `webhook_executor.py` (NEW — handle_signal ENTRY/EXIT, _wh_state, dedup, safety, status), `trader_dashboard.py` (`/api/webhook/tv` token-auth route + `/api/webhook/status` + auto_scheduler guard so non-process keys skip), `nifty_config.json` (`webhook_v1` block)
**Kyun:** Pine→Python full conversion me logic drift hoti thi (90% match ceiling, live fail). TV ko signal-generator banake execution Python me rakhne se drift khatam.
**Reuse:** `dhan_master.get_option_contract/get_equity_info`, `smart_order.execute` (paper==live parity), `brokers/dhan_broker.DhanBroker`, `dhan_feed`, log format `parse_pnl`-compatible (webhook trades P&L tab me auto dikhte hain)
**Verified:** offline (token expired today) — ENTRY→paper log→state→SL, dedup, reopen-block, EXIT netting; HTTP route 403 on bad/no token, 200 + paper order on good token (query + X-WH-Token header); `parse_pnl` → 1 closed trade ₹650. Live order test pending fresh Dhan token (rozana update).
**Depends on:** TradingView paid plan (webhook feature); fresh Dhan token for live-data path
**Next:** Phase 2 — monitor daemon thread (trailing SL premium/index, target, 3:15 force squareoff)

### Phase 2 (same day) — monitor daemon: trailing SL + target + 3:15 squareoff
**Status:** DONE
**Kya:** `webhook_executor.monitor_tick()` — har ~3s open webhook positions pe: (1) premium-mode trailing SL (option premium pe ratchet, default), (2) index-mode trailing (underlying ATR×mult, fallback 30pts), (3) fixed target/SL, (4) 3:15 force squareoff. Daemon `webhook_monitor_loop()` `trader_dashboard.py` __main__ me wired (auto_scheduler ke saath). Helpers: `_current_premium` (feed→REST), `_index_atr` (Wilder RMA, best-effort), `_do_entry` ab `entry_spot`+`idx_sl`+`idx_trail_dist` store karta hai. `_do_exit` reused for all exit reasons (TV_EXIT/TRAIL_SL/TARGET/IDX_TRAIL/SQUAREOFF_315).
**Verified:** offline stubs — premium SL ratchet 120→130→150 (no down-ratchet), TRAIL_SL exit @148; TARGET exit @195 (tgt 190); 3:15 squareoff; index-mode idx_sl trail 24470→24570 → IDX_TRAIL exit on pullback.
**Next:** Phase 3 — "🔗 Webhook" UI tab (mockup-first): config + secret token + TV alert template + live log + open positions.

### Phase 3 (same day) — Webhook UI tab + SELL default + Pine override
**Status:** DONE
**Kya:** `templates/index.html` me naya "🔗 Webhook" tab (mockup-first, approved). Sections: connection (webhook URL + secret token, copy/regenerate), execution config grid (strike/qty/trail/SL/target/squareoff), **Option mode Sell/Buy toggle** (Sell default — user selling karta; toggle opt_action + long/short type flip karta: SELL→long PE/short CE, BUY→long CE/short PE), **live strike LTP preview** (CE+PE, `/api/option-ltp` reuse, 4s poll, symbol picker — user ne maanga), TradingView alert template (`{{timenow}}`/`{{strategy.order.action}}` literal via `{% raw %}`, copy ENTRY/EXIT), open positions + live webhook log (`/api/webhook/status` poll).
**Executor changes:** `_DEFAULTS` ab SELL convention (long PE/short CE/opt_action SELL); **`_OVERRIDABLE`** — Pine alert JSON me bheja koi execution param (strike_offset/qty/sl_points/etc.) dashboard config ko override karta hai (`_merge_overrides`) → user ko Pine me set kiya value dobara dashboard me nahi daalna padta.
**Verified (browser, preview):** tab renders 0 console errors; config load/save incl. SELL↔BUY flip persisted; toggles work; LTP graceful degrade (token expired → clean note; VPS pe live); TV template literal placeholders; live POST `/api/webhook/tv` on running server → 403 bad token / executor reached on good token. Jinja `{{ }}` clash fixed via `{% raw %}`. Note: Flask debug=False → template cache; edits need server restart.
**Next:** Phase 4 — VPS deploy (deploy_vps.py + webhook_executor.py in file list; webhook_v1 block on VPS nifty_config.json since gitignored) + real TradingView alert wiring (paper) + optional UFW TV-IP whitelist.

### Phase 4 (same day) — VPS deploy + LIVE end-to-end verified (paper)
**Status:** DONE
**Kya:** webhook engine VPS pe deploy + live test. **Manual SCP** use kiya (deploy_vps.py STALE hai — REMOTE_DIR=`/root/code4` galat, asli dir `/root/CODE3B- TV BACKTEST ENGINE`; FILES me root-level trader files hain jo ab `_TRADERS/` me; SSH/SCP space-quoting bhi nahi). Pushed: `webhook_executor.py` (naya), `trader_dashboard.py`, `templates/index.html`. `webhook_v1` block VPS `nifty_config.json` me **merge** kiya (overwrite nahi — ARS_CHAIN_V1/ema_v1/rsi_v1 intact). `systemctl restart algo-dashboard`.
**Verified LIVE (VPS public IP, fresh token):** `POST /api/webhook/tv` good token → real paper order **SELL NIFTY-Jun2026-24000-PE @ 72.30** (spot 24013 → ATM 24000 PE, SL 102.30 = entry+30, qty 65); EXIT → closed @ 72.40. Bad token → 403. Auto-scheduler started ARS/rsi but **skipped webhook_v1** (process guard works). Monitor thread running (`[WEBHOOK] new trading day` in journal). `GET /` 200 (Jinja ok), webhook tab + `{{...}}` literal served.
**VPS facts (corrected):** dir `/root/CODE3B- TV BACKTEST ENGINE/`, venv `venv/bin/python`, service `algo-dashboard`, has own `data/config.json` token. CLAUDE.md `/root/code4` was stale → fixed.
**Pending (user):** TradingView alert wiring (Webhook URL + alert JSON from the tab) — user will do later. Optional: UFW TV-IP whitelist. deploy_vps.py proper fix (separate task).

### Phase 4b (same day) — HTTPS reachability via Caddy (TradingView "port 80 only" fix)
**Status:** DONE
**Problem:** TradingView HTTP sirf port 80 / HTTPS 443 allow karta hai — `http://...:5099` reject ("Only port 80 is allowed for HTTP"). User ne LAN IP (192.168.29.200) bhi diya tha (TV public chahiye).
**VPS infra (discovered):** port 80 = code2 Docker (busy); port 443 = **Caddy** already running, serving `https://72-61-173-32.nip.io` (nip.io → IP, auto Let's Encrypt valid cert) → `localhost:3737`.
**Fix (user-approved — shared infra):** `/etc/caddy/Caddyfile` me route add (backup liya: `Caddyfile.bak.<ts>`): `handle /algo/api/webhook/* { uri strip_prefix /algo; reverse_proxy localhost:5099 }` + catch-all `handle { reverse_proxy localhost:3737 }` (existing site untouched, verified root→200). `caddy validate` + `systemctl reload caddy` (graceful).
**TradingView webhook URL (LIVE, HTTPS):** `https://72-61-173-32.nip.io/algo/api/webhook/tv?token=<secret>` — tested: bad token 403, good token → executor. Only `/algo/api/webhook/*` proxied (dashboard surface minimal).
**Dashboard:** webhook tab ab `public_webhook_base` config (`https://72-61-173-32.nip.io/algo/api/webhook/tv`) se URL dikhata hai (location.origin fallback) — copy-ready, no LAN:5099 confusion. `index.html` `WH_PUBLIC_BASE` logic.

### Phase 4c (same day) — Pine TV alerts JSON + dual-dashboard sync
**Status:** DONE
**Pine alerts:** `range_chain.pine` ke `alert()` calls ab JSON bhejte hain (LONG→ENTRY/buy, SHORT→ENTRY/sell, exits→EXIT) + `whSymbol` input + 3:15 guarded EXIT. TV setup: "alert() function calls only" + Webhook URL (Message box ignore). Base = user's actual `Desktop/LATEST.txt` (not dashboard v5 — wo purana tha; `show_hlc`/`show_fc_fib` false preserved). Output: `Desktop/LATEST_webhook.txt` + repo `_PINE/range_chain.pine` + dashboard version.
**Pine store mismatch fix:** Dashboard "Pine > History" ek alag store hai (`_PINE/versions.json` + `v{N}.pine` snapshots), repo file se NAHI. User do dashboards chalata hai — **local (Windows, 192.168.29.200)** aur **VPS (72.61.173.32)** — jo diverge ho gaye the (local: VWAP+RSI v2; VPS: Ars webhook). UNION merge karke dono ko identical [1,4,5,6,7,8,9,10] kiya.
**`sync_pine.py` (NEW):** smart union-merge — VPS+local versions.json union, missing snapshots cross-pull, merged store dono pe push. Kabhi version drop nahi. "Pine ek jagah save karo (local) → `python sync_pine.py` → dono identical."
**LOCAL/VPS badge:** `index.html` header me hostname-based badge (🖥️ LOCAL `192.168.*`/`127.*` vs ☁️ VPS) + browser tab title prefix — dono dashboards same dikhte the, confusion fix.
**Encoding:** local dashboard ko `-X utf8` ke saath relaunch kiya (manual launch bina utf8 = emoji mojibake on Windows cp1252; `-X utf8` se versions.json UTF-8 read/write). launch.json me `-X utf8` already hai.

---

## 2026-06-20 — Reusable charting/pattern/zone module (_CHARTING)
**Status:** DONE
**Kya:** Candle pattern detection + zone/pivot builder + indicator calc (pandas-ta) ko `range_trader.py` se nikal ke `_CHARTING/` shared module mein daalna; `backtest_chart.html` ko generic plot-spec renderer banana (indicators/zones/pattern markers) taaki har naya strategy bina chart-code likhe visualize ho. Stretch goal: TV-parity itni achi ho ki Pine-first step skip ho sake.
**Layer:** validation, ui, strategy
**Files:** `_CHARTING/__init__.py`, `_CHARTING/patterns.py`, `_CHARTING/zones.py`, `_CHARTING/indicators.py`, `_CHARTING/plot_spec.py`, `_TRADERS/range_trader.py`, `_TOOLS/backtest_engine.py`, `templates/backtest_chart.html`
**Kyun:** Pine vs Python visual mismatch debug karne mein time barbaad hota tha — asal mein logic bug nahi, sirf Python chart mein zone/indicator draw nahi hota tha
**Depends on:** `pandas-ta` pip install; existing 90.2%/93% validate_strategy.py baseline (regression gate)

### Follow-up (same day) — 3 UX fixes after first review
1. **Picker slowness fixed** — "Add Indicator" ab client-side JS me compute hota hai (candles already page pe hain), server round-trip / data re-download nahi → instant. Server `/api/indicators/compute` route abhi bhi hai (fallback), par picker use nahi karta. VWAP ke liye `_candles_json` ab `volume` bhi bhejta hai.
2. **Strategy ke apne indicators by default** — RSI/EMA/VWAP runners already plot_spec me apne indicators emit karte hain (vwap → EMA(10)+VWAP auto).
3. **Oscillators alag panel (TV jaisa)** — registry me `overlay` flag: EMA/SMA/VWAP/BBANDS price chart pe (overlay=True), RSI/ATR apne bottom panel me (overlay=False, own priceScaleId + scaleMargins). Client RSI math server `ta` se 60 bars baad ~identical (cold-start sirf pehle ~40 bars, documented warm-up behaviour).

### Follow-up 2 (same day) — symbol picker + line styling + NIFTY download bug
4. **NIFTY redundant download fix** — `run_backtest()` ka unconditional `ensure_nifty_data()` hata diya; ab `_run_range` apni NIFTY ensure karta hai, rsi/ema/vwap apne symbol ki. Pehle TCS/POLYCAB (vwap) backtest bhi NIFTY days download karta tha ("downloading NIFTY 1/10" har run) — fixed.
5. **Symbol-aware rsi/ema** — naya `ensure_and_load_symbol(symbol, ...)` generic loader (NIFTY index store ya equity store, `cfg.symbol` se pick). rsi/ema ab kisi bhi symbol pe chalte hain (signal logic symbol-agnostic). `_buffered_from(date_from, symbol)` — equity ke liye flat 45-day warmup (NIFTY-cache extension sirf index ke liye). **Range NIFTY-only hi rehta** — pivot/zone/chain engine index-specific + 90.2% validated, equity generalization separate task.
6. **UI symbol picker har symbol-pickable strategy me** — `modal-multi-row` ab vwap/rsi/ema sab me (range nahi). `symbolPickable(type)` helper. `symbolsFor()` ab explicit `cfg.symbol` ko `symbols` array se priority deta hai.
7. **Indicator line color + thickness UI** — har drawn indicator (default + picker) ke liye 🎨 color picker + 1-4px thickness dropdown; live `applyOptions`, localStorage `bt_ind_styles` me persist (`_addIndicatorSeries` apply karta hai).

---

## 2026-06-16 — Project init + EMA/RSI strategies
**Status:** DONE
**Kya:** CODE3B banaya — EMA 9/20 + RSI(14) paper trader, Flask dashboard port 5099
**Layer:** strategy, ui, infra
**Files:** `nifty_ema_trader.py`, `rsi_trader.py`, `trader_dashboard.py`, `deploy_vps.py`
**Kyun:** CODE4 CLI-only tha, web dashboard chahiye tha
**Depends on:** Dhan JWT token, VPS running

---

## 2026-06-16 — Range Chain strategy
**Status:** DONE
**Kya:** PineScript `Ars_Auto_Rev_Chain_RANGE` ka Python conversion
**Layer:** strategy
**Files:** `range_trader.py`
**Kyun:** Main trading strategy yahi hai — live pe chalani hai
**Depends on:** `dhan_master.py` (option contracts)

---

## 2026-06-17 — Bug fixes batch (stale entry, startup exit, options price)
**Status:** DONE
**Kya:** 4 critical bugs fix — stale signal, fake startup trades, options ₹0 price, TATAMOTORS remove
**Layer:** strategy, execution
**Files:** `range_trader.py`
**Kyun:** Live pe jaane se pehle yeh bugs hote to bade loss hote
**Depends on:** nothing

---

## 2026-06-17 — P&L tab rebuild + Open Positions LTP
**Status:** DONE
**Kya:** Dashboard P&L tab full redesign — summary pills, open positions with live LTP, completed trades table
**Layer:** ui, broker
**Files:** `trader_dashboard.py`, `templates/index.html`
**Kyun:** Pehle P&L readable nahi tha, positions ka LTP nahi dikh raha tha
**Depends on:** Dhan `/v2/marketfeed/ltp`

---

## 2026-06-17 — Universe System (Phases 0–3)
**Status:** DONE
**Kya:** Best-in-class Nifty-50 scanner — broker abstraction, WebSocket feed, marketable-limit, universe engine
**Layer:** broker, execution, universe
**Files:** `brokers/base_broker.py`, `brokers/dhan_broker.py`, `dhan_feed.py`, `smart_order.py`, `universe.py`, `universe_trader.py`, `strategies/`
**Kyun:** yfinance slow + MARKET order slip — Dhan real-time feed + marketable-limit chahiye tha
**Depends on:** Dhan Data API subscription, `dhanhq` pkg

---

## 2026-06-17 — Pine→Python Validation (Phases 4–5)
**Status:** DONE
**Kya:** `validate_strategy.py` — TV "List of Trades" CSV vs engine signals % match score. 90.2% exact achieved.
**Layer:** validation
**Files:** `validate_strategy.py`, `ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md`
**Kyun:** Live pe jaane se pehle engine aur Pine 1:1 match zaroori tha
**Depends on:** `ACCURACY SCORE CLAUD/TEST 1/pine-logs UPDATE.csv`

---

## 2026-06-17 — Pine Version Control (`_PINE/` folder)
**Status:** DONE
**Kya:** `_PINE/` folder — canonical Pine files, git-tracked, ritual for paste→diff→sync→commit
**Layer:** strategy, infra
**Files:** `_PINE/range_chain.pine`, `_PINE/range_chain_zonelog.pine`, `_PINE/README.md`
**Kyun:** Pine files ad-hoc naam se padhi thi — versions track karna mushkil tha
**Depends on:** GitHub repo (`algo-trader.git`)

---

## PENDING — Phase 6 — Go Live
**Status:** PENDING
**Kya:** universe_v1 ko paper se live mode mein switch karna, ek manual order test karna pehle
**Layer:** execution, config
**Files:** `nifty_config.json`, Quick Order widget
**Kyun:** Phases 0-5 done, validation 90.2% — ab real money test
**Depends on:** Dhan account balance > ₹0, JWT token fresh (expires 24h)

---

## 2026-06-18 — Pine Version Manager (dashboard tab)
**Status:** DONE
**Kya:** Dashboard mein "📌 Pine" tab — script paste karo, strategy name auto-parse ho, version+timestamp assign ho, history dikhe
**Layer:** ui, infra
**Files:** `trader_dashboard.py` (2 routes), `templates/index.html` (tab + UI), `_PINE/versions.json` (new)
**Kyun:** Pine script baar baar badle — track karna mushkil; ek jagah paste karo aur confirm ho ki latest loaded hai
**Depends on:** `_PINE/` folder (already exists)

---

## PENDING — UI Polish (universe config tab, shadow badge, Quick Order bid/ask)
**Status:** PENDING
**Kya:** Dashboard mein universe config tab (abhi manual JSON), shadow badge on positions, live bid/ask in Quick Order
**Layer:** ui
**Files:** `trader_dashboard.py`, `templates/index.html`
**Kyun:** Non-blocking — live ke baad karna hai
**Depends on:** Phase 6 done
