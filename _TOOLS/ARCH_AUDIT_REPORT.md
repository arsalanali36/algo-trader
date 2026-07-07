# Architecture Audit Report

- **Run:** 2026-07-07 13:10
- **Files scanned:** 69
- **FAIL:** 0 | **WARN:** 1 | **Baselined debt:** 3

## FAILs

(none)

## WARNs

| Check | File:Line | Detail |
|---|---|---|
| STATE-PERSIST | `_TRADERS\universe_trader.py:75` | module-level '_state' looks persist-worthy but file has no json.dump/_save_* — restart will wipe it (see _pending_group_close/_kf_state pattern in trader_dashboard.py) |

## Baselined (pre-existing debt — Tasks 3+ scope)

| Check | File:Line | Detail |
|---|---|---|
| RAW-ORDER | `trader_dashboard.py:1027` | direct .place_order() call — use smart_order.execute() (or execution_gateway.execute_signal() once built)  [baselined pre-existing debt: 1/1 allowed] |
| RAW-ORDER | `_TRADERS\01_rsi_v1.py:333` | direct .place_order() call — use smart_order.execute() (or execution_gateway.execute_signal() once built)  [baselined pre-existing debt: 1/1 allowed] |
| BACKTEST-RISK | `_TOOLS\backtest_engine.py:1040` | backtest/simulation file mentions capital/concentration/drawdown on 1 line(s) but imports none of ['execution_gateway', 'risk_gate', 'smart_order', 'strategy_safety'] — backtest must share live risk rules (execute_signal(mode='backtest')) or be explicitly flagged to the user  [baselined pre-existing debt: 1/1 allowed] |

