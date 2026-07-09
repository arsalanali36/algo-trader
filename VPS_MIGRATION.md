# VPS Migration Checklist — 2026-07-09 folder refactor

> Local refactor DONE + committed. VPS deploy is DEFERRED (per user).
> Do this during a **market-closed window**, ideally with 0 open positions.
> Backup tag: `backup-pre-refactor-20260709_154105` (GitHub) + local tarball.

VPS: `root@72.61.173.32`  dir: `/root/CODE3B- TV BACKTEST ENGINE`  key: `~/.ssh/khazana_ed25519`

## What changed (why the VPS needs care)
- Root modules → `_core/`, `_data/`, `_ops/`. Live traders `_TRADERS/` → `strategies/live/`.
  Backtest strategies → `strategies/backtest/`. New `_paths.py` bootstrap.
- Entrypoints (`trader_dashboard.py`, `monitor_daemon.py`, `health_check.py`) **stay at root**
  → systemd ExecStart for `algo-dashboard`/`algo-monitor`/`algo-healthcheck` **unchanged**. ✅
- **No nifty_config `_module` migration needed** — loader is backward-compatible
  (`strategies.<id>` still resolves to `strategies.backtest.<id>`). ✅

## Steps (in order)

### 1. Confirm safe window
```bash
ssh … "cd '/root/CODE3B- TV BACKTEST ENGINE' && venv/bin/python -c \"import _core.order_store\" 2>/dev/null; \
       curl -s localhost:5099/api/positions | head -c 300"   # 0 open positions preferred
```

### 2. Deploy new structure (from local)
```bash
python "_ops/deploy_vps.py" --dry-run     # review file list first
python "_ops/deploy_vps.py"               # tarball scp + extract + restart (globs already updated)
```
`deploy_vps.py` extracts new folders alongside old — it does NOT delete. That's fine
(`_paths` puts `_core`/`_data` ahead of root, so new copies win) but clean up next:

### 3. Remove STALE old-location files on VPS (avoid edit-the-wrong-file confusion)
```bash
ssh … "cd '/root/CODE3B- TV BACKTEST ENGINE' && \
  rm -f risk_gate.py order_store.py smart_order.py broker_sync.py execution_gateway.py \
        webhook_executor.py strategy_safety.py daily_state.py mfe_routes.py \
        dhan_master.py dhan_feed.py dhan_rate_limiter.py kite_rate_limiter.py ltp_poller.py \
        shared_ltp_cache.py shared_candle_cache.py universe.py fno_universe.py \
        auto_data_downloader.py download_equity_history.py download_nifty50.py \
        export_trade_log.py rate_limit_verify.py optimize_strategy.py deploy_vps.py \
        sync_data.py sync_pine.py sync_vps_to_local.py && \
  rm -f _TRADERS/range_trader.py _TRADERS/universe_trader.py _TRADERS/01_rsi_v1.py \
        _TRADERS/nifty_ema_trader.py _TRADERS/range_config.json _TRADERS/NEW_STRATEGY_CHECKLIST.md && \
  rm -f strategies/always_buy.py strategies/bb_reversion.py strategies/rsi_v1.py \
        strategies/sample_ema.py strategies/vwap_ema_failure.py strategies/user_*.py && \
  echo 'stale removed'"
```
(Runtime `data/`, `logs/`, `nifty_config.json`, `_TRADERS/*.log` are untouched.)

### 4. Fix VPS systemd TIMERS that launch MOVED `_ops` scripts
Entrypoints stay at root, but these `_ops/` scripts are launched by their own units:
```bash
ssh … "grep -rlE 'auto_data_downloader|rate_limit_verify|download_equity_history' /etc/systemd/system/ 2>/dev/null"
```
For each unit found, edit `ExecStart` path `X.py` → `_ops/X.py` (WorkingDirectory stays repo root):
```bash
# e.g. algo-equity-daily.{service}, ratelimit-verify.{service}, any downloader unit
ssh … "sed -i 's#/download_equity_history.py#/_ops/download_equity_history.py#; \
               s#/rate_limit_verify.py#/_ops/rate_limit_verify.py#; \
               s# auto_data_downloader.py# _ops/auto_data_downloader.py#' \
               /etc/systemd/system/<unit>.service && systemctl daemon-reload"
```
Also check any `nohup … auto_data_downloader.py` launcher / crontab: `ssh … "crontab -l"`.

### 5. Restart + verify
```bash
ssh … "systemctl restart algo-dashboard algo-monitor && sleep 5 && \
  systemctl is-active algo-dashboard algo-monitor && \
  cd '/root/CODE3B- TV BACKTEST ENGINE' && \
  venv/bin/python -c 'import _paths, trader_dashboard; print(\"dashboard import OK\", trader_dashboard.TRADERS_DIR)' && \
  tail -20 logs/*.log 2>/dev/null | grep -iE 'error|traceback|no module' || echo 'no import errors'"
```
Then in the dashboard UI: start each active strategy (paper), confirm it launches from
`strategies/live/`, backtest dropdown loads a strategy, health-check green.

### 6. Health check
```bash
ssh … "cd '/root/CODE3B- TV BACKTEST ENGINE' && venv/bin/python -X utf8 health_check.py --all"
```

## Rollback
`git reset --hard backup-pre-refactor-20260709_154105` (local) → redeploy;
or extract `../CODE3B_BACKUP_pre-refactor_20260709_154117.tar.gz`.
