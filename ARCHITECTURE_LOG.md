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

## 2026-07-02 — rsi_trader/universe_trader restart-recovery (TRAP #28 ported) + last_day seeding bug found+fixed
**Status:** DONE (deployed — neither process was running at fix time, so zero live impact)
**Kya:** rsi_trader.py aur universe_trader.py dono me _state (positions/active_opts/trades_today ya _state dict) restart pe order_store se rebuild nahi hoti thi — sirf range_trader.py ko TRAP #28 mila tha 2026-06-29, ye dono chhoot gaye the. Naya `_recover_rsi_state()` / `_recover_state_from_order_store()` add kiya, dono ne CE/PE-suffix se LONG/SHORT derive karna aur (universe ke liye) equity-route BUY/SELL entry-side bhi handle karna cover kiya. **Isi ke saath ek naya, zyada serious bug mila:** dono files (aur range_trader.py bhi, jo ABHI LIVE hai) me `last_day/last_date = None` set hota tha loop se pehle — jiski wajah se pehli hi loop-iteration turant "New trading day" reset trigger karti thi, jo recovery ne abhi populate kiya tha use turant wapas khali kar deti thi. VPS log se confirm kiya (ARS_CHAIN_V1.log 2026-07-01 11:24:29): "[RECOVER] re-attached 1 open position" ke agli hi line pe "New trading day — resetting state". rsi_trader/universe_trader me `last_date`/`last_day` ko turant `ist_now().date()` se seed kar diya (recovery ke turant baad) — range_trader.py ka wahi fix pending hai (live process, alag se permission maang raha hoon).
**Layer:** execution
**Files:** _TRADERS/rsi_trader.py, _TRADERS/universe_trader.py
**Kyun:** User ne kal ke gap-report ke baad turant fix karne ko kaha (dono processes is waqt band the, safe tha).
**Depends on:** range_trader.py ka same `last_day=None` bug — separate fix, user go-ahead pending (live process)

---

