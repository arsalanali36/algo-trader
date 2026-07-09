# CODE3B — Refactor & Reorganization Plan
**Date:** 2026-07-09 | **Owner:** Arsalan | **Status:** IN-PROGRESS

## Backup (done before any change)
- Git tag `backup-pre-refactor-20260709_154105` → pushed to GitHub `origin` ✅
- Local tarball `../CODE3B_BACKUP_pre-refactor_20260709_154117.tar.gz` (54M) ✅
- Restore: `git reset --hard backup-pre-refactor-20260709_154105` OR extract tarball.

## Why
118 top-level files, 62 `.py` at root. Real core modules, standalone scripts, and
scratch/junk all sitting together. Goal: logical folders so future strategies +
their optimization/Monte-Carlo results have a clear home.

## Key constraint (why we go careful)
- `trader_dashboard.py` uses **flat imports** (`import risk_gate`) — works because
  `BASE_DIR` is on `sys.path`. Moving core modules → must add new dirs to `sys.path`
  (bootstrap) so flat imports keep resolving. NO import-statement rewrites needed.
- `strategies/` is loaded by **dotted path** `strategies.<name>` (loader + live
  `nifty_config._module`). Nesting → loader change + live-config migration.
- systemd (VPS): `trader_dashboard.py`, `health_check.py`, `monitor_daemon.py` run
  from repo root by name → these 3 stay at root.
- `deploy_vps.py` has explicit `ROOT_FILES` list → must update on every move.
- Live project: TradingView webhook hits VPS. VPS redeploy + verify after Phase 3/4.

---

## Target structure
```
CODE3B/
├── trader_dashboard.py   monitor_daemon.py   health_check.py   ← stay at root (systemd)
├── CLAUDE.md  LESSONS.md  ARCHITECTURE_LOG.md  REFACTOR_PLAN.md  requirements.txt  .gitignore
│
├── _core/     risk_gate, order_store, smart_order, broker_sync, execution_gateway,
│              webhook_executor, strategy_safety, daily_state, mfe_routes
├── _data/     dhan_master, dhan_feed, dhan_rate_limiter, kite_rate_limiter, ltp_poller,
│              shared_ltp_cache, shared_candle_cache, universe, fno_universe
├── _ops/      auto_data_downloader, download_equity_history, download_nifty50,
│              export_trade_log, rate_limit_verify, optimize_strategy, deploy_vps,
│              sync_data, sync_pine, sync_vps_to_local
│
├── strategies/
│   ├── backtest/   pluggable backtest strategies (evaluate/backtest contract) + base.py,
│   │               custom_rule_engine.py + user_*_v{n}.py (Script Library)
│   ├── live/       live trader loops (range_trader, universe_trader, 01_rsi_v1, nifty_ema_trader)
│   └── lab/        ← NEW: AI-built strategy lab (per-strategy folders, see scope below)
│
├── _TOOLS/  _CHARTING/  _PINE/  _ADR/  _DEPLOY/   ← already organized
│   └── _PINE/_py_snapshots/   ← move _PINE/*.py history here (keep .pine clean)
├── brokers/  templates/  static/  data/  images/  results/  logs/   ← fine
└── _DEV/
    ├── tests/      _test_*.py
    └── mockups/    rms_mockup.html, traps_resolved_presentation.html
```

---

## Phases (safest first)

### Phase 1 — Junk cleanup (ZERO app risk)
- DELETE tracked junk: `scratch.py`, `scratch_test.py`, `backtest_db.json`(0b),
  `patch.diff`(0b), `patch_utf8.diff`(0b), `full_patch.txt`(6b)
- MOVE `_test_*.py` → `_DEV/tests/`; mockup HTML → `_DEV/mockups/`
- Sweep physical untracked scratch (already gitignored: `test_*.js`, `dump*`,
  `puppeteer_*`, `patch_*.py`, `delete_*.py`, `old_*`, `*.png`) into `scratch/`.
- Commit. No imports/systemd touched.

### Phase 2 — `_ops/` (LOW risk — standalone scripts)
- Move ops scripts into `_ops/`. Update references in `broker_sync.py`,
  `trader_dashboard.py` (auto_data_downloader launcher), `deploy_vps.py`, `fno_universe.py`.
- VPS timers (`algo-equity-daily`, `ratelimit-verify`) reference script paths → update
  systemd ExecStart on VPS.

### Phase 3 — `_core/` + `_data/` (MEDIUM risk)
- Move modules. Add `_core/`, `_data/` to `sys.path` at top of: trader_dashboard,
  health_check, monitor_daemon, each `strategies/live/*` trader, `_TOOLS/backtest_engine`.
- Update `deploy_vps.py` ROOT_FILES → `_core/*.py`, `_data/*.py` globs.
- VPS redeploy → restart `algo-dashboard`+`algo-monitor` → verify active + no ImportError in logs.

### Phase 4 — strategies restructure (MEDIUM-HIGH risk — live config migration)
- `strategies/*.py` → `strategies/backtest/`; `_TRADERS/*.py` → `strategies/live/`.
- Update loader `strategies/__init__.py` (search backtest/), path builders in
  trader_dashboard (`strategies/{id}.py` → `strategies/backtest/{id}.py`), `_module`
  namespace (`strategies.<id>` → `strategies.backtest.<id>`), `TRADERS_DIR`, deploy globs,
  architecture_audit SCAN_DIRS.
- **Live-config migration:** rewrite `nifty_config._module` values on VPS.
- VPS redeploy + verify each active strategy still loads + backtest dropdown works.

### Phase 5 — docs
- Update CLAUDE.md project map + Master Feature Index + `NEW_STRATEGY_CHECKLIST.md`
  with new homes. ARCHITECTURE_LOG + Update Log entries.

---

## SCOPE (future) — AI-built Strategy Lab  (`strategies/lab/`)
Reference: Jesse-framework workflow (build → backtest → Monte Carlo → significance
test → optimize → walk-forward → report). NOT built now — folder + convention only.

Per-strategy self-contained folder:
```
strategies/lab/<strategy_name>/
├── strategy.py          strategy code + params (min/max/default, like Jesse hyperparameters())
├── spec.md              the conditions/params I was given ("entry when X, exit when Y")
├── config.json          instrument, timeframe, date range, param grid
├── reports/
│   ├── backtest.md      metrics: Sharpe/Sortino/Calmar, drawdown, expectancy, win-rate
│   ├── monte_carlo.json MC distribution (N resampled sessions) + percentile bands
│   ├── optimization.json hyperparameter sweep → ranked candidates
│   └── walk_forward.md  out-of-sample validation (clean OOS Sharpe)
└── results/
    ├── equity_curve.png / drawdown.png
    └── dashboard.html   self-contained results dashboard (Jesse-style)
```
Engine components to build later (reuse existing `_TOOLS/backtest_engine.py`,
`optimizer.py`, `_CHARTING/`):
- Monte Carlo resampler (trade-order shuffle / block bootstrap → distribution)
- Optimizer wrapper writing ranked candidates + significance test
- Report/dashboard generator (HTML, per-strategy)
Promotion path: lab strategy proven → copy to `strategies/backtest/` (paper) →
`strategies/live/` (live trader loop).
