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

## 7. Data / broker plumbing (`_data/*` + `brokers/*`)

Poore app ka price + contract-resolution + order plumbing yahin baithta hai. Per-module
API [`MODULES.md`](MODULES.md) me hai — yahan **wiring**: data kaise flow karta, rate-limit
priority, aur feed-vs-poller kab kaun.

### 7.1 Do zaroori baatein pehle

1. **Dhan limit ACCOUNT-WIDE hai (~1 req/s, burst pe DH-904), per-process nahi.** Har
   process (dashboard, monitor, har forked strategy, webhook, collector) apna
   `brokers.DhanBroker` banata hai — sab ek hi account quota pe. Isliye plumbing ka poora
   design = "N process × M symbol" ko "~1 call per symbol per window" me collapse karna.
2. **Contract identity = sec_id, trad_sym NAHI.** trad_sym unique nahi (month+year, expiry-din
   nahi) → do open position ek string, do expiry pe. Har price/chart/payoff/margin OPEN
   position ke liye order_store ke **stored sec_id** se; `dhan_master.get_sec_id_for_trad_sym`
   sirf fresh/nearest-expiry quoting ke liye (TRAP #100/#166).

### 7.2 Read path (price/candle) — 4 layer, upar se neeche fallback

```
 Dhan REST + WebSocket
   │
 [1] dhan_rate_limiter  ── EVERY real Dhan call yahan se: acquire(priority) → slot → call → 429? note_429()
   │      priority: order > ltp > candle > account
   │      · "order" slot HAMESHA reserved — background candle/LTP kabhi order ko starve nahi karta
   │      · per-endpoint sub-cap (ltp/candle alag rolling min-spacing — Dhan inhe alag 429 karta)
   │      · 429 → 8s cooldown: non-order traffic band, orders phir bhi chalte (account recover kare)
   │      · sqlite token-bucket (stdlib, Win+Linux same) — kite_rate_limiter = alag DB (alag quota)
   │
 [2] dhan_feed (WebSocket, Full packet)  →  in-memory LIVE dict: bid/ask/LTP/OI/volume per sec_id
   │      · cross-process LEADER-ELECTION (sqlite): EK process feed owns, baaki chup wait
   │        (warna 3 conn same account → 429 storm; TRAP #87/#89). range_trader + dashboard callers.
   │      · fastest, zero-REST — jo sec_id subscribed hai uska LTP yahin milta
   │
 [3] ltp_poller (monitor_daemon me ek daemon thread)
   │      · har cycle 1 BATCHED /marketfeed/ltp call = SAARE open-position sec_id + NIFTY/BNF spot
   │        (Dhan 1000 instr/call leta hai — N alag call → 1 call)
   │      · request_watch(sec_id) = dynamic warm-list (90s TTL) — dashboard routes "isko warm rakho" bolte
   │      · result → shared_ltp_cache.put_many()
   │
 [4] shared_ltp_cache (file-backed JSON, sec_id-keyed, short TTL)
   │      · SAB process ek file share karte — jisne fetch kiya wo sabko de deta
   │      · get_index(sym, max_age=) = NIFTY/BNF spot cache-only (poller-warmed, ZERO extra call)
   │      · last-writer-wins (koi distributed lock nahi — price cache ke liye theek)
   ▼
 consumers (sab cache-FIRST, miss pe REST fallback):
   pos_monitor_loop · smart_order.marketable_price · dashboard LTP routes · strategy fetch
```

**shared_candle_cache** = wahi pattern candles ke liye (`/v2/charts/intraday`): same symbol+interval
TTL ke andar dobara maanga → cached DataFrame. **Bar-boundary-aware** — current bar khulne ke
baad ka fetch saare closed bars deta = lossless reuse (range_trader + rsi_trader same underlying
pe duplicate fetch band; TRAP #2/#95).

**Feed vs poller — kab kaun:** feed (2) sabse fast par sirf subscribed + leader-process ko; poller
(3) har open position ka steady-state LTP, ek batched call me, sabke liye. Consumer hamesha
`shared_ltp_cache` (4) pehle padhta — usme feed aur poller dono likhte hain. Naye entry ka option
contract (order_store row banne se pehle) = rare cache-miss → direct REST (rate-limited) one-off.

### 7.3 Resolver layer — sec_id / trad_sym / lot / expiry

- **`dhan_master`** = poore app ka single source ("is contract ka sec_id/lot/expiry kya"). Roz
  28MB `api-scrip-master.csv` download/cache; usse har resolve. **Option resolver PE offset ko
  KHUD invert karta** (positive = OTM/neeche) → PE pe negative offset KABHI nahi (TRAP #140, audit
  PE-OFFSET-SIGN guard). Monthly-expiry ke liye `get_monthly_option_contract` (positional/overnight).
- **`universe`** = NIFTY-50 constituents + equity/index/option routing (`equity_secid`,
  `index_spot_secid`, ATM resolvers `dhan_master` se reuse).
- **`fno_universe`** = ~200 F&O stock universe (SEBI F&O-eligible = objective liquid set),
  scrip-master ke FUTSTK rows se derive → `data/fno_universe.json` (downloader/scanner/backtest sab isse padhte).
- **`opt_hist`** = expired-options historical premium (Dhan paid `rollingoption` add-on) — held-strike
  reconstruction; backtest lakes + what-if isse.

### 7.4 Broker abstraction (`brokers/*`)

```
 get_broker("kite" | "dhan")   ← factory (__init__.py), creds data/config.json se
   └─ BaseBroker (abstract): place_order · quote · funds · positions · intraday_candles
        · order_status · get_fill · cancel_order · resolve_symbol · positions_detailed
```

- Engine kabhi broker SDK se seedha baat nahi karta — sab `BaseBroker` conventions pe
  (seg/instrument/side/order_type + normalized quote/order-result dicts).
- **Config-driven active broker** (`nifty_config.json → "broker"`). Aaj **orders = Kite**
  (`default_broker=kite`), **data = Dhan hamesha** (Kite pe market-data add-on nahi — `kite.ltp`
  → Insufficient permission; KiteBroker ka quote/candles Dhan ko delegate karte, by design).
- **Kite symbol resolve** = structured-field match (`resolve_kite_symbol`/`resolve_symbol` —
  `kite.instruments("NFO")` se exact), formatted string parse NAHI (TRAP #13/#79). Reverse
  (`resolve_dhan_from_kite_symbol`) auto-adopt ke liye.
- **IPv4-force dono pe zaroori** (VPS IPv6 outbound → Dhan DH-905 / Kite PermissionException chahe
  IP whitelisted ho; Critical Rule 1).

---

## 8. Control plane — dashboard / scheduler / supervisor / health

§1 ne "kaun process" bataya; ye "kaun kise control karta" hai. Yaad rakho: process aapas me
**shared memory se nahi, disk (order_store / config / flag-file) se** baat karte hain.

### 8.1 Strategy start/stop — dashboard = single source

```
 auto_scheduler (monitor_daemon me, 9:10 start / 15:30 stop)
   │  loopback + internal-token se dashboard ko HTTP call (TRAP #120 — login-gate ke
   │  peeche apne hi caller lock ho gaye the; ab get_internal_token() + status-check + loud fail)
   ▼
 POST /api/start (dashboard)  ← strategy control ka EKMATRA entry
   │  STRATEGIES dict = single source (id → script, mode). Config me active/mode persist.
   │
   ├─ supervisor_mode.flag maujood? →  data/supervisor_desired.json me {sid, mode, script} likho
   │        ▼
   │   algo-supervisor daemon reconcile karta (desired ⟷ running): warm-parent se os.fork()
   │        · parent = pandas + 26MB scrip-cache EK BAAR (single-threaded; trader_dashboard KABHI
   │          import nahi — fork+threads = deadlock trap)
   │        · har child COW-shared par ALAG process (ek restart/kill → baaki safe; ~76% RAM kam)
   │        · daily re-warm (naye din ki pehli fork se pehle scrip re-download — stale-expiry money-bug block)
   │        · PARITY: crash pe auto-respawn NAHI (legacy Popen bhi nahi karta tha)
   │
   └─ flag nahi / daemon dead (pidfile check)? →  FAIL-SAFE legacy subprocess.Popen + loud log
            ("supervisor mara to kal strategies start hi nahi hui" wala failure exist nahi karta)
```

- **`get_pid()`** (dashboard) child ko `supervisor_pids.json` + setproctitle (`code3b-strategy
  --paper --id <sid>`) se pehchanta — psutil/pgrep dono match (idle `rsi_v1` ≠ live `rsi_v1_PAPER`,
  single-token split-fix; real-money footgun tha).
- **15:30 ke baad** sab `desired:stopped` → 0 children (normal); 9:10 pe re-fork.
- **`monitor_daemon`** khud `trader_dashboard` ko **plain module import** karta (functions +
  Flask `app` banata, port-bind sirf `__main__` me) → wahi loop-code zero-drift reuse.

### 8.2 health_check — roz subah "order lagega ya nahi" preflight

Read-only (koi order nahi). Per strategy chain verify: CONFIG (active?) → SCRIPT (compile?) →
HEARTBEAT (log recent? TF-aware) → TOKEN (Dhan JWT valid + kab expire — sabse bada subah-killer)
→ DATA (live LTP aata?) → CONTRACT (ATM resolve?). Webhook = dashboard-up check. `--json`
(scheduler), `--fire-test` (PAPER, asli order-path → DB-land confirm → cleanup), `--report`
(dashboard red-banner). systemd `algo-healthcheck.timer` Mon–Fri 09:20 IST. Exit 1 = koi active RED.

### 8.3 `trader_dashboard.py` route-map (~200 routes — GROUP-wise, per-route essay nahi)

| Group | Routes (sample) | Money-path? |
|---|---|---|
| **Auth** | `/login` `/logout` `/api/change-password` | — (gate; `/api/webhook/tv` + `/static/*` open) |
| **Process control** | `/api/start` `/api/stop` `/api/status` `/api/config` GET/POST `/api/timer-*` | 🔴 start/stop = supervisor desired-state |
| **Orders & positions** | `/api/orders*` `/api/manual-order` `/api/bulk-order` `/api/close-position[-group]` `/api/position-*` `/api/triggers` `/api/auto-straddle/*` `/api/chain/fire-basket` `/api/sync-positions` `/api/reconcile-*` | 🔴 real orders → execution_gateway/smart_order + reconcile-broker |
| **RMS / risk** | `/api/risk-config` `/api/rms-*` `/api/kill-floor-status` `/api/per-instrument-lock-status` `/api/broker-balances` `/api/broker-ledger*` | ⚠️ config drives money-path (gates/caps) |
| **Webhook** | `/api/webhook/tv` (TV→order) `/api/webhook/status` | 🔴 TV signal → webhook_executor |
| **Broker/token** | `/api/token` `/api/kite-*` `/api/lot-sizes` | ⚠️ creds (JWT roz, Kite request_token) |
| **Backtest/research** | `/api/backtest/*` `/api/pine/*` `/api/indicators/*` `/api/scanner/run` `/api/symbols/search` `/api/deploy-variation` `/lab/upload*` | — display/research (ADR-010; deploy-variation = paper, inactive) |
| **Analytics/display data** | `/api/daily-report*` `/api/gex` `/api/option-*` `/api/whatif*` `/api/bs-shadow` `/api/stat-views` `/api/strategy-equity` `/api/intervention*` `/api/morning-brief` `/api/fii-flow` `/api/backtest/calendar-summary` | — read-only (Rule 10 — display-only) |
| **LTP/chart data** | `/api/positions-ltp` `/api/ltp-stream` (SSE) `/api/option-ltp` `/api/trade-chart-data` `/api/peak-pnl-history` `/api/margin-history` | — reads (ltp_poller/feed via shared_ltp_cache) |
| **Notify/ops** | `/api/notifications*` `/api/notify` `/api/health-report` `/api/downloader-alerts` `/api/log` `/api/rate-limit-events` | — |
| **HTML pages** | `/` `/stats2` `/curves` `/gex` `/whatif[2]` `/backtest-lab` `/brief` `/report` `/registry[2]` `/intervention` `/trade-chart` `/strategy-study` `/lab` `/script3` `/sl-map` `/reports` `/presentations` `/fii-flow` `/strategy-equity` … | — templates |

**Bulk = display.** Sirf **Process control + Orders&positions + Webhook** money-path chhoote hain;
sab wahi shared gate se jaate (§2). RMS/token config-only (par money-path drive karte). Baaki
read-only (Rule 10). Login-gate: sab band except `/login` `/logout` `/static/*` `/api/webhook/tv`.

---

## 9. Strategy layer — ek order ka poora lifecycle

§2/§3 ne money-path GENERIC bataya; ye ek asli strategy ka concrete walk hai. Poora point:
**strategy file sirf signal + ATR-stop/sizing likhti hai — baaki sab shared layer.**

### 9.1 Do folder

- **`strategies/signals/*.py`** = signal ki EKMATRA implementation (`orb.py`, `chain_zone.py`).
  Backtest **aur** har live trader dono isko call karte → by-construction match (ADR-010,
  TRAP #153). Live trader me signal ki inline copy KABHI nahi.
- **`strategies/live/*.py`** = live trader LOOP (~20 files: `orb_trader`, `range_trader`,
  `01_rsi_v1`, `04_chainzone_trader`, `vrp_*`, `straddle_trader`, `bnf_*`, …). Har ek: candle
  fetch → signal (shared se) → ATR-stop/sizing → gate → execute → record → recovery. Ye
  supervisor ke forked children ke roop me chalti hain (§8.1).

### 9.2 Concrete walk — `orb_trader.py` (single-leg pilot, sabse saaf)

```
 main() loop (forked child, ~per-bar)
   │  df = candle fetch (shared_candle_cache → Dhan intraday, §7.2)
   ▼
 compute_signal(df, cfg)
   │  return orb.orb_signal_last(df, sig_params)   ← SHARED signal (strategies/signals/orb.py)
   │  · wahi validated tod_orb jo backtest chalata (OR-boundary/ATR bit-identical)
   │  · trader me sirf: kaun sa ATM CE/PE (LONG→CE, SHORT→PE), lots, ATR stop/target
   ▼
 gw.execute_signal(strategy_id, sym, "BUY", lots, lot_size, sec_id, trad_sym, ...)
   │  = execution_gateway (§2): gate_entry (RMS fail-closed) → ₹0-skip → SL/target tags
   │    → naked SELL pe hedge-first → smart_order.execute → order_store.record
   ▼
 position OPEN → order_store me row → RMS ab isko dekhta (Rule 6)
   │
   ├─ EXIT (signal ya ATR-stop): gw.execute_exit(strategy_id, sym, sec_id, trad_sym, qty, ...)
   │       fresh strategy-aware flat-check + is_exit chase + extra_tags=[reason] (Rule 9)
   └─ EXIT (SL/TP/EOD/RMS): pos_monitor_loop se (§3) — strategy ko nahi pata, isliye ⤵
 restart-recovery: _recover_state_from_order_store() (startup + per-cycle re-validate)
   │  order_store se today's open legs rebuild → in-memory _state seed (last_day=today, TRAP #76)
   │  warna restart pe strategy "flat" maan ke duplicate/orphan (TRAP #28/#84/#119)
```

### 9.3 Naya strategy — 4 cheezein har baar (Rule 8, checklist se dohraana nahi)

Full copy-paste-ready templates + har wo galti jo bite kar chuki:
[`strategies/live/NEW_STRATEGY_CHECKLIST.md`](../strategies/live/NEW_STRATEGY_CHECKLIST.md).

1. Order = `execution_gateway.execute_signal`/`execute_exit` (raw REST nahi) → rate-limit +
   async-confirm + order_store auto.
2. Entry se pehle `strategy_safety.gate_entry(...)` (ek call = gating + drawdown + liquidity +
   concentration + capital-sizedown + funds); `ok=False` → skip, `qty` sized-down use karo.
3. Naked SELL → `strategy_safety.compute_hedge_target(...)` → hedge BUY pehle.
4. `order_store.record` (smart_order se auto) — warna RMS-blind.
Plus: signal `strategies/signals/*` se (inline nahi); restart-recovery (`_recover_*` + last_day
seed); positional/overnight → `risk_gate._ALWAYS_OVERNIGHT` (code-set, config nahi — TRAP #119).

**Honest boundary (ADR-010):** sirf spot-candle-series signal literally share hota hai. VRP
(IV-rank, live-premium vs lake), BankNifty (alag store), RSI/EMA (alag design), ARS_CHAIN
(Pine 90.2% validated) — alag data-domain, inpe zabardasti "100% match" nahi.

---

## Cross-reference
- Per-module detail (auto): [`_DOCS/MODULES.md`](MODULES.md)
- Decisions (kyun): [`_ADR/`](../_ADR/) — 001 gateway · 010 signal-single-source · 011 reconcile · 015 margin
- Recurring bugs + guards: [`LESSONS.md`](../LESSONS.md)
- Naya strategy: [`strategies/live/NEW_STRATEGY_CHECKLIST.md`](../strategies/live/NEW_STRATEGY_CHECKLIST.md)
