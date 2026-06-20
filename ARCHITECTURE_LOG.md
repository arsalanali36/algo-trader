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
