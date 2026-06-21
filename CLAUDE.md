# CODE3B — Algo Trader (EMA + RSI + Range)

## Project Ka Kaam
Multi-strategy live/paper algo trader jo Dhan API pe orders deta hai.
Web dashboard se control hota hai — koi command line nahi.

## Files

> **📘 Pine→Python validation? PEHLE YEH PADHO:** `ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md`
> — reusable methodology + all findings (fill convention, Wilder ATR, pyramiding=0,
> consistent-log export, etc.). Pichli baar pura din laga; yeh playbook se turant pick hoga.
> Tool: `validate_strategy.py` (`--signals <log>` to score, `--debug DATE` to trace).
> Best result: **90.2% exact / 93% entry** (Range Chain vs TradingView, NIFTY).

| File | Kaam |
|------|------|
| `nifty_ema_trader.py` | EMA crossover strategy |
| `validate_strategy.py` | TV Pine vs engine matching harness — `--signals`, `--debug`, HTML report |
| `_TOOLS/backtest_engine.py` | **Generic date-range backtester — any strategy (range/rsi/ema)**. Dashboard "📊 Backtest" button in Run modal → `POST /api/backtest/run` (strategy, date_from, date_to, optional TV log file) → candles + python trades + (if TV log given) accuracy %. Renders on TradingView-style chart (lightweight-charts CDN) with PY (blue) vs TV (orange) markers. |
| `rsi_trader.py` | RSI overbought/oversold strategy |
| `range_trader.py` | Range breakout/zone strategy with advanced exits (ATR, Fib) |
| `trader_dashboard.py` | Flask web UI — port 5099, Backend API, process manager |
| `dhan_master.py` | Daily dhan scrip master download + Option contract resolver |
| `save_daily_summary.py` | Aaj ki P&L ko `results/` mein save karo |
| `deploy_vps.py` | SCP se VPS pe push + dashboard restart |
| `templates/index.html`| Dynamic grid UI for configuration and dashboard |
| `mfe_routes.py` | MFE/MAE analysis routes — trade_log CRUD + analyse + generate-backtest |
| `generate_june_mfe.py` | Backtest→trade_log pipeline (NIFTY 1-min → Range Chain → ATM option bars) |
| `auto_data_downloader.py` | VPS daemon — polls Dhan orders → downloads OHLC bars → gap detection → dashboard banner |
| `webhook_executor.py` | **TradingView webhook → Dhan order engine.** `handle_signal` (ENTRY/EXIT), monitor (trailing SL premium/index, target, 3:15 squareoff), dedup, max-trades/day, Pine-alert overrides. Config `webhook_v1` in nifty_config.json |
| `sync_pine.py` | Pine version store (📌 Pine tab: versions.json + v{N}.pine + v{N}_imgs/) ko local ↔ VPS union-merge sync — kabhi version drop nahi. Run: `python sync_pine.py` |
| `_CHARTING/patterns.py` | Candle pattern detection (hammer/engulfing/harami) — shared, reusable across all strategies |
| `_CHARTING/zones.py` | Pivot/key-level builder + chart-renderable zone shapes (price lines, zone boxes) |
| `_CHARTING/indicators.py` | Library-backed indicator calc (`ta` package — EMA/RSI/ATR/VWAP/SMA/BBANDS) + registry driving the dashboard's no-code "Add Indicator" picker |
| `_CHARTING/plot_spec.py` | Pure JSON shaper — turns indicators/zones/pattern tags into the `plot_spec` the chart renders |

## VPS Info

- **IP:** `72.61.173.32` (Hostinger)
- **User:** `root`
- **Project dir:** `/root/CODE3B- TV BACKTEST ENGINE/`  *(dir name me space hai — scp/ssh me quote karo. Purana `/root/code4` ab nahi hai.)*
- **venv:** `/root/CODE3B- TV BACKTEST ENGINE/venv/bin/python`
- **SSH Key:** `C:\Users\arsal\.ssh\khazana_ed25519`
- **Dashboard:** `http://72.61.173.32:5099`
- **Service:** `algo-dashboard` (systemd, auto-start on reboot)

