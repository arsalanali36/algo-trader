# 🖥️ FRONTEND — static/js map + render pipeline

Dashboard ka frontend = **classic global-scope scripts** (koi ES module / bundler nahi).
`templates/index.html` = **markup only** (3,675 lines); saara JS `static/js/` ki files me.
Ye doc = "kaun si file kya OWNS + load-order + kuch permanent traps". Backend wiring
[`ARCHITECTURE.md`](ARCHITECTURE.md), backend routes uski §8.3 me.

---

## 1. Load-order — `<script src>` ka order = ORIGINAL code order

2026-07-16 split: ek 14k-line inline `<script>` → ye files. **Load order = code order**
(files global scope share karti — ek file ka function doosri define karti). `index.html` me:

```
 notify.js · env-badge.js · registry.js          ← shared/global (standalone pages bhi load karte)
 app-00-core → app-15-mobile                      ← main dashboard, 16 files, ISI order me
 mobile-tools.js · sltp-modal.js · tags.js        ← helpers (main page pe)
```

**⚠️ TRAP #125 — hoisting files ke beech NAHI chalti.** Ek inline block me `setTimeout(fn,0)`
"sab scripts ke baad" chal jaata tha; alag files me browser use **do scripts ke beech** chala
deta hai → `X is not defined`. **Isliye:** koi bhi load-time / top-level call `DOMContentLoaded`
me karo, file-body me seedha nahi. `{{ }}` (Jinja) JS static file me KABHI nahi (wahan Jinja
render nahi hoti — TradingView `{{timenow}}` isi liye `{% raw %}` me inline rehta hai).

Har `.js` ka ek `.js.gz` sibling hai — `serve_lab_file`/static route gzip serve karta (ETag/304).

---

## 2. Shared / global (har standalone page bhi load karta)

| File | Owns |
|---|---|
| **`registry.js`** | 🔑 Frontend ka **SINGLE source for strategy identity** (TRAP #132). 4-tarfa alias index (id/config_key/slug/aliases — Python `resolve()` parity), `regLabel`/`regFull`/`regId`/`_stratHue`. Naam READ-time pe resolve → naam badlo to poori purani history naye naam pe. Raw id kabhi leak nahi (registry down → raw, seed-map nahi). Har page pe load hota. |
| **`notify.js`** | Bell / notification-centre + toast. Alert `source`/`source_label` se aata, label read-time pe (registry). Chain alerts clickable → `/curves`. UI-noise filter (load-race "not defined"). |
| **`topnav.js`** | index.html wala SAME global header standalone pages pe clone karta (nav consistency). |
| **`env-badge.js`** | LOCAL/VPS badge (hostname) + `syncFromVps`. Dual-deploy confusion fix. |
| **`mobile-tools.js`** | Kisi bhi page ka busy toolbar ko per-page "☰" sub-menu me collapse (narrow-width). |

---

## 3. Main dashboard — `app-00` … `app-15` (kaun si tab / concern)

| File | Owns (tab / concern) |
|---|---|
| **app-00-core** | Core boot: `switchTab()` (tab router, blank→Orders fallback), 10-min auto-reload (OOM leak guard), apiFetch/toast base. |
| **app-01-rms** | ⚠️ **Risk tab** — global + per-strategy override table (caps/SL/mode/margin/tier), `gateBadge` liveness (RUNNING_PIDS-aware), broker balances + ledger graph, kill-floor/lock cards. |
| **app-02-webhook-orders** | 🔗 Webhook tab + Orders page control (payoff panel, price-triggers panel, group-collapse, per-order margin). |
| **app-03-orders-render** | Open Positions render — live LTP patch (`_ltpLive` sec_id-keyed), group collapse (localStorage), Run-Up/Down, DTE. |
| **app-04-orders-summary** | Completed-trades summary, `getStratColor` (golden-angle per-strategy hue), TOTAL rows. |
| **app-05-sound-bulk** | Order-sound notifications (Web Audio beep) + bulk-order preview/fire. |
| **app-06-config-tab** | Config tab — `renderConfigTable`, per-strategy grid (instrument/offset/TF/mode), exit-reason badges. |
| **app-07-columns-stats** | Column selector (⚙ Columns) + stats column defs + DTE/margin badges (shared across Orders/Stats tables). |
| **app-08-clock-sse** | Clock, Kite login link, `/api/ltp-stream` SSE consumer (live-LTP merge into `_ltpLive`). |
| **app-09-quick-order** | 🎯 Quick Order floating panel — Instant / Trigger / 🩳 Straddle leg-builder, chain picker, live LTP. |
| **app-10-pine** | 📌 Pine version manager (versions.json + code + images). |
| **app-11-script-lab** | 📜 Script Library — paste/upload Pine/Python/DSL, master-prompt modal, version history. |
| **app-12-calendar** | Stats calendar TAB logic — mode/view/strategy filters, Saved Views, Live⟷Backtest toggle. |
| **app-13-calendar-render** | Calendar RENDER — grid, Total Summary table, equity curve (combined + per-strategy overlay), point-per-trade, `data-tip` hover. |
| **app-14-bs-shadow** | 💰 Real \| 📐 BS \| ⚖️ Compare — bs_shadow divergence join (bykey), Real-vs-BS bottom tab. |
| **app-15-mobile** | 9:16 shell glue — hamburger drawer inject (`.hdr .tabs`), scrim, dropdown collapse. Pairs with `static/css/mobile.css` (SAB rules `@media(max-width:760px)` ke peeche — desktop bit-exact untouched). |

**Helpers (main page):** `sltp-modal.js` (per-position SL/Target ⚙ modal), `tags.js` (tag store + assign).

---

## 4. Standalone pages (apni glue file / template)

`/stats2` → `stats2.js` (app-12/13/07 reuse, new tabbed layout) · `/report` → `daily_report.js`
(`DR` global, display-only) · `/curves` `/gex` `/whatif[2]` `/backtest-lab` `/intervention` `/registry2`
`/trade-chart` `/strategy-study` etc. = apne `templates/*.html` me page-scoped `<style>`/JS, par
`registry.js`+`notify.js`+`topnav.js` shared load karte. Mobile: har naya page 9:16-ready ho
(page-scoped `@media(≤760px)` + `[style*="…"]!important` for JS-injected inline-styled content).

## 5. Render pipeline (mental model)

```
 apiFetch(/api/...) → JSON → module render fn (innerHTML/DOM)
   · strategy naam kabhi raw nahi — registry.js (regLabel/regFull) se
   · live LTP: SSE (app-08) + /api/positions-ltp poll → _ltpLive (sec_id-keyed) → patch cells
   · Stats/Orders tables: shared column-defs (app-07), shared render (app-13) → live/backtest same shape
```

---

## Cross-reference
- Backend wiring + route-map: [`ARCHITECTURE.md`](ARCHITECTURE.md) (§8.3 routes)
- Per-module (Python) detail: [`MODULES.md`](MODULES.md)
- Recurring frontend traps: [`../LESSONS.md`](../LESSONS.md) (#125 hoisting, #132 registry-label, #137/#147/#148/#149 UI)
