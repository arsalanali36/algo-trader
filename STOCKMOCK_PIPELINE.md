# StockMock-Parity Strategy Pipeline — read this FIRST

**Goal:** user picks a strategy on StockMock (or anywhere) → we (1) backtest it on OUR data
and cross-check vs StockMock, (2) show it in the registry + Lab report, (3) run it as **live
paper** that fires on real market days. **One config drives backtest AND live paper** — they
can't silently diverge (Rule 10 / ADR-010 single-source).

> If you're adding another StockMock strategy, you do NOT write code. You write ONE config
> block + ONE registry row + run ONE command. See "Add a new strategy" below. Everything
> else (entry firing, per-leg SL, EOD exit, backtest, Lab report, Stats calendar) is already
> generic. This doc exists so you don't re-derive the whole system every session.

---

## The config (this IS the strategy)

Lives in `nifty_config.json` (VPS runtime, gitignored) under the strategy's key:

```jsonc
"sm_nifty_expiry_v1": {
  "active": true,
  "mode": "paper",                 // _sm is HARD paper-locked (live = deliberate future change)
  "_sm": {                          // ← the `_sm` marker makes it a StockMock-style strategy
    "instrument": "NIFTY",          // NIFTY | BANKNIFTY (only these have live data + lake)
    "entry_hm": "09:22",
    "exit_hm": "15:15",
    "day_filter": "expiry",         // all | expiry | weekday:N  (N: 0=Mon..4=Fri)
    "squareoff": "one",             // one | all  (per-leg SL is naturally "one")
    "legs": [
      {"opt":"PE","side":"SELL","off":0,"lots":6,"sl_pct":25},   // off = strike offset from ATM (0=ATM)
      {"opt":"CE","side":"SELL","off":0,"lots":5,"sl_pct":25}    // off>0 = OTM (get_option_contract inverts PE, TRAP #140-safe)
    ]
  }
}
```

Per-leg fields: `opt` (CE/PE), `side` (SELL/BUY), `off` (int strike-offset, 0=ATM), `lots`,
`sl_pct` (% of entry premium), `tp_pct` (optional).

---

## Two halves, same config

### 1. LIVE PAPER  (fires on real market days)
- **`_ops/sm_runner.py`** — PURE logic only (no orders, no market data): `parse_cfg`,
  `is_expiry_day` (holiday-shift aware, uses `expiry_calendar` + `market_calendar`),
  `should_fire_today`, `describe`. (Gotcha: it adds `scratch/nifty_trend` to `sys.path`
  itself because `_paths` doesn't — else `expiry_calendar` import fails and expiry-day
  detection is silently always-False → strategy never fires.)
- **`trader_dashboard._fire_sm_strategy(id, cfg)`** — the actual firing: resolve all legs
  (`dhan_master.get_option_contract`) → gate the WHOLE basket ONCE (`risk_gate.gating_status`
  + `position_margin` + `check_capital_needed`) → place each leg via
  `execution_gateway.execute_signal(gate=False)` **with a per-leg `SL_TYPE:pct` / `SL_VAL` tag**.
  All-or-nothing: any leg fails → unwind placed legs (no naked orphan).
- **Exits are NOT written here.** Each leg carries `SL_TYPE:pct` → the existing
  `pos_monitor_loop` enforces the stop (SELL: fires when premium ≥ entry×(1+sl%)) and the
  EOD squareoff. Square-off-one is automatic because each leg is a separate monitored
  position. (`execute_signal` only adds its own default-SL tags when `gate=True`; we use
  `gate=False`, so our explicit pct tag is the only SL.)
- **`trader_dashboard.sm_runner_loop()`** — a daemon thread in `monitor_daemon.py`. Every 20s
  during market hours: for each active `_sm` config, if `should_fire_today` and now ≥ entry
  time and `risk_gate.entries_today(id) == 0` (durable already-fired guard) → fire once.
- **Not a process.** `auto_scheduler` only Popen's ids that are in the `STRATEGIES` dict;
  `_sm` ids aren't, so they're loop-driven (same as webhook / auto_straddle).