## Deploy Karna

> ⚠️ `deploy_vps.py` abhi **STALE** hai (2026-06-21): REMOTE_DIR `/root/code4` galat,
> FILES me root-level trader files hain jo ab `_TRADERS/` me hain, aur spaced dir ke liye
> scp/ssh quoting nahi. Fix hone tak **manual SCP** use karo (dir name quote karke):

```bash
# dir name me space hai — SFTP scp literal path leta hai, bas quote karo:
scp -i "C:/Users/arsal/.ssh/khazana_ed25519" -o StrictHostKeyChecking=no <file> "root@72.61.173.32:/root/CODE3B- TV BACKTEST ENGINE/<file>"
ssh -i "C:/Users/arsal/.ssh/khazana_ed25519" root@72.61.173.32 "systemctl restart algo-dashboard"
```

## Strategy Variations & Options Trading

Ab system mein aap ek strategy ke **multiple variations** (jaise `ema_v1`, `ema_v2`, `rsi_v1`) alag alag configurations ke saath ek hi waqt pe chala sakte hain. Har variation ek isolated Python process ki tarah chalta hai.

- **Options Support**: Config grid mein Instrument = "Options" select karke Strike Offset (-3 to +3) de sakte hain.
- **PE/CE Selling**: Agar Long signal aata hai toh PE sell hoga, Short signal pe CE sell hoga. `dhan_master.py` dynamically live ATM strike calculate karke scrip nikalta hai.
- **Config Storage**: Saari configs ek single file `nifty_config.json` mein store hoti hain under keys like `ema_v1`, `range_v2`.

## Data Storage

```
/root/CODE3B- TV BACKTEST ENGINE/   ← VPS pe (purana /root/code4 nahi)
├── logs/                 ← Har variation ki separate log file (e.g. ema_v1.log)
├── nifty_config.json     ← All configurations (hot-reload)
├── data/                 
│   ├── config.json       ← Dhan JWT + client_id (SECRET — gitignored)
│   └── api-scrip-master.csv ← Dhan Options symbols list
└── results/
    ├── YYYY-MM-DD.txt    ← Daily P&L summary
    └── master_log.json   ← All-time P&L history
```

## Critical Rules — Kabhi Mat Bhoolna

### 1. IPv4 Force (DH-905 fix)
VPS pe IPv6 default hoti hai — Dhan reject karta hai. Har trader file ke top mein yeh hona CHAHIYE:
```python
import socket
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4
```

### 2. Candles — Dhan intraday API only
All candle data comes from Dhan `/v2/charts/intraday` (sec_id from `dhan_master.get_equity_info()`).
Index spot price from Dhan `/v2/marketfeed/ltp` with `IDX_I` segment (sec_id 13=NIFTY, 25=BANKNIFTY).

### 3. correlationId = strategy prefix
Har order ka ek specific correlation ID hota hai jisme strategy version prefix hota hai: `EMA_V1_NIFTY_<timestamp>`.
Issey dashboard P&L parse kar pata hai.

### 4. Dhan token — rozana update
JWT token har 24 ghante mein expire hota hai.
Dashboard ke **Control tab** mein token paste karo → Save.

### 5. Auto Scheduler
Subah 9:10 par `trader_dashboard.py` apne aap saare active variations ko paper mode mein start kar deta hai, aur 15:30 par stop.

## Update Log

