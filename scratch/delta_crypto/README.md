# Delta Crypto — research reproducers (Phase 1/2 + Lab artifact)

Source working copy: `D:\KHAZANA\KHAZANA\PYTHON\_DELTA_CRYPTO\` (local). These are backed
up here in the algo-trader repo. The candle cache (`data/cache*`, ~81MB) is NOT committed —
it regenerates from Delta's public API on first run.

| File | What |
|------|------|
| `delta_client.py` | read-only Delta India public client (candles/symbol reconstruct) |
| `delta_broker.py` | early DeltaBroker copy (canonical lives in CODE3B `brokers/delta_broker.py`) |
| `backtest_delta.py` | iron-fly entry-time backtest (cache-efficient, real premium) |
| `backtest_v2.py` | + realistic per-leg slippage (measured spreads × crossing factor) |
| `backtest_weekly.py` / `analyze*.py` | weekly variant + significance (REJECTED — daily only) |
| `spread_probe.py` | live bid/ask spread measurement by moneyness |
| `build_delta_run.py` | **Lab artifact generator** → `scratch/nifty_trend/runs/delta_ironfly_btc/` |
| `probe_grid.py` / `smoke_broker.py` / `tail_check.py` | probes/tests |

Findings: Phase-2 winner = **daily iron-fly, enter 12h before 12:00 UTC expiry**
(significant p=0.001, slippage-proof, defined-risk). Weekly = no edge. Full history:
memory `project_delta_crypto_options` + CODE3B ADR-021.

Rebuild Lab artifact: `python build_delta_run.py` (run from the working copy with cache).