### 2. BACKTEST + LAB REPORT  (on our data, cross-checked vs StockMock)
- **`scratch/nifty_trend/sm_backtest.py`** — reads the SAME `_sm` config, runs it on the
  `OptChainLake_1m` (2021→) and emits `runs/<slug>/{results.js, meta.json, index.html}` +
  appends `runs/index.json`.
  - Model (validated vs StockMock's exported PDF): **entry = the entry-minute's OPEN**
    (StockMock's fill convention — NOT close; on a fast 0-DTE open the one-minute shift flips
    per-leg SL outcomes), **held ATM strike** (not the rolling-ATM `CE_ATM.csv`), per-leg SL
    fires on the **minute HIGH** at entry×(1+sl%) and fills at that level, **0.5% slippage/leg**
    (StockMock includes) + real date-aware **Zerodha charges** + date-aware **lot**.
  - `results.js` = RESULTS_SCHEMA: `combos["bs|full"|"bs|train"|"bs|oos"]`, each with
    `metrics` (day-level Sharpe/win/PF/maxDD) + `all_trades` (per-leg rows the Stats calendar
    buckets by day). `index.html` = a self-contained rich report (KPIs + monthly grid +
    equity + trade log). (The run_hunt `dashboard_intraday.html` template needs a richer
    results.js — meta.passes/dna/benchmark — so we ship our own self-contained page.)
- **Where it shows:** `runs/index.json` → registry (`/registry2`, Lab↗ link) + Lab hub +
  **Stats tab → 🧪 Backtest toggle** (`_ops/backtest_calendar.py` reads the same combos and
  renders the full day-wise calendar/equity/summary, identical to live).

---

## Add a new strategy (the whole job)

1. **Config** — add an `sm_<name>_v1` block (as above) to VPS `nifty_config.json`
   (`active:true, mode:paper`, backup first). NIFTY or BANKNIFTY only.
2. **Registry** — add a row to `strategy_registry.json` under `strategies` with a new id in
   the right family (e.g. `02.NN` for Volatility): `config_key`/`slug` = the config key,
   `status:"paper"`, `legs`, `instrument`, `structure`, `desc`. (Use a Python one-liner with
   `object_pairs_hook=OrderedDict` + `json.dump`, then validate.)
3. **Backtest + Lab report** — `python scratch/nifty_trend/sm_backtest.py <config_key>`
   (needs the lake; run locally where the lake is, or on VPS). Emits `runs/<slug>/` +
   updates `runs/index.json`. Cross-check a few days vs StockMock's PDF (loss/trending days
   should match ~1%).
4. **Deploy** — commit the run files (`git add -f scratch/nifty_trend/runs/<slug>/
   scratch/nifty_trend/runs/index.json` — scratch is gitignored) + `strategy_registry.json`,
   push, VPS `git pull` + restart `algo-dashboard` (registry/Lab) and, if `sm_runner.py` or
   `_fire_sm_strategy` changed, `algo-monitor` (the loop).
5. **Verify** — VPS dry-check (read-only, no fire): parse config + `should_fire_today` for the
   next qualifying day; confirm the registry label + backtest run load.

That's it. No new Python for a standard leg-basket strategy.

---

## Validation reality (why our number ≠ StockMock exactly)

Matched on StockMock's exported per-expiry PDF (2026-08-05, "42% NIFTY EXPIRY DAY"):
- **Win-rate matches** (56.7% ≈ StockMock 56%) and **trending/loss days match ~1%** once
  entry uses the OPEN. SL fills exactly at entry×1.25 (SM 47.86→59.83 = our logic).
- **Absolute ₹ runs higher** because: (a) our lake is 2021→ (StockMock's low ₹ is dragged by
  2019-2023 low-premium years; its 2026 days match our magnitude), and (b) on **borderline
  spot days** our lake and StockMock's data pick an ATM ±50 apart → CE/PE flip → those days
  diverge. Not a bug — data-source + window. Report the our-data number honestly.

---

## Gotchas / rules

- **`_sm` is PAPER-locked.** Going live = a deliberate, separate change (backtest must be
  trusted first — Rule 10).
- **expiry-day-only strategies fire only on expiry** (NIFTY weekly = Tuesday in 2026). Not
  daily. Tell the user which day it fires.
- **Live data = NIFTY + BANKNIFTY only** (collector + Dhan). Other underlyings = backtest
  only.
- **`scratch/nifty_trend/` is gitignored** — new files there need `git add -f` or they never
  reach VPS.
- **Backtest window = 2021→, ATM±10 strikes, weekly expiry.** Far-OTM/ITM legs or pre-2021
  can't be backtested on our lake.

## File map
| Piece | File |
|---|---|
| Config parse + day-filter + expiry-detect (pure) | `_ops/sm_runner.py` |
| Live firing (basket gate + per-leg SL tags + unwind) | `trader_dashboard.py` → `_fire_sm_strategy` |
| Fire loop (daemon) | `trader_dashboard.py` → `sm_runner_loop`; thread in `monitor_daemon.py` |
| Exits (per-leg SL + EOD) | existing `pos_monitor_loop` (via `SL_TYPE:pct` tags) — no custom code |
| Backtest + Lab-report emit | `scratch/nifty_trend/sm_backtest.py` |
| Backtest → Stats calendar | `_ops/backtest_calendar.py` (reads `runs/<slug>/results.js`) |
| Registry | `strategy_registry.json` (`strategies` dict, family 02 = Volatility) |
| Runs output | `scratch/nifty_trend/runs/<slug>/` + `runs/index.json` |

Memory: `project_code3b_stockmock_parity`. Results schema: `scratch/nifty_trend/RESULTS_SCHEMA.md`.