## 2026-07-02 — P6 ownership-desync audit findings #1-5 all fixed
**Status:** DONE (deployed)
**Kya:** Kal ke audit ne 5 jagah dhoondi thi jahan exit order fresh broker flat-check ke bina fire hota tha (TRAP #44/#73 family, manual close ke saath race). Sab 5 fix: (1) trailing-lock squareoff (per-instrument + aggregate, dono) — naya shared `_pre_exit_guard()` helper, `_do_squareoff` bhi isi se refactor kiya (duplicate logic hataya); (2) webhook_executor `_do_exit` layer-2 — is_flat() → is_flat_fresh(); (3) 3:15 exit-all — range_trader/rsi_trader/universe_trader teeno me flat-check add + range_trader ka dead duplicate elif hataya; (4) universe_trader FLIP-close — flat-check add; (5) manual UI close (/api/close-position, /api/close-position-group dono isi se) — flat-check add, already-flat pe order_store externally_closed mark karta hai, koi fabricated row nahi.
**Layer:** execution
**Files:** trader_dashboard.py, webhook_executor.py, _TRADERS/range_trader.py, _TRADERS/rsi_trader.py, _TRADERS/universe_trader.py
**Kyun:** User ne kal ke P6 audit report ke baad turant fix karne ko kaha — "report only" tha, ab sab live band ho gaye.
**Depends on:** nothing (broker_sync.is_flat_fresh already P3 me bana tha)

---

## 2026-07-02 — Batched LTP poller (ltp_poller.py) + _rest_ltp_fallback rate-limiter wiring
**Status:** DONE (deployed)
**Kya:** Naya ltp_poller.py — algo-monitor me daemon thread, har 1.5s me EK batched /v2/marketfeed/ltp call (sab open positions + NIFTY/BANKNIFTY spot, segment-grouped), results shared_ltp_cache.put_many() se sab processes ko. _rest_ltp_fallback ab shared cache first + dhan_rate_limiter.acquire/note_429 through (pehle throttle se puri tarah invisible tha, apna private cache tha). dhan_broker.quote() pehle se cache-first tha — ab poller us cache ko warm rakhta hai. Direct REST sirf cache-miss one-off (naye contract entry-time) ke liye bacha hai — deliberately, warna entries block ho jati.
**Layer:** broker / infra
**Files:** ltp_poller.py (new), shared_ltp_cache.py (put_many), trader_dashboard.py (_rest_ltp_fallback), monitor_daemon.py (start)
**Kyun:** Worklist Priority 7 — N open positions = N separate 1-req/sec calls; Dhan 1000 symbols/call allow karta hai.
**Depends on:** nothing

---

## 2026-07-02 — Hedge-sibling close: 35s-stale is_flat() → fresh is_flat_fresh() (TRAP #73 last open path)
**Status:** DONE (deployed)
**Kya:** _do_squareoff ka pre-exit flat check ab broker_sync.is_flat_fresh() use karta hai — 5s se purana positions data kabhi trust nahi, fresh broker.positions() fetch (shared cache refresh hota hai to EOD/group burst me ek hi API call). Sibling-close recursion _do_squareoff se hi jati hai to hedge leg bhi covered.
**Layer:** execution
**Files:** broker_sync.py (new is_flat_fresh), trader_dashboard.py (_do_squareoff)
**Kyun:** Worklist Priority 3 — dono hedge legs paas-paas manually close hue to 35s stale cache sibling-close ko already-flat leg pe live order fire karne deta tha (TRAP #73 shape, ye path chhut gaya tha).
**Depends on:** nothing

---

## 2026-07-02 — _pending_group_close queue disk-persist (restart-safe forced hedge-close)
**Status:** DONE (deployed)
**Kya:** _pending_group_close (hedge forced-retry queue) ab data/pending_group_close.json me persist — har add/pop pe write, startup pe same-day restore (loud recovery log), day-rollover pe clear. Keys str-normalized.
**Layer:** execution
**Files:** trader_dashboard.py
**Kyun:** Worklist Priority 4 — restart ke waqt queued leg apni scheduled protection chupchaap kho deta tha (no retry, no alert).
**Depends on:** nothing

---

## 2026-07-02 — Per-instrument trailing lock: account-wide entry-block flag removed + _pos_peaks disk persistence
**Status:** DONE (deployed)
**Kya:** (a) per_instrument mode me single position ka floor fire hone pe ab day-level trailing_lock_fired flag NAHI likha jata (wo flag webhook _do_entry se PURE account ki new entries block karta tha — per-instrument mode ka point hi khatam). User decision: option (a), koi block nahi — fired floor = closed resolved event. (b) _pos_peaks (per-position peak tracker) ab data/pos_peaks.json me persist hota hai (har cycle write, startup pe same-day restore, day-rollover pe clear) — mid-day dashboard restart pe trailing-lock memory zero hone ka gap band (TRAP #38 ka per-instrument equivalent).
**Layer:** execution / ui
**Files:** trader_dashboard.py
**Kyun:** Worklist Priority 2 — live confirmed: ek instrument ka floor fire → poore account ki entries blocked, per-instrument mode chunne ki wajah hi defeat.
**Depends on:** nothing

---

## 2026-07-02 — TRAP #74: order-chase duplicate-order guard (terminal-status + cancel_ok gating)
**Status:** DONE (user review + VPS deploy pending)
**Kya:** smart_order chase loop — manual/external cancel ke baad duplicate order re-place hone ka path band; chase ka self-abort bug (apna hi cancel "REJECTED" samajh lena) fix; Dhan EXPIRED/PART_TRADED + Kite partial-fill statuses ab distinctly handled.
**Layer:** execution / broker
**Files:** smart_order.py, brokers/dhan_broker.py, brokers/kite_broker.py, brokers/base_broker.py, LESSONS.md (TRAP #74)
**Kyun:** Worklist Priority 1 — "MARUTI duplicate" report. Code-trace ne dikhaya reported mechanism deployed code pe fire nahi ho sakta tha (get_fill CANCELLED→REJECTED collapse pehle se tha); asli gaps adjacent the — cancel_ok-unaware re-place, self-defeating chase, unmapped EXPIRED/PART_TRADED.
**Depends on:** nothing

---

## 2026-06-30 — Orders & P&L tab: 5 compounding bugs fixed (OPEN positions, trailing floor, NET panel)
**Status:** DONE
**Kya:** P&L tab me open positions nahi dikh rahi thi, trailing 30% floor kabhi fire nahi hoti thi, NET panel tiles "—" dikh rahe the, page refresh pe 10+ second freeze. Sab ek hi session me fix kiya.
**Layer:** broker, ui
**Files:** `order_store.py` (`_net_rows`), `trader_dashboard.py` (margin estimate, trailing peak restore), `templates/index.html` (JS bugs: `let _tot` scope, `</tfoot>` without opener, `_patchLtpCells` missing branch, TOTAL row as `<tfoot>`)

### BUG #1 — `_net_rows` phantom completed trades (order_store.py)
**Root cause:** OPEN-status rows (status="OPEN", live positions) ko netting algorithm mein daal rahe the. Ek SELL OPEN + hedge BUY OPEN (same trad_sym/strategy) pair ho ke phantom "completed trade" ban jaata tha — P&L=0, open positions blank.
**Fix:** `_OPEN_ST = {"open"}` set banao. `live_rows` alag karo pehle, sirf `closed_rows` par netting chalao. `live_rows` directly `opens` list mein.
**LESSONS.md TRAP #32 bana iske liye.**
**Downstream effect:** Trailing floor bhi is wajah se nahi chal raha tha — `_n_pos=0` se wrong branch execute hoti thi.

### BUG #2 — Trailing peak reset on restart (trader_dashboard.py)
**Root cause:** `_trailing_peak_pnl = 0.0` on every service restart. Agar service 09:50 pe peak ₹7246 dekha, phir 11:30 pe restart hua — peak 0 ho gayi, floor 0 → kabhi squareoff trigger nahi hua.
**Fix:** Startup pe `data/peak_pnl_history.json` padho. Agar aaj ki file hai → `max(v[1] for v in history)` se peak restore karo. Confirmed working: `[TRAILING-LOCK] Restored peak ₹7246 from 500 history entries after restart.`

### BUG #3 — Page freeze 10+ seconds on refresh
**Root cause:** `risk_gate._leg_capital()` har open position ke liye Dhan `/v2/margincalculator` API hit karta tha. 10 positions × 1 req/sec rate limit = 10+ second freeze. `/api/orders` route ka response await hota hai — is doran UI hang.
**Fix:** Local estimate: `margin = qty × price × multiplier (5x for SELL)`. Multiplier `risk_config.json` ke `margin_multiplier` key se. Zero Dhan API calls. Instant.

### BUG #4 — `let _tot` block-scope JS ReferenceError (index.html)
**Root cause:** `let _tot = {g:0,...}` declare tha `if(sortedCompleted.length){` block ke ANDAR, lekin reference tha bahar `window._realizedTot = _tot` line par. Classic JS block-scope trap — `let`/`const` sirf us block mein visible hote hain, `var` ki tarah nahi.
**Fix:** `let _tot` ko `if` block se BAHAR hoist kiya (ek line upar).
**Symptom:** Try-catch daala tha render ke around — error: `ReferenceError: _tot is not defined`.

### BUG #5 — `</tfoot>` without `<tfoot>` opener
**Root cause:** TOTAL row add karte waqt `</tbody></table>` ko `</tr></tfoot></table>` se replace kiya, but `<tfoot>` kabhi open nahi hua. Browser silently ignore karta hai malformed HTML.
**Fix:** `tfoot` open + close dono properly kiye.

### BUG #6 — `_patchLtpCells()` not called in no-positions branch
**Root cause:** Jab koi open position nahi hoti, ek branch `return` kar jaata tha bina `_patchLtpCells()` call kiye. NET panel tiles (REALIZED/UNREALIZED/NET TODAY) "—" dikha rahe the.
**Fix:** No-positions branch mein bhi `_patchLtpCells()` call karo.

### OPEN POSITIONS TOTAL ROW
**Kya bana:** Completed trades wali `<tfoot>` TOTAL row pattern open positions table mein bhi lagai. Pehle ek flex div tha jo columns se align nahi hota tha.
**How:** Per-strategy group ke end mein `<tfoot><tr>` banao. `activeOpenCols` ke har column ke liye `text-align` decide karo (right: entry_px/ltp/points/pnl/ret_pct/margin/run_up/run_down, center: entry_time/qty/chart/actions). `qty` aur `margin` sum show karo.

### LESSON: DB file name trap
**Real file:** `trades.db` (not `orders.db`). Table name: `orders`. Columns: id, ts, date, source, strategy, mode, broker, symbol, instrument, trad_sym, sec_id, segment, side, qty, price, correlation_id, broker_order_id, status, tags, product_type, group_id.
**Status values in DB:** `"COMPLETE"` / `"filled"` (closed), `"OPEN"` (live open position), `"paper"` (paper filled), `"rejected"` / `"cancelled"` / `"failed"` (dead — skip from netting).

### LESSON: Try-catch silently kills progress
**Problem:** `statsMetricsRender` ka `catch(e){ /* ignore */ }` aisi errors swallow karta tha jo pills update se pehle throw hoti. Debugging impossible.
**Fix rule:** Production code mein bhi `catch(e){ console.error('[context] error:', e); }` likho. `/* ignore */` kabhi mat karo — at least console me dikhao.

---

## 2026-06-23 — Script Library: paste-and-run custom strategies (TradingView-style)
**Status:** DONE (local build + backend verified) — VPS deploy PENDING (market open; do off-market)
**Kya:** "📌 Pine" tab → "📜 Script" library banao jisme Pine + Python + DSL-rule versions save hon. Koi bhi conforming Python/DSL script ko backtest dropdown se runnable banao (Pine reference-only). Plus ek master-prompt + contract doc jo kisi bhi AI ko de do to woh hamare syntax me code likhe.
**Layer:** strategy, ui, config
**Files:** `_TOOLS/backtest_engine.py` (new `_run_custom` generic dynamic-import runner + `_eval_loop` + dispatch in `run_backtest`), `trader_dashboard.py` (`/api/pine/save` lang+snapshot-ext+python→`strategies/<id>.py`+dsl→cfg parse, `_parse_dsl_block` helper, `api_backtest_run` dynamic dispatch), `templates/index.html` (Script rename, lang pills+auto-detect+confirm, file upload, Lang badge, Master-Prompt modal), `templates/backtest_chart.html` (skip `_`-keys in edit modal; dropdown auto-lists new config keys — free), `strategies/SCRIPT_CONTRACT.md` (NEW — DSL+Python spec + master prompt), `strategies/custom_rule_engine.py` (exists local, VPS pe deploy)
**Kyun:** User ko TradingView/QuantMan jaisa flow chahiye — ek file paste/upload → version-history library → dropdown → backtest. Abhi har strategy hardcoded (`_RUNNERS` + manual `strategies/*.py`). custom_rule_engine local pe bana tha, VPS pe missing.
**Reuse:** `custom_rule_engine._run_bb` (DSL exec), `_run_ema`/`_run_vwap_ema` patterns (eval-loop / backtest-call), data loaders (`ensure_and_load_symbol`/equity loaders/`_cfg_symbol`/`_fill`/`TF_MIN`), generic Edit modal + `collectModalFields` (already a key=value editor), `/api/config`-driven dropdown (auto-lists any nifty_config key)
**Depends on:** nothing (backtest cached data; no live Dhan)
**Build:** LOCAL first (VPS pe live trading — undisturbed), verify, phir off-market SCP deploy
**Verified (local, port 5098, NIFTY Apr-2026 cached):** python save → id `user_<slug>_v1` + `strategies/<id>.py` + nifty_config `{_module,_lang,active:false}`; dsl save → parsed `entry_long/exit_long/bb_window/sl_pct` + `_lang:dsl`; pine save → name from `strategy("...")`, NO script_id (reference-only); backtest python script = 1507 candles/36 trades/+904 pts; dsl script = 23 trades; delete cleans config+`.py`+snapshots. Frontend (Script tab pills/upload/Master-Prompt modal, lang badge, deep-link Run) = needs user's visual check after restart.
**Known caveat (follow-up):** evaluate() path recomputes indicators per-bar (O(n²)) — ~1-2 min for ~1500 bars over a buffered month; fine for 5m/short ranges, slow for 1m/multi-month. backtest(df,cfg) path (vectorized) avoids it.
**Pending:** off-market VPS deploy (incl. `strategies/custom_rule_engine.py` which is MISSING on VPS) + restart `algo-dashboard`; `sync_pine.py` extend for `.py`/`.rules` snapshots + `strategies/user_*.py`.

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

## 2026-06-22 — Dashboard: Orders+P&L merge, Quick Order CE/PE fix, dates+charts
**Status:** DONE
**Kya:**
- #3 Quick Order CE/PE confusion fix — ab CE/PE explicitly select hota hai (tick swatch), BUY/SELL usi selected leg pe chalta hai. Pehle hardcoded tha (BUY→PE, SELL→CE). Backend `api_manual_order` ab `opt_type` request se leta hai (legacy fallback retained).
- #4 P&L tab ko Orders tab me fold kiya (P&L tab + col-modal markup hata). 📒 Orders & P&L ab per-strategy summary pills + Gross/Tax(Zerodha charges)/Net columns dikhata hai. `calcCharges()` reuse.
- #2 Completed Trades me Date column; Open Positions me 📈 chart button (entry kahan hua). `order_store.trades_for` ab entry_date/exit_date deta hai. `openTradeChart()` optional date param leta hai.
- Orders tab 4s auto-refresh (DB-backed, no Dhan) — P&L ki jagah.
**Layer:** ui / data
**Files:** `templates/index.html`, `trader_dashboard.py`, `order_store.py`
**Kyun:** User feedback — quick order galat leg le raha tha; do tab same kaam; trade date + open-position entry visibility chahiye thi
**Pending:** #5 webhook reversal bug (TV reverse karta, Python `_do_entry` "position already open" pe block karke purani pakde rehta) — baad me. Validated: Jinja render + node --check + py_compile sab OK. VPS deploy pending.

## 2026-06-22 — Close zero-price fix, two-pass netting, phantom-position handling, RSI→order_store
**Status:** DONE
**Kya:**
- **Zero-price close fix** — `api_close_position` me `option_ltp` default 0.0; LTP fetch fail/429 pe close 0.00 record ho raha tha (SELL@71→exit@0 = jhootha profit). Ab 3x retry; na mile to record NAHI karta, error deta. Same `_dhan_live_fate()` verify manual-order + close dono me — Dhan 200=accepted (filled nahi); REJECTED ko phantom position nahi banata.
- **Two-pass netting** (`order_store.trades_for`) — Pass1: exact (source,strategy,trad_sym) round-trips; Pass2: bache opposite legs ko (mode,trad_sym) FIFO net (manual BUY se webhook/strategy SELL bhi close hoti). Rejected/cancelled/failed legs netting se excluded.
- **Phantom clear** — `/api/orders/book-close` + 🗑 button: stuck/phantom position ko ledger se hatao (offsetting leg @ entry price, pnl0, no real order).
- **RSI → order_store** — `_TRADERS/rsi_trader.py` ab entry + RSI-exit + 3:15-exit pe `order_store.record()` karta hai (source='strategy'), **actual option premium fetch karke** (`_opt_ltp`, pehle sirf underlying close logs hota tha). Isse RSI trades 'Orders & P&L' tab me dikhenge (range_trader/webhook ki tarah). ema/universe inactive — chhoda.
**Layer:** ui / data / strategy-engine
**Files:** `trader_dashboard.py`, `order_store.py`, `templates/index.html`, `_TRADERS/rsi_trader.py`
**Kyun:** User-reported: close zero price, manual close net nahi hota, live phantom positions, strategy entries P&L tab me nahi
**Verify:** RSI order_store recording next market session (9:10) pe live confirm hoga — abhi market band.

## 2026-06-22 — Webhook reversal fix (#5) + zero-leg data cleanup
**Status:** DONE
**Kya:**
- **Webhook reversal** (`webhook_executor._do_entry`) — pehle "position already open" pe naya ENTRY block hota tha → TV reverse karta, Python purani pakde rehta. Ab: opposite-direction ENTRY = REVERSAL (purani exit → nayi enter, atomic: exit fail to entry nahi), same-direction = ignore (pyramiding off). Pine unchanged (ek alert, Python reconcile). Unit-tested: buy→LONG, sell→reverse→SHORT, sell→ignored, 2 trades.
- **Zero-leg cleanup** — aaj ke 2 corrupt @0 close legs (24150-CE webhook, 24000-CE manual) ko Dhan intraday se asli exit premium (₹62.80 / ₹163.95) set kiya (one-off `_fix_zero_legs.py`, DB backup leke, script delete). Fake ~₹15,343 profit hata.
**Files:** `webhook_executor.py`
**Verify:** reversal unit-test pass + VPS deploy OK. Live reversal kal market me confirm hoga.

## 2026-07-01 — Peak-P&L day-rollover fix + critical pos_monitor_loop silent-break fix (datetime.utcnow() deprecation-commit fallout)
**Status:** DONE
**Kya:**
- **Peak-P&L stale-carryover fix** — user ne dekha ki naye din ki shuruaat me (zero trades) Peak/DD/30% floor purane din ke ₹7,916 dikha raha tha. Root cause: `pos_monitor_loop`'s day-rollover check apne hi abhi-abhi-likhe hue `peak_pnl_history.json` ka mtime check kar raha tha — hamesha "aaj" hi milta, reset kabhi fire nahi hota tha (process long-lived systemd service hai, roz restart nahi hota). Fix: naya module-level `_peak_day_str` explicit day-tracker, mtime-dependency hata di. TRAP #55.
- **CRITICAL — same restart ne ek alag, pehle se maujood bug expose kiya:** commit `3cbad3f` (isi session se ~10 min pehle, ek earlier session ne `datetime.utcnow()` deprecation fix kiya tha) ne 5 jagah imports galat kar diye the — `risk_gate.py` ke 3 functions (`_today_open`/`_today_realized_pnl`/`_strategy_day_pnl` — capital/daily-loss/concentration checks) NameError pe crash kar rahe the, aur `trader_dashboard.py` me `_trailing_lock_fired_today()` + trailing-lock flag-write ka `as _dtc` galti se `timezone` ko bind kar raha tha (`datetime` ko nahi) — ek jagah to poore `pos_monitor_loop` (SL/TP/EOD-squareoff wala loop) ko HAR CYCLE UnboundLocalError pe crash kara raha tha, bilkul silently (missing `flush=True` outer print pe). Dono bugs sirf isliye pehle nazar nahi aaye kyunki purana already-running process apna OLD in-memory code use kar raha tha — restart karte hi surface hue. Fix: sab 5 jagah sahi import wapas kiya. TRAP #56.
**Layer:** infra / broker / validation
**Files:** `trader_dashboard.py`, `risk_gate.py`
**Kyun:** User-reported cosmetic bug (peak P&L stale) → diagnosis ke dauraan ek zyada critical live-safety bug mil gaya (pos_monitor_loop poora silently down tha restart ke baad)
**Depends on:** nothing
**Verify:** VPS pe dono `algo-dashboard` + `algo-monitor` restart karke confirm kiya — `peak_pnl_history.json` ab clean ₹0 se start hota hai, koi error log nahi, `_trailing_lock_fired_today()` sahi kaam karta hai. Us waqt koi open position nahi thi (zero live-trading impact is baar).

## 2026-07-01 — Restart-risk scenario modeling → 2 preemptive fixes (mode-preservation + untracked-position scan)
**Status:** DONE
**Kya:** User ne pucha "restart bolun to kya toot sakta hai" — 4 restart-types (single strategy / algo-monitor / algo-dashboard / full VPS reboot) x 3 categories (data corruption / order flow / position mismatch) model kiya, code padh ke (guesswork nahi). 4 findings mile, user ne top-2 critical fix karne bola:
- **TRAP #57 — silent live→paper downgrade:** `auto_scheduler()` restart-recovery hardcoded `mode=paper` bhejta tha; `nifty_config.json` `mode` store hi nahi karta tha. Fix: `/api/start` ab `cfg[s]['mode']` bhi save karta hai; `auto_scheduler` usi ko read karke restore karta hai.
- **TRAP #58 — untracked live position:** `broker_sync.py` sirf ek direction check karta tha (DB-open-but-broker-flat). Ulta kabhi nahi (broker-open-but-DB-absent) — jo `smart_order.execute()`'s ~8s live-fill-poll window me SIGTERM (koi handler kahin nahi hai) se ban sakta hai. Fix: naya `broker_sync.untracked_scan_if_due()` — dono broker ke live positions seedhe poll karke order_store se diff karta hai; Dhan → auto-adopt (apna hi tradingSymbol/segment deta hai, guessing nahi), Kite → alert-only (reverse-symbol-guess nahi kiya, TRAP #13/#22 jaisa hi).
**Layer:** infra / broker / validation / risk-management
**Files:** `trader_dashboard.py`, `broker_sync.py`, `brokers/base_broker.py`, `brokers/dhan_broker.py`, `brokers/kite_broker.py`
**Kyun:** User khud VPS pe build karta hai (restart frequent) — preemptive modeling taaki live capital risk na aaye
**Depends on:** nothing
**Not done (user-scoped out for later):** #3 webhook profit-target lost on recovery (target:None), #4 nifty_config.json concurrent-write race (no atomic write)
**Verify:** VPS deploy + syntax-check + restart (no open positions) + confirmed clean logs, peak-pnl still healthy, zero errors in pos_monitor_loop for 2+ min post-restart. Untracked-scan correctly silent (nothing to find). Live "adopt a real orphan" path not yet exercised (no orphan existed to test against) — logic reviewed carefully but flagging as unverified-in-anger.

## 2026-07-01 — TRAP #58 live-confirmed same day + TRAP #59 (resolve_kite_symbol signature bug + quantity field bug) — real untracked position found and fixed while deploying
**Status:** DONE (detection + alerting) / PENDING (deeper structural fix, deferred)
**Kya:** ~1hr baad hi TRAP #58 ka mechanism REAL LIVE mila — user ne khud Zerodha screenshot bheja jisme RELIANCE-1310-CE (SELL 500) aur SUNPHARMA-1980-CE hedge (BUY 350) dashboard me nahi the lekin Zerodha me the. Root cause: restart nahi, **8-second fill-confirm timeout** — `smart_order.execute()` ne order place kiya, broker pe fill bhi ho gaya, lekin poll 8s me TRADED confirm nahi hua to `order_store.record()` kabhi chala hi nahi. Deploy karte waqt untracked-scan khud zero orphans dikha raha tha (jabki 2 the) — dusra bug mila: `resolve_kite_symbol(trad_sym)` sab jagah galat signature se call ho raha tha (real signature `(kite, trad_sym, sec_id)`, `kite` client missing) — TypeError silently swallow. Compounding: Kite field `net_quantity` nahi, `quantity` hai — dono `positions()` aur naya `positions_detailed()` galat field padh rahe the (hamesha 0). Teesra bug (verify ke dauraan mila): alert-writer `.get()` crash kar raha tha kyunki `downloader_alert.json` me mixed format (strings + dicts) hain — same bug `_write_naked_alert` (TRAP #53) me bhi tha, dono fix kiye.
**Layer:** broker / risk-management / validation
**Files:** `broker_sync.py`, `brokers/kite_broker.py`
**Kyun:** Live incident ke dauraan mila, turant fix + deploy kiya (RELIANCE position abhi bhi user ko manually handle karna pada — alert-only hai, auto-SL nahi)
**Depends on:** TRAP #58 (same-day, is session ka pehla fix)
**Not done (user ne deferred kiya, RELIANCE handle karne ke baad):** `smart_order.execute()` ka structural fix — order accept hote hi "pending" row likhna, phir confirm par update. Untracked-scan sirf after-the-fact catch karta hai; yeh fix root cause par lagta.
**Verify:** VPS pe deploy + restart, dry-run se independently detection verify kiya (restart se pehle), phir live confirm — dono orphans correctly UNTRACKED detect hue, alert successfully likha gaya (crash-fix ke baad). SUNPHARMA-1980 khud resolve ho gaya (position flat), RELIANCE abhi bhi open + untracked tha jab session yahan tak pahuncha.

## 2026-07-01 — TRAP #60: ghost-sync duplicate-exit feedback loop found live + fixed + full-day P&L reconciliation
**Status:** DONE
**Kya:** RELIANCE handle karne ke baad user ne poore din ke trades Zerodha se sync karne ko bola — reconciliation ke dauraan mila ki `broker_sync` har ~30s cycle MARUTI ke liye NAYA duplicate "exit" row bana raha tha, same stale price (₹388.00) baar-baar reuse karke — ek live, active feedback loop jo diagnosis ke dauraan bhi chalta raha (~20 phantom rows). Root cause do gaps ka combo: (1) `_net_rows()` Pass-1 pairing sirf simple side-alternation karta hai id-order me — odd-count same-key rows me last row hamesha "dangling open" reh jaata hai, chahe din genuinely flat ho chuka ho; (2) `_fetch_fills()` ek symbol ke SAARE fills ko EK dict entry me collapse kar deta tha (last-write-wins) — `_resolve_exit_price()` isliye har baar SAME purana fill price deta tha, koi tareeka nahi tha yeh janne ka ki "yeh fill pehle hi use ho chuka hai." Dono gaps mil ke loop banate the: TRAP #58/#59 ke fix ne (sahi resolve_kite_symbol + sahi quantity field) `_check_flat` ko PEHLI BAAR sahi se "haan flat hai" confirm karne diya — jo pehle silently fail hoti thi, ab har cycle trigger hone lagi, is pre-existing weakness ko expose karke.
**Fix (root):** `_fetch_fills()` ab har fill ka apna unique broker id (`trade_id`/Kite native, `exchangeTradeId`/`orderId` Dhan me naya add kiya) bhi carry karta hai. `_run_sync()` exit likhne se PEHLE `_fill_already_used(tid,...)` check karta hai (order_store ke `correlation_id` field se, jo schema me pehle se tha bas use nahi ho raha tha) — agar yeh fill pehle hi record ho chuka hai to skip (na duplicate write, na `mark_externally_closed` — dusra isliye bhi zaroori kyunki woh legitimate row ko P&L se hi hata deta, `_dead_filtered` ki wajah se).
**Manual data fix (aaj ke liye):** ~20 galat MARUTI rows delete karke, Zerodha ke real fills se 4 clean round-trips insert kiye. Poora din reconcile kiya (RELIANCE/SUNPHARMA-1880/SUNPHARMA-1980-hedge/MARUTI sab) — final total ₹1,827.50, Zerodha se exact match. `trades.db` backup liya har DELETE/INSERT se pehle.
**Layer:** broker / data-integrity / risk-management
**Files:** `broker_sync.py`, `brokers/dhan_broker.py`
**Kyun:** Live P&L corruption diagnosis ke dauraan mila — root cause fix taaki dobara na ho (kal ya kabhi bhi, 3+ round-trip wale din pe)
**Depends on:** TRAP #58/#59 (isi session, jinhone yeh pre-existing bug expose kiya)
**Verify:** Fix deploy + restart, 60s window me row-count stable (9, no new phantom) — 2 baar independently confirm kiya (before aur after root fix). Broker confirmed fully flat throughout.

## 2026-07-01 — TRAP #61 (mark_externally_closed unconditionally hid P&L entries) fixed + TRAP #62 (strategy state-desync causing real unintended orders) flagged
**Status:** DONE (#61) / PENDING (#62, user-flagged for later)
**Kya:** User ne khud pucha "yeh SUNPHARMA BUY kaise ho gaya, hamari strategy sirf SELL karti hai" — investigate karne pe do alag bugs mile. **TRAP #62 (root trigger):** account-level trailing-profit-lock (jo pura account squareoff karta hai) ne SUNPHARMA position 10:26 pe close kiya, lekin strategy process (`range_trader.py`) ki apni memory ko pata hi nahi chala — 40 min baad strategy ne "EXIT via ATR_TRAILING" samajh ke ek REAL BUY order bhej diya, jabki koi position band karne layak thi hi nahi (fresh unwanted long entry, ~₹122.50 cost). **TRAP #61 (display bug jo isse expose hua):** jab user ne is phantom BUY ko Zerodha pe manually close kiya, `broker_sync` ne sahi se exit record kiya (TRAP #60 ka fix sahi kaam kar raha tha) — lekin sath hi entry row ko `mark_externally_closed` bhi kar diya, jo P&L calculation se hi row hata deta hai (`_dead_filtered`) — isliye entry gayab ho gaya, exit akela "nayi open position" jaisa dikhne laga.
**Fix (#61 — done):** `mark_externally_closed` ab sirf tab call hota hai jab exit price hi nahi mila (genuinely kuch pair karne ko nahi). Jab exit sahi se record ho jaaye, entry ka status chhedo mat — normal netting khud pair kar leta hai.
**#62 abhi fix nahi hua** — user ne baad ke liye flag kiya. Do options note kiye: trailing-lock squareoff strategy process ko signal bheje, ya strategy apni state ko live periodically order_store se re-validate kare.
**Layer:** broker / strategy-engine / data-integrity
**Files:** `broker_sync.py`
**Kyun:** User ne khud confusion flag kiya ("strategy sirf sell karti hai, yeh buy kaise") — investigate karke 2 real bugs mile
**Depends on:** TRAP #60 (isi session — jiske baad yeh path pehli baar cleanly exercise hua)
**Verify:** Row 378 ka status fix karke turant sahi pair hua (BUY 28.60→SELL 28.25, pnl -122.50). Poore din ka total ab ₹1,705.00 — Zerodha ke apne "Total P&L" se EXACT match. Deploy + restart clean, 10 completed trades, open:[].

## 2026-07-01 — TRAP #63: TRAP #58 ka root cause fix — order_store row broker-accept pe hi likho, fill-confirm ke baad nahi
**Status:** DONE
**Kya:** Aaj hi 4 baar (RELIANCE, SUNPHARMA-hedge, HINDUNILVR, aur implicitly MARUTI ke through bhi) same gap dikha — `smart_order.execute()` sirf TRADED confirm hone ke baad hi order_store me likhta tha, aur 8 second (5×1.5s) baad haar maan leta tha. User ne pucha "yeh baar-baar kyun ho raha hai" — jawaab: saare affected symbols (RELIANCE/SUNPHARMA/MARUTI/HINDUNILVR) STOCK options hain, NIFTY jaise liquid nahi — inka fill-confirm aksar 8 sec se zyada leta hai, chahe fill genuinely ho chuka ho.
**Fix:** Order broker accept karte hi (poll shuru hone se PEHLE) ek "provisional" row likh do (best-guess price, `UNCONFIRMED_FILL` tag). Confirm TRADED → price sahi karo, tag hatao. Confirm REJECTED → status='rejected' (P&L se sahi exclude). Timeout → row waise hi chhodo — already protected hai (pos_monitor_loop SL/EOD laga dega), aur agar genuinely fill nahi hua to broker_sync khud clean kar dega (TRAP #61 ka no-price-branch).
**Layer:** broker / risk-management / order-flow
**Files:** `smart_order.py`, `order_store.py` (naya `update_fill()` function)
**Kyun:** User ne khud pucha "kyun baar-baar ho raha hai" — root fix maangi
**Depends on:** TRAP #58 (jisne yeh gap pehli baar identify kiya), TRAP #61 (jiska no-price branch iske timeout-case ko safely clean karta hai)
**Verify:** Deploy karte waqt ek REAL open MARUTI position thi (protected) — `ARS_CHAIN_V1` strategy process restart karna zaroori tha (Python `smart_order.py` hot-reload nahi karta), `_recover_state_from_order_store` (TRAP #28) se position sahi recover hui, zero protection-gap. Restart ke baad dashboard pe MARUTI abhi bhi correctly tracked confirmed.

## 2026-07-01 — TRAP #63 follow-up: delayed-fill monitoring log (user-requested — "is state ko track karo, data baad me kaam aayega")
**Status:** DONE
**Kya:** User ne 2 sawaal pucho: (1) 15-min-delay jaisa extreme case ho to SL protection turant lagti hai ya confirm ka wait karti hai — confirm kiya: turant lagti hai, kyunki provisional row apne SL tags ke saath hi likha jaata hai. (2) TRAP #63 ka fix delay resolve hone pe `UNCONFIRMED_FILL` tag hata deta hai — matlab "yeh delay hua tha" ka record kho jaata hai. User ne isko track karne ko bola.
**Fix:** Naya append-only log `data/fill_confirm_delays.json` — har live order jiska fill-confirm poll 1 se zyada attempt le (symbol, side, qty, attempted price, order_id, attempts, resolution: confirmed/rejected/timeout). Dashboard route `/api/fill-delays` (optional `?symbol=` filter) se dekho.
**Layer:** broker / monitoring
**Files:** `smart_order.py`, `trader_dashboard.py`
**Kyun:** User ne khud pucha "is data ko rakh lo, baad me kaam aayega"
**Depends on:** TRAP #63 (isi session)
**Verify:** Deploy + restart clean (`ARS_CHAIN_V1` + dashboard/monitor dono), koi open position nahi thi. Route `/api/fill-delays` test kiya — `[]` return kar raha (abhi tak koi delayed fill nahi hui, expected).

## 2026-07-01 — UI: Completed Trades "Group by Symbol" (Zerodha Day's History jaisa)
**Status:** DONE
**Kya:** User ne Zerodha ke "Day's history" table ka screenshot dikhaya — per-symbol grouped totals, expand karke individual trades. Wahi feature app ke "Completed Trades" table me bhi maanga.
**Fix:** Naya "📁 Group by Symbol" toggle button (Completed Trades header). OFF = purana flat view (byte-identical, refactor-only). ON = per-symbol summary row (points/gross/tax/net total + trade count), click karke expand/collapse — individual trades neeche sub-rows me dikhte hain. Row-rendering logic ek reusable function `_completedRowHtml()` me nikaala (pehle forEach ke andar duplicate tha) taaki flat aur grouped dono modes same code use karein — zero drift risk.
**Layer:** ui
**Files:** `templates/index.html`
**Kyun:** User-requested UI parity with Zerodha's own trade history view
**Depends on:** nothing
**Verify:** JS syntax `node --check` se verify kiya (Jinja tags strip karke). Deploy kiya (Flask templates auto-reload — koi service restart nahi lagi, `curl` se 200 OK + naya button HTML confirm kiya). Visual/interaction testing user ne khud browser me karna hai (is session me direct browser access nahi tha).

## 2026-07-01 — Full-day live incident investigation (Zerodha CSV se) → TRAP #64 order-chasing + shadow-live/paper-mode clarify
**Status:** DONE (order-chasing) / user ke apne actions clarify hue (shadow-live, paper mode, NIFTY manual trade)
**Kya:** User ne poore din ka Zerodha orders CSV diya — TITAN aur ICICIBANK ke orders 8-second wait ke baad bhi minutes tak broker pe unfilled reh rahe the (TITAN ~4.5 min baad khud fill hua; ICICIBANK kabhi fill hi nahi hua, user ne haath se cancel kiya). 30% trailing-lock din me 3 baar fire hua (12:00, 12:05, 13:41) — design ke mutabik kaam kar raha hai, lekin user ne pucha kya per-instrument lagana better hoga (design question, abhi decide nahi hua). Shadow-Live ON mila (user ne khud on kiya tha bhool se, ab OFF kar diya maine seedha config me — UI se save nahi hua tha). Paper mode (12:41 se) user ka apna intentional choice tha.
**Fix:** User ka idea implement kiya — order agar poll ke baad bhi fill nahi hua, cancel karke fresh price pe re-place karo (max 2 chase, 3 total attempts, ~24s max). Naya `BaseBroker.cancel_order()` (Dhan `DELETE /v2/orders/{id}`, Kite `cancel_order()`). Provisional row (TRAP #63) ka price + broker_order_id har chase round update hota hai.
**Layer:** broker / order-flow
**Files:** `smart_order.py`, `order_store.py`, `brokers/base_broker.py`, `brokers/dhan_broker.py`, `brokers/kite_broker.py`
**Kyun:** User ne khud suggest kiya jab dekha orders manually cancel karne pad rahe the
**Depends on:** TRAP #63 (isi session)
**Not done:** ICICIBANK phantom row (id 386) abhi bhi order_store me "open" dikha raha hai — real risk nahi hai (kabhi fill hi nahi hua), lekin cleanup pending. 30% ceiling per-instrument vs account-level — user decide karenge.
**Verify:** Syntax check + deploy + restart (paper mode me, safe waqt), clean startup confirmed, koi error nahi.

## 2026-07-01 — TRAP #65: liquidity filter poore din data hi nahi paa raha tha — `dhan_feed.start()` kabhi call hi nahi hua
**Status:** DONE (root fix) / PENDING (cold-start proactive-subscribe, REST fallback hardening)
**Kya:** User ne pucha "hamare paas to 2/3 wala liquidity filter hai, TITAN/ICICI illiquid hone ke bawajood kaise chal gaye?" — check kiya to poore din ke SAARE 13 `[LIQUIDITY]` log lines mein se HAR EK "no live market-depth data — failing OPEN" tha. Ek bhi real data check nahi hua. Root cause: `range_trader.py` sirf `dhan_feed.add()` call karta tha, `dhan_feed.start()` KABHI nahi — `add()` sirf tabhi kaam karta hai jab feed ka background thread already chal raha ho; woh thread sirf `start()` create karta hai. Isliye `dhan_feed.LIVE` is process me hamesha khaali raha (confirmed: size 0), aur har liquidity check REST fallback pe gira — jo khud test karne pe theek kaam kiya (direct call se real data mila) lekin real-time load me consistently fail ho raha tha.
**Fix:** `range_trader.py`'s `main()` ab startup pe `dhan_feed.start({client_id, jwt_token}, [])` call karta hai (same pattern jo `trader_dashboard.py`'s `_ensure_feed_started()` use karta hai) — ab feed genuinely connect hoga.
**Layer:** broker / risk-management
**Files:** `_TRADERS/range_trader.py`
**Kyun:** User ka sawaal ("filter kaam kyun nahi kiya") — investigate karke root cause mila
**Depends on:** nothing
**Not done:** din ki shuruaat me saare universe symbols ko proactively subscribe karna (abhi sirf reactive, signal aane pe) — pehla signal cold-start rahega. REST fallback ki reliability under load bhi harden karni chahiye shayad.
**Verify:** Deploy + restart clean, `[startup] dhan_feed started` log confirm hua. Real signal ka wait chal raha hai final confirmation ke liye (`[LIQUIDITY]` line real data ke saath aani chahiye ab).

## 2026-07-01 — Exit-side order-chasing (TRAP #64 follow-up) + phantom ICICI row cleanup
**Status:** DONE
**Kya:** User: entries conservative reh sakte hain (price bhaag jaye to skip), par exits ko zyada aggressive hona chahiye — loss badhta jaye aur price hi na mile aisa nahi hona chahiye. `smart_order.execute()` ab `is_exit` param leta hai — exits ko 4 chase rounds milte hain (entries 2 hi), aur har round LIMIT price ko spread ke aur andar cross karta hai (buffer double hota hai round pe, 150bps tak cap) — kabhi MARKET order nahi (Zerodha stock-options pe MARKET reject karta hai, sirf LIMIT allow hai). Saare automated exit call-sites (`range_trader` EOD+EXIT, `webhook_executor._do_exit`, `universe_trader` 3 exits, `trader_dashboard` trailing-lock+`_do_squareoff`) ko `is_exit=True` diya. Saath hi ICICIBANK ka ek phantom row (id 386, BUY 1330-PE @9.85, UNCONFIRMED_FILL) mila — Kite `order_history()` se verify kiya ki woh order CANCELLED hua tha (filled_quantity=0, kabhi fill nahi hua) — safe delete, DB backup lekar.
**Layer:** broker / risk-management
**Files:** `smart_order.py`, `_TRADERS/range_trader.py`, `_TRADERS/universe_trader.py`, `webhook_executor.py`, `trader_dashboard.py`
**Kyun:** User ne live TITAN/ICICIBANK stuck-order incident ke baad flexibility maangi
**Depends on:** TRAP #64 (order-chasing base)
**Verify:** Deploy + restart, ARS_CHAIN_V1 clean startup, koi open positions disturb nahi hui.

## 2026-07-01 — Broker Balances "Cash" label fix (TRAP #66)
**Status:** DONE
**Kya:** User: "Zerodha se mera balance app ka match nahi kar raha." 💰 Broker Balances card "Cash" line `b.available` dikha raha tha — Kite ke liye yeh total available MARGIN hai (cash + pledged collateral − used), asli cash nahi. Real cash (`b.cash`) already API se aa raha tha, bas UI mein kabhi use nahi hua. Fix: Cash ab `b.cash` dikhata hai, naya "Available Margin" line `b.available` ke liye add kiya.
**Layer:** UI / broker
**Files:** `templates/index.html`
**Kyun:** User ka mismatch report
**Depends on:** nothing
**Verify:** Deploy + dashboard restart, curl se "Available Margin" text confirm hua page pe.

## 2026-07-01 — Manual-trade broker reconciliation (🧾 Reconcile vs Broker) — built, broke, fixed (TRAP #67, #68, #69)
**Status:** DONE
**Kya:** User: Zerodha hi actual source of truth hai (app sirf order punch karti hai) — chahte hain ki Zerodha ke real fills se app automatically match ho jaye, jo trade app ne nahi kiya (manual) wo tag ke saath dikhe. Naya `broker_sync.reconcile_manual_trades()` (button-triggered, `/api/reconcile-manual-trades` route, "🧾 Reconcile vs Broker" button Completed Trades ke paas) — Kite ke `trades()` se aaj ke saare real fills leta hai, `order_store` se match karta hai, jo match nahi hota use `source=manual` tag ke saath insert kar deta hai.
**Pehla version TOOTA:** matching sirf `broker_order_id` (order id) se ki thi — kuch purani rows (isi session ki earlier manual TRAP#60/61 cleanup se) ka broker_order_id kabhi populate hi nahi hua tha, to unhe "unmatched" samajh ke 32 DUPLICATE rows insert kar diye. User ne turant screenshot se pakda ("gross balance alag hai"). Surgical DELETE se (`correlation_id LIKE 'MANUAL_TID_%'`) saaf kiya — poori DB restore nahi ki (WAL-mode backup incomplete nikla, restore karta to 19 real rows kho jaate — TRAP #68).
**Fix (v2):** matching ab SIGNATURE+COUNT se — `(root_symbol, strike, CE/PE, side, qty, price)` normalize karke, jitne broker ke fills us signature ke utne hi order_store rows hone chahiye; farak > 0 to utne hi naye insert karo. Ye kisi bhi purani row ke broker_order_id missing hone se safe hai, aur khud-idempotent hai. Dry-run script se pehle verify kiya (12 genuinely-missing fills mile, 0 false-positive dupes) tab jaake live chalaya.
**Deploy gotcha:** ek multi-file scp mein `templates/index.html` silently deploy nahi hua tha (koi error nahi dikha) — user ne button missing dekha, mtime check se pakda, alag se re-deploy kiya (TRAP #69).
**Layer:** broker / data-integrity / UI
**Files:** `broker_sync.py`, `trader_dashboard.py`, `templates/index.html`
**Kyun:** User ka reconciliation automation ka ask
**Depends on:** nothing
**Verify:** Signature-based version live-run kiya — 12 manual trades insert hue (HINDUNILVR, NIFTY x2, NESTLEIND), per-instrument Zerodha se exact match (HINDUNILVR -225, NIFTY -1215.50, NIFTY -1186.25). Live-mode total ab ₹-2,389.25 vs Zerodha ₹-2,426.75 (₹37.50 ka chhota gap bacha, NESTLEIND-1450 pe — further check pending).

## 2026-07-01 — Alternate History / Counterfactual feature — rewritten, then REMOVED entirely (user decision)
**Status:** DONE (removal)
**Kya:** Same-session follow-up: user ne "Alternate History" table (duplicate Algo+Panic rows same trade ke liye) dikhaya. Root cause counterfactual.py's dual-source design tha (order_store + separate Kite raw-fill refetch, same real trade dono jagah se aa raha tha — TRAP #67 wali exact same class of bug, ek level upar). Pehle isko theek kiya: `order_store._net_rows()`'s `_complete()` mein `exit_source`/`exit_strategy` add kiya (entry aur exit ka origin independently pata chale), `counterfactual.analyze()` ko poora order_store-only (single source) pe rewrite kiya, table mein per-leg 🤖Algo/👤Manual chip + price dikhaya, chart hover tooltips (price+instrument), bottom-label overlap bhi fix kiya. Live-verify kiya — duplicate rows gaye, numbers sahi (algo ₹1337.50, manual -₹3726.75, panic ₹0). **User phir bhi bola "ye eakdum bekar hai, pura hata do"** — feature hi hata diya: `counterfactual.py` delete, `/api/counterfactual` + `/api/kite-csv-upload` routes hate, poora "🔄 Alternate History" card + JS (`loadCounterfactual()`) UI se nikala, `order_store.py`'s `exit_source`/`exit_strategy` bhi revert kiya (sirf isi feature ke liye tha, koi aur consumer nahi).
**Layer:** UI / broker / data-integrity
**Files:** `counterfactual.py` (deleted), `trader_dashboard.py`, `templates/index.html`, `order_store.py`
**Kyun:** User ka explicit "hata do" — feature trust nahi bana paya, complexity uske value se zyada thi
**Depends on:** nothing (Completed Trades' apna manual-trade tagging aur "🧾 Reconcile vs Broker" button untouched hai — wo alag feature hai, iska hissa nahi)
**Verify:** Deploy + restart, `/api/counterfactual` 404, koi UI trace nahi bacha, baaki dashboard (Completed Trades, Open Positions) normal chal raha.
