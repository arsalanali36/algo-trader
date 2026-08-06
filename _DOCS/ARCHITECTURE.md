# 🏛️ ARCHITECTURE — pieces aapas me kaise judte hain

Ye **wiring** doc hai — "kaun sa module kya karta hai" nahi (wo `_DOCS/MODULES.md`,
auto-generated). Yahan wo taar hain jo docstrings nahi bata sakti: order/money-path ka
flow, process model, netting, reconcile. Ye file sirf **asli architecture badalne** pe
update hoti hai. Decisions ka kyun = `_ADR/ADR-0xx-*.md`. Recurring bugs = `LESSONS.md`.

**Golden rule (poore system ka intent):** "signal banana" aur "signal ko safely
execute/monitor/reconcile karna" DO ALAG layer hain. Strategy files sirf entry/exit
condition likhein; baaki sab shared layer (gateway / risk_gate / broker) sambhaale.
(CLAUDE.md Rule 6/6B/6E, ADR-001/010.)

---

## 1. Process model — teen (+2) alag process

Sab systemd pe (VPS), dev pe manually. **Ek-dusre se order_store (sqlite) + config +
disk-cache ke through baat karte hain — shared memory nahi.**

| systemd unit | Entrypoint | Kaam |
|---|---|---|
| `algo-dashboard` | `trader_dashboard.py` (Flask :5099) | UI + saare `/api/*` routes + webhook receiver (`/api/webhook/tv`) + auto-scheduler (9:10 start / 15:30 stop). LOGIN-gated (`_core/dashboard_auth`). |
| `algo-monitor` | `monitor_daemon.py` | **Safety brain.** `pos_monitor_loop` (~5s): SL/TP/Default-TSL/KILL-floor/EOD-3:15/expiry squareoff · reconcile (broker mirror) · auto-straddle basket-exit · price-trigger watch. LTP `ltp_poller` se. |
| `algo-supervisor` | `_ops/strategy_supervisor.py` | Warm-parent (pandas + scrip-master cache) se har live strategy ko `os.fork()` (Linux COW, ~76% RAM kam). Har strategy PHIR BHI alag process (isolation intact). Fallback: legacy Popen. |
| `algo-optionchain` | `_ops/option_chain_collector.py` | NIFTY/BNF (+5 stocks) ka per-minute option-chain snapshot → `_TRADING_DATA/OptionChain/` lake (gamma/OI/VIX/PCR). ALAG Dhan rate-bucket (trading budget se). |
| timers | `_DEPLOY/*.timer` | health-check 9:20 · eod-report 15:45 · bs-shadow 15:50 · equity-daily 16:10 · bt-extend 16:25 |

**Live traders = `algo-supervisor` ke forked children** (ek per active strategy).
Dashboard `STRATEGIES` dict single-source hai (kaun sa id → kaun si script). 15:30 ke
baad sab `desired:stopped` (0 children after-hours = normal); 9:10 pe re-fork.

---

## 2. Order money-path — ENTRY se EXIT tak (SABSE ZAROORI)

```
 SIGNAL (strategies/signals/*.py — orb.py / chain_zone.py; live+backtest DONO isko call)
   │  (live trader loop: strategies/live/*.py — sirf ATR-stop/sizing/order yahan)
   ▼
 strategy_safety.gate_entry(strategy_id, symbol, lots, lot_size, est_price, side, sec_id, mode, broker)
   │  ek call me: gating_status (max-trades/day, kill-floor, manual-close-veto)
   │           + drawdown breaker + live-liquidity (any-2-of-3) + concentration
   │           + capital (mode-wise pool, size-DOWN de sakta) + broker-funds (LIVE, real margin)
   │  ok=False → entry SKIP. qty = returned (sized-down ho sakta).
   ▼
 execution_gateway.execute_signal(...)   ← SINGLE order gate (ADR-001)
   │  · ₹0/no-premium → SKIP (kabhi ₹0 fill record nahi — TRAP #1)
   │  · gate=True → gate_entry (upar) fail-closed; blocked → skipped_store.record (what-if data)
   │  · default SL/target tags stamp (risk_gate.default_sl_profile — per-strategy/global)
   │  · naked SELL → strategy_safety.compute_hedge_target → hedge BUY pehle (unwind-safe)
   ▼
 smart_order.execute(...)  (brokers/ ke through — get_broker('kite'|'dhan'))
   │  · marketable-limit price (dhan_feed bid/ask, max_age-guarded)
   │  · dhan_rate_limiter / kite_rate_limiter (account-wide 1req/s, order-priority reserved)
   │  · async fill-confirm (poll get_fill 8s) + order-chase (cancel+re-place)
   │  · provisional row turant, real fill pe correct (TRAP #63) — "8s me confirm nahi" ≠ "hua hi nahi"
   ▼
 order_store.record(...)   ← RMS ka EKMATRA sach (jo yahan nahi, wo RMS ko dikhta hi nahi, Rule 6)
   trades.db · fields: side/qty/price/status/source/strategy/mode/broker/trad_sym/sec_id/
   broker_order_id/correlation_id/group_id/tags
```

**Paper vs Live:** `mode` param har layer me thread hota hai. Paper==Live logging
(P&L identical), live me asli broker order bhi. Capital pools mode-wise ALAG
(paper positions live budget nahi khaati).

**Naya strategy?** `strategies/live/NEW_STRATEGY_CHECKLIST.md` + Rule 8 — ye 4 cheezein
har baar: (1) `smart_order.execute`/gateway se order, raw REST nahi; (2) entry se pehle
`gate_entry`; (3) naked SELL → `compute_hedge_target`; (4) `order_store.record` (auto via
smart_order). Signal ki inline copy KABHI mat likho — `strategies/signals/*` se call (ADR-010).

