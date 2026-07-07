# Architecture Audit Report

- **Run:** 2026-07-07 12:54
- **Files scanned:** 69
- **FAIL:** 7 | **WARN:** 1

## FAILs

| Check | File:Line | Detail |
|---|---|---|
| RAW-ORDER | `trader_dashboard.py:1027` | direct .place_order() call — use smart_order.execute() (or execution_gateway.execute_signal() once built) |
| RAW-ORDER | `_TRADERS\01_rsi_v1.py:343` | direct .place_order() call — use smart_order.execute() (or execution_gateway.execute_signal() once built) |
| DUP-INDICATOR | `_TRADERS\01_rsi_v1.py:253` | indicator-like function '_compute_rsi' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet |
| DUP-INDICATOR | `_TRADERS\range_trader.py:284` | indicator-like function 'compute_atr' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet |
| DUP-INDICATOR | `_TOOLS\backtest_engine.py:541` | indicator-like function '_compute_rsi' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet |
| BACKTEST-RISK | `_TOOLS\backtest_engine.py:1048` | backtest/simulation file mentions capital/concentration/drawdown on 1 line(s) but imports none of ['execution_gateway', 'risk_gate', 'smart_order', 'strategy_safety'] — backtest must share live risk rules (execute_signal(mode='backtest')) or be explicitly flagged to the user |
| DUP-INDICATOR | `strategies\rsi_v1.py:62` | indicator-like function '_rsi' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet |

## WARNs

| Check | File:Line | Detail |
|---|---|---|
| STATE-PERSIST | `_TRADERS\universe_trader.py:75` | module-level '_state' looks persist-worthy but file has no json.dump/_save_* — restart will wipe it (see _pending_group_close/_kf_state pattern in trader_dashboard.py) |