| Date | Kya bana |
|------|----------|
| 2026-06-19 | Run modal — Start/Stop merged into single toggle button (`runModalToggle()`). Added "📊 Backtest" section: date range + optional TV log upload → `_TOOLS/backtest_engine.py` (generic replay for range/rsi/ema, reuses `validate_strategy.backtest_day`/`parse_log`) → `/api/backtest/run` → TradingView-style candlestick chart (lightweight-charts CDN) with PY vs TV entry/exit markers + match %. |
| 2026-06-16 | Init — EMA + RSI trader, tabbed dashboard, VPS deploy, GitHub backup |
| 2026-06-16 | Added Range Strategy, Auto-scheduler for 9:10 AM |
| 2026-06-16 | **Options Trading** added via `dhan_master.py` (Dynamic Strike Offset PE/CE Selling) |
| 2026-06-16 | **Multi-Instance (Variations)** added with dynamic Grid UI in Vanilla JS, separated logs and processes |
| 2026-06-17 | Stale entry fix — `run_signal_engine` ab `signal_bar` + `total_bars` return karta hai; main loop skip karta hai agar `(total_bars - sig_bar) > 2` (purana historical signal) |
| 2026-06-17 | TATAMOTORS removed from SYMBOLS (delisted — data unavailable) |
| 2026-06-17 | Options branch mein `price=price` fix — paper log ab 0.00 nahi dikhata |
| 2026-06-17 | Startup exit guard — `if st["position"] is None: continue` before EXIT handler (fake startup trades fix) |
| 2026-06-17 | `[CONFIG]` log line har loop pe — TF, Instrument, Qty, MaxTrades, FreshZoneOnly, Exit mode, Entry rules |
| 2026-06-17 | Log panel — Pause/Play scroll button added; config line 3 separate lines mein (Entry / Exit / Config) |
| 2026-06-17 | Quick Order floating panel — NIFTY/BANKNIFTY, lot size from scrip CSV (/api/lot-sizes), draggable, Paper/Live toggle, BUY/SELL |
| 2026-06-17 | /api/manual-order — direct requests.post to Dhan (not via place_order — was silently failing). IPv4 patch added to trader_dashboard.py top |
| 2026-06-17 | DH-905 fix — NFO_OPT -> NSE_FNO everywhere + IPv4 force in dashboard. Orders confirmed working live on Dhan |
| 2026-06-17 | P&L tab full rebuild — summary pills (net P&L per strategy), Open Positions full-width table, Completed Trades full-width table. Old per-strategy card grid removed. |
| 2026-06-17 | Open Positions — live LTP via Dhan `/v2/quotes` + scrip master sec_id lookup (`_get_sec_ids()` + `_sec_id_cache`). Shows Entry ₹, LTP, Points, Qty, Unrealized P&L. |
| 2026-06-17 | Position netting — `parse_pnl()` ab opposite-side same-symbol order ko auto-close treat karta hai (BUY → SELL = completed trade with P&L). Qty also captured from log line. |
| 2026-06-17 | Manual order price fix — `api_manual_order` ab index price nahi, **actual option LTP** log karta hai (Dhan `/v2/quotes` se pehle fetch karta hai). |
| 2026-06-17 | Close Position button — Open Positions har row pe SELL ✕ / BUY ✕ button. `/api/close-position` route: sec_id lookup → LTP fetch → log write (paper) ya Dhan order (live). Mode Quick Order panel ke Paper/Live toggle se sync. |
| 2026-06-17 | LTP endpoint `/v2/quotes` → `/v2/marketfeed/ltp` (quotes 404 deta tha). Quick Order widget: index price hata, sirf selected ATM CE/PE LTP; server-side cache (`_ltp_cache`, 3s TTL) DH-904 rate-limit se bachne ko; auto-refresh 3s. |
| 2026-06-17 | Quick Order — LIMIT order + manual price box: `qo-price` input (blank = LTP par bhejo), PE/CE box click → price autofill, `qoOrder` ab `order_type:'LIMIT'` + `price` bhejta hai. Backend `api_manual_order` LIMIT/price accept karta hai. Strike/symbol line bada+bright (12px `#adbac7`). |
| 2026-06-17 | **CRITICAL fix** — Open Positions LTP ⏳ atak raha tha. Wajah: same trad_sym (e.g. `NIFTY-Jun2026-24050-CE`) **multiple expiries** ko map karta hai (symbol me din nahi hota); CSV first-match = EXPIRED contract (no LTP). Fix: `dhan_master.get_sec_id_for_trad_sym()` — nearest NON-expired expiry resolve karta hai; `_get_sec_ids()` ab isse use karta hai. `dhan_master.py` deploy list me bhi add kiya. |
| 2026-06-17 | **BEST-IN-CLASS UNIVERSE SYSTEM** (Phases 0-3 done). Plan: `~/.claude/plans/dapper-yawning-waffle.md`. All data from Dhan API. |
| 2026-06-17 | Phase 0 — `brokers/`: `base_broker.py` + `dhan_broker.py` (Dhan Data API intraday candles real-time, IST +5:30 fix; REST quote/orders; funds) + `kite_broker.py` stub. `get_broker(name)` factory, config `"broker":"dhan"`. dhanhq 2.x: `dhanhq(DhanContext(cid,token))`. |
| 2026-06-17 | Phase 1 — `dhan_feed.py`: Dhan WebSocket **Full packet** (`MarketFeed`) → in-memory `LIVE` best bid/ask/LTP per sec_id. Single persistent conn (5000 instr). Marketable-limit ka price source. |
| 2026-06-17 | Phase 2 — `smart_order.py`: marketable-limit (BUY=ask, SELL=bid, tick 0.05). **Paper==Live**: always logs intended-fill `[PAPER]/[LIVE]` (P&L identical); live also fires real LIMIT + `[BROKER]` status line (shadow badge). |
| 2026-06-17 | Phase 3 — `universe.py` (Nifty-50 + sec_id/option resolvers, 48/50) + `strategies/` plugin (`base`, `sample_ema`, `always_buy`) + `universe_trader.py` (Nifty-50 scan, route equity/stock_option/index_option, caps, 3:15 exit, `--once` flag). Dashboard `STRATEGIES['universe']`. Config: `nifty_config.json`→`universe_v1`. Verified: 48-equity scan, caps stop at 5. |
| 2026-06-17 | Phase 4 DONE — `validate_strategy.py`: NIFTY 1-min→5-min, runs range_trader zone/ATR per-day (collects ALL trades), compares vs TradingView List-of-Trades CSV. TV next-bar-open fill, continuous ATR(14), tolerance diag. Data: `._TRADING DATA\Index\NIFTY`. Run: `python validate_strategy.py --csv <tv.csv> --to 2026-05-19`. |
| 2026-06-17 | Phase 5 (in progress) — Pine fidelity: TV fill convention, bullHarami/bearHarami, Zone Exit (MainExit ON), post-entry zone reset, max-cs entry filter, selectedLine RESISTANCE-priority, **exact candle patterns** (`AA_CandlePatterns`: wickRatio **2.5**, redHammer upperWick≤body, invRedHam lowerWick≤body — in `range_trader.py`). Score Jan06-May19 NIFTY: **entry-exact 44%, within-1bar 47%, exact entry+exit 27%**. |
| 2026-06-17 | **Phase 5 COMPLETE — validation 90.2% exact / 93% entry** (Range Chain vs TradingView, NIFTY). Methodology + ALL findings saved to `ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md` (read FIRST next time). Key fixes: TV next-bar fill, Wilder-RMA ATR, exact `AA_CandlePatterns`, pyramiding=0, current-bar zone touch, Not_on_Red/Gren_line, block ≥15:15 entries, full Dhan daily history, skip data-gap days. Biggest trap: List-of-Trades & zone-log were from DIFFERENT runs → use ONE consistent `log.info` export (`Ars_Auto_Rev_Chain_ZONELOG.pine` logs ZONE+SIGNAL+EXIT). Tools: `validate_strategy.py --signals <log>` (score) / `--debug DATE` (trace) / HTML report. Pivots+data verified match TV to ~1pt. Remaining ~3 trades = gap-day level micro-edges (diminishing returns). |
| 2026-06-17 | **Pending:** Phase 6 live (small qty, market hours), UI polish (universe config tab, shadow badge, Quick Order bid/ask). |
| 2026-06-18 | MFE/MAE analysis tab — `mfe_routes.py`: per-trade runup/DD (premium pts + ₹ + index move + duration), sec_id cache for expired contracts, generate-backtest route. LOT_SIZE=65. |
| 2026-06-18 | `generate_june_mfe.py` — pipeline: NIFTY 1-min from Dhan → Range Chain backtest → ATM strike (`round(price/50)*50`) → option sec_id probe (cache-first for expired) → option bars → trade_log.json. DH-904 retry with backoff. |
| 2026-06-18 | `auto_data_downloader.py` — VPS daemon: polls Dhan /v2/orders every 5min (market hours) / 60min (off-hours), downloads 1-min OHLC bars to `data/trade_ohlc/{SECID}_{DATE}.json`, gap detection, token-expiry alert → `data/downloader_alert.json` → dashboard red banner via `/api/downloader-alerts` route. |
| 2026-06-19 | Token banner auto-clear — `api_set_token` token save hone ke baad `downloader_alert.json` se token-expire entries filter karta hai. |
| 2026-06-19 | Auto-scheduler active flag — Stop → `"active": false`; Paper/Live → `"active": true`. Sirf active=true strategies 9:10 pe start hoti hain. |
| 2026-06-19 | Order sound notification — Log polling mein `[PAPER]/[LIVE] BUY/SELL` detect ho to browser beep (Web Audio API). WIN=ascending, entry=double beep. Duplicate guard. |
| 2026-06-19 | dhan_master lot size fix — `int(float(...))` se `'850.0'` crash fix. Sab error returns `None,None,None` (3 values) — pehle 2 the, unpack crash hota tha. |
| 2026-06-19 | Quick Order PE/CE unpack fix — `trader_dashboard.py` mein 3 jagah `sec,sym=` → `sec,sym,_=`. Lot size ab dhan_master cache se (hardcoded 65 hata). |
| 2026-06-19 | P&L Column Selector — ⚙️ Columns button → modal → checkboxes. New columns: Capital Invested, Gross P&L, Tax & Charges (Zerodha formula), Net P&L. localStorage persist. |
| 2026-06-20 | Backtest Results page — match-status filter tabs (All/Exact/Near/Entry-only/No match) above chart, filters chart markers + table together; TV's own Win Rate/Profit Factor/Sharpe/Trades shown subtly under each PY stat card (`tv_summary` in backtest_engine.py, shared `_compute_stats()` for PY+TV); Range Chain renamed "Range Chain" in dropdown (was raw `ARS_CHAIN` config key), version dropdown shows just the suffix. |
| 2026-06-20 | **`_CHARTING/` reusable charting module** — candle pattern detection (`patterns.py`, extracted verbatim from `range_trader.py`, behavior-preserving — regression gate re-confirmed 90.2%/93% baseline unchanged) + pivot/zone builder (`zones.py`, same extraction) + library-backed indicator calc (`indicators.py`, uses the open-source `ta` package — `pandas-ta` doesn't support Python 3.14 here, numba build fails) + JSON shaper (`plot_spec.py`). `backtest_engine.py`'s `_run_range`/`_run_rsi`/`_run_ema`/`_run_vwap_ema` all now emit a `plot_spec` (zones/pattern markers/indicator lines) in the backtest JSON; `backtest_chart.html` has a generic `_renderPlotSpec()` that draws it — no per-strategy chart JS needed going forward. Dashboard also got a no-code "➕ Add Indicator" dropdown (`/api/indicators/list` + `/api/indicators/compute`) backed by the same registry, persisted per strategy+date-range in localStorage. Goal: close the Pine-vs-Python visualization gap that was costing debugging time; stretch goal is visualization good enough to skip the Pine-first step for new strategies. **Found and left unfixed (separate scope):** `/backtest` route in `trader_dashboard.py` 500s — `send_file(BASE_DIR / 'backtest_dashboard.html')` points at a nonexistent path (real file is at `_TOOLS/backtest_dashboard.html`); the working route is `/backtest-chart` → `templates/backtest_chart.html`. |
| 2026-06-20 | **VWAP-EMA Failure Reversal strategy** added (Mukul's strategy, decent on TCS/POLYCAB/RIL 5-min). `strategies/vwap_ema_failure.py` — EMA(10) vs daily VWAP regime, failure/reversal trigger, R-multiple targets w/ partial exits (40/30/30%), daily-trend filter, session filter; 3:15 EOD force-exit enforced bar-by-bar (no overnight holds, ever — project rule). `_TOOLS/backtest_engine.py` — generic per-symbol equity 1-min downloader (`ensure_equity_data`/`load_equity_1m_range`, mirrors NIFTY's, uses `dhan_master.get_equity_info()`), `resample_with_volume()` (VWAP needs volume, NIFTY's `resample()` doesn't keep it), `_run_vwap_ema()` runner registered as `_RUNNERS["vwap"]`. Configs: `vwap_v1`(TCS)/`vwap_v2`(POLYCAB)/`vwap_v3`(RELIANCE) in `nifty_config.json`, all `active:false` (backtest-only for now). Pine: `_PINE/v6.pine` / `vwap_v{1,2,3}.pine`. |
| 2026-06-21 | **TradingView Webhook → auto order engine** (Phases 1-4, LIVE on VPS). TV Pine sirf thin signal (`{id,symbol,signal:ENTRY/EXIT,action:buy/sell}`) bhejta hai; strike (ATM±N)/qty/paper-live/trailing/exit sab Python decide karta — strategy Pine me hi rehti, zero drift. `webhook_executor.py` (NEW): `handle_signal` + monitor (trailing SL premium/index, target, 3:15 squareoff), dedup, max-trades/day, no-entry-after. `trader_dashboard.py`: `/api/webhook/tv` (token auth ?token=/X-WH-Token) + `/api/webhook/status` + monitor daemon + scheduler guard (webhook_v1 process nahi). `templates/index.html`: 🔗 Webhook tab — URL/secret-token, config grid, **Sell/Buy toggle** (SELL default: LONG→PE sell, SHORT→CE sell), **live strike LTP** (CE+PE, `/api/option-ltp` reuse), TV alert template (`{% raw %}` for `{{...}}`), positions+log. **Pine override:** alert JSON me koi exec param (sl/qty/strike_offset…) bheja to dashboard config override (`_merge_overrides`). Reuse: `dhan_master.get_option_contract`, `smart_order.execute` (paper==live), `DhanBroker`, `dhan_feed`; log parse_pnl-compatible. VPS verified: real paper SELL 24000-PE @72.30. Config `webhook_v1`. |
| 2026-06-21 | **Webhook — HTTPS + Pine alerts + dual-dashboard sync + fixes.** (a) **Caddy** route added (`/etc/caddy/Caddyfile`): TradingView URL = `https://72-61-173-32.nip.io/algo/api/webhook/tv?token=…` (TV sirf 80/443 allow karta; 80 code2-docker ne liya, 443 pe Caddy already valid cert). (b) **Pine alerts JSON:** `range_chain.pine` `alert()` calls ab JSON (LONG→ENTRY/buy, SHORT→ENTRY/sell, exits→EXIT) + `whSymbol` input + 3:15 guarded exit. TV: **"alert() function calls only"** (Message box ignore; Notifications se sound/app aayega). Base = user's actual Desktop/LATEST.txt (dashboard v5 nahi). (c) **`sync_pine.py`:** dual-dashboard Pine store union-sync (versions+code+images). Local (192.168.29.200) aur VPS diverge ho gaye the → merge karke identical kiya. (d) **LOCAL/VPS badge** header+tab-title me (`index.html`, hostname-based). (e) **Creds path fix:** dashboard ab apne `CONFIG_FILE` (root data/config.json) se token padhta hai, `range_trader.load_creds()` nahi (jo local pe `_TRADERS/data/config.json` dhundta tha — fail). 6 routes fix. (f) **LTP rate-limit fix:** Dhan marketfeed ~1 req/sec — index+option calls ke beech 1.1s gap, 429 pe stale-cache, TTL 3s→30s, LTP poll 4s→15s, strike onchange refresh. (g) **`-X utf8`:** local dashboard launch (emoji mojibake fix). | 
