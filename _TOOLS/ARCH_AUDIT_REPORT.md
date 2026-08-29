# Architecture Audit Report

- **Run:** 2026-08-19 09:21
- **Files scanned:** 330
- **FAIL:** 0 | **WARN:** 1 | **Baselined debt:** 14

## FAILs

(none)

## WARNs

| Check | File:Line | Detail |
|---|---|---|
| STATE-PERSIST | `strategies\live\universe_trader.py:96` | module-level '_state' looks persist-worthy but file has no json.dump/_save_* — restart will wipe it (see _pending_group_close/_kf_state pattern in trader_dashboard.py) |

## Baselined (pre-existing debt — Tasks 3+ scope)

| Check | File:Line | Detail |
|---|---|---|
| RAW-HTTP-ORDER | `trader_dashboard.py:5173` | raw HTTP .post() to a broker ORDER endpoint — bypasses smart_order/execution_gateway, so RMS gating, rate-limiting, async fill-confirm and order_store recording ALL skip. Route it through execution_gateway.execute_signal()/execute_exit()  [baselined pre-existing debt: 1/1 allowed] |
| DUP-INDICATOR | `strategies\live\03_orbst_trader.py:172` | indicator-like function '_supertrend_dir' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 1/1 allowed] |
| INLINE-SIGNAL | `strategies\live\07_banknifty_trader.py:196` | inline ORB opening-range signal math in a live trader — call the shared strategies/signals/* module (orb.orb_signal_last / chain_zone.*), the SAME code the backtest runs, so live == backtest by construction (TRAP #153). Do NOT keep a private copy; if this file is a deliberate not-yet-migrated exception add '# inline-signal-ok: <reason>'  [baselined pre-existing debt: 1/1 allowed] |
| RAW-HTTP-ORDER | `strategies\live\nifty_ema_trader.py:265` | raw HTTP .post() to a broker ORDER endpoint — bypasses smart_order/execution_gateway, so RMS gating, rate-limiting, async fill-confirm and order_store recording ALL skip. Route it through execution_gateway.execute_signal()/execute_exit()  [baselined pre-existing debt: 2/2 allowed] |
| RAW-HTTP-ORDER | `strategies\live\nifty_ema_trader.py:303` | raw HTTP .post() to a broker ORDER endpoint — bypasses smart_order/execution_gateway, so RMS gating, rate-limiting, async fill-confirm and order_store recording ALL skip. Route it through execution_gateway.execute_signal()/execute_exit()  [baselined pre-existing debt: 2/2 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\_positional_hunt.py:16` | indicator-like function 'atr' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 3/3 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\_positional_hunt.py:21` | indicator-like function 'sma' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 3/3 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\_positional_hunt.py:22` | indicator-like function 'rsi' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 3/3 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\engine.py:27` | indicator-like function 'ema' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 3/3 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\engine.py:30` | indicator-like function 'atr' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 3/3 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\engine.py:36` | indicator-like function 'bollinger' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 3/3 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\intraday_engine.py:56` | indicator-like function 'rsi' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 2/2 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\intraday_engine.py:64` | indicator-like function 'supertrend' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 2/2 allowed] |
| DUP-INDICATOR | `scratch\nifty_trend\ml_features.py:78` | indicator-like function '_atr' defined outside _CHARTING/ — import from _CHARTING/indicators.py (INDICATOR_REGISTRY) instead, or add it THERE if it doesn't exist yet  [baselined pre-existing debt: 1/1 allowed] |