---

## 3. Monitoring + exit (algo-monitor: `pos_monitor_loop`)

```
 pos_monitor_loop (~5s, monitor_daemon.py)
   │  order_store.trades_for(today).open  → har open leg
   │  LTP: ltp_poller (1 batched Dhan call/cycle) → shared_ltp_cache (sec_id-keyed, TRAP #166)
   ▼
 per-leg checks (jaldi-se-jaldi jo bhi trigger ho):
   · SL/TP tags (SL_TYPE/SL_VAL — %, points, ₹/lot, premium-level, index-level)
   · Default-TSL (risk_gate.advance_target_sl — arm/gap/confirm, spike-guard, candle-close)
   · per-instrument trailing lock  +  account-wide KILL-floor (risk_gate.advance_trailing_lock)
   · EOD 3:15 squareoff (risk_gate.exit_time_config — single source; allow_overnight skip)
   · expiry guards (default OFF — risk_gate.expiry_auto_squareoff_enabled)
   · RMS daily-loss breaker / daily-profit-lock
   ▼
 _pre_exit_guard(...)   ← FRESH broker flat-check (position abhi bhi khuli hai? TRAP #75)
   │  paper bhi (order_store flat-check, TRAP #157)
   ▼
 _do_squareoff → group-aware (siblings bhi, multi-leg atomicity) → execution_gateway.execute_exit
   │  execute_exit: fresh strategy-aware flat-check + is_exit order-chase
   │  har exit pe extra_tags=[reason] (Rule 9 — Exit Reason column kabhi blank nahi)
```

**Market gate:** `_core/market_calendar` (weekend + NSE holiday) — `execute_signal`
non-trading-din pe entry block karta (default-enforced, TRAP #142).

---

## 4. Netting + display (`order_store._net_rows`)

Open vs Completed dashboard pe isi se bante hain. Niyam (bugs yahin baar-baar aaye):

- **qty-aware FIFO** — exit sirf `min(exit,entry)` close karta; baaki OPEN rehta
  (3-lot me se 2 manual bech diye → 1 lot orphan nahi, TRAP #167).
- **same-strategy netting** — do alag strategy same strike trade karein to cross-net
  NAHI (phantom completed rok, TRAP #145). Exception `_MANUAL_CLOSERS={manual,broker_reconcile}`
  — ye broker-truth legs strategy leg se pair ho sakte (TRAP #170).
- **positional/overnight** — per-day `trades_for(date)` overnight ko mis-net karta;
  multi-day ke liye `trades_for_range` (400-din lookback) + round-trip ko **EXIT-date**
  pe bucket (TRAP #141). `risk_gate._ALWAYS_OVERNIGHT` = code-set positional ids.
- **contract identity** — open leg ka LTP/chart/payoff hamesha order_store ke **stored
  sec_id** se (trad_sym UNIQUE nahi — month+year, expiry-din nahi; nearest-guess galat, TRAP #166).

## 5. Reconcile — broker = sach (`_core/reconcile_broker`, ADR-011)

App aur broker alag ho jaayein ("app me khula, Zerodha pe flat") → **authoritative mirror**.
`reconcile_broker.apply()` broker ko order_id se MIRROR karta hai (guess nahi) — jo broker
order app ke paas nahi = external → aggregated matched-qty row record. **LIVE only, PAPER
kabhi nahi.** "Sync from Broker" button + `mirror_if_due()` (pos_monitor, ~2.5min) isi ko
chalate hain. Purane heuristic guessers (`broker_sync` ke 3 scans) DISABLED — sirf mirror.
`_core/invariant_guard` (~120s) watchdog: app-net==broker-net + no-blank/₹0/dup.
Manual "Book Close" (`/api/orders/book-close`) = nakli offsetting leg (no real order) —
sirf sach-me-phantom position ke liye; asli close ke saath double ho to phantom banta hai.

## 6. Margin — single gate (`risk_gate.position_margin`, ADR-015)

Capital-in-use ka EKMATRA source. Multi-leg = real hedged basket margin (kabhi per-leg
sum se zyada nahi); single-leg = uska margin. `_leg_capital`/`kite_basket_margin` PRIVATE
(audit MARGIN-GATE block karta bahar se call). Real margin executing-broker se
(kite `order_margins` → Dhan calc → multiplier, kabhi fail-open nahi; TRAP #90).

---

## 7. Data flow (short — poora P1 me)

```
 Dhan/Kite REST+WS
   │  dhan_rate_limiter (sqlite token-bucket, priority: order>ltp>candle>account, 429 cooldown)
   ├─ dhan_feed (WebSocket, best bid/ask/LTP/OI per sec_id; cross-process leader-election)
   └─ ltp_poller (1 batched /marketfeed/ltp per cycle for ALL open + index spot)
        ▼
   shared_ltp_cache (file-backed, sec_id-keyed, short TTL — sab process ek cache share)
        ▼
   consumers: pos_monitor · smart_order marketable-price · dashboard LTP · strategy fetch
 dhan_master: roz scrip-master → sec_id/trad_sym/lot/expiry resolver (poore app ka)
```

---

## Cross-reference
- Per-module detail (auto): [`_DOCS/MODULES.md`](MODULES.md)
- Decisions (kyun): [`_ADR/`](../_ADR/) — 001 gateway · 010 signal-single-source · 011 reconcile · 015 margin
- Recurring bugs + guards: [`LESSONS.md`](../LESSONS.md)
- Naya strategy: [`strategies/live/NEW_STRATEGY_CHECKLIST.md`](../strategies/live/NEW_STRATEGY_CHECKLIST.md)
