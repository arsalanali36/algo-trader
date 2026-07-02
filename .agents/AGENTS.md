# 🚀 Strategy Development Rules & Standard Memory

These rules MUST be followed whenever generating or modifying backtesting strategies in this workspace.

## 1. Fast Workflow Rule (CRITICAL)
- The user prefers an extremely fast workflow.
- **DO NOT** over-read files, over-plan, or over-verify unless absolutely necessary.
- When the user asks for a feature, directly implement the requested code snippet or apply the fix quickly. Speed and accuracy are the top priorities.

## 2. Standardized Naming Convention (Live Log Memory)
To make things easy for the user to memorize and reuse, ALWAYS use these specific variable names when injecting data into the `eval` environment (`env`) of ANY new or updated strategy engine:

**Candle Data:**
- `c_open` : Current candle Open price
- `c_high` : Current candle High price (CRUCIAL: User frequently uses this for wicks touching bands/levels)
- `c_low` : Current candle Low price
- `c_close` : Current candle Close price
- `c_volume` : Current candle Volume
- `c_atr` : Current candle ATR (Average True Range)

**Trade Data:**
- `ep` or `entry_price` : The price at which the current position was entered.
- `pos` : Current position status (`1` for Long, `-1` for Short, `0` for Flat)
- `entry_candle_high` : The High price of the candle that triggered the entry.
- `entry_candle_low` : The Low price of the candle that triggered the entry.

**Standard Config Options:**
- `max_trades_per_day` : Limit number of trades per day (0 = unlimited). You must implement this logic in the strategy loop.
- `sl_pct` : Stop Loss percentage (e.g., `1.0` for 1%).
- `tp_pct` : Take Profit percentage (e.g., `2.0` for 2%).

## 3. Dynamic Strategy Architecture
- Any new strategy you build MUST support a dynamic **Rule Engine** like the `bb` strategy. 
- You should provide `entry_long`, `entry_short`, `exit_long`, `exit_short` as string config parameters.
- Compile them once before the loop (using `compile(..., "<string>", "eval")`) and evaluate them via `eval()` inside the loop for maximum user flexibility.

## 4. UI Organization
- Whenever adding fields to the UI Text Editor (`templates/backtest_chart.html`), automatically group them under comment headers:
  - `// --- General ---` (for timeframe, instrument, max_trades, etc.)
  - `// --- Entry ---` (for entry logic and entry-specific indicators)
  - `// --- Exit ---` (for exit logic, SL, TP, etc.)

## 5. Global RMS & Trailing SL Specifications (Memory)
- **JSON Settings Keys** (stored in `nifty_config.json` via `/api/risk-config` endpoint):
  - `default_sl_type`: string (e.g., `'pct'`, `'pt'`, `'trailing_pt'`, `'rs'`, `'premium'`, `'index'`).
  - `default_sl_val`: string (e.g., `'10'` or `'10:2.5'`).
  - `default_sl_candle_close`: boolean checkbox.
  - `default_tp_type`: string (target does NOT support `'trailing_pt'`).
  - `default_tp_val`: string.
  - `default_tp_candle_close`: boolean checkbox.
  - `default_sl_rs`: legacy fallback (displayed in the "Daily Profit Target" UI section, but keys are unchanged).
- **Trailing Stop Loss (`trailing_pt`) Rules**:
  - Users enter trailing SL value as `gap:step` (e.g., `10` or `10:2.5`). If step is omitted, it defaults to the minimum step of the premium range.
  - **Premium-based Minimum Steps** (Zerodha GTT option premium guidelines):
    * Entry Price ₹0 to ₹50: minimum step **₹1.0**
    * Entry Price ₹50 to ₹100: minimum step **₹2.50**
    * Entry Price ₹100 to ₹500: minimum step **₹5.0**
    * Entry Price Above ₹500: minimum step **₹10.0**
  - **Dynamic Correction**: Enforced in `/api/orders/update-sl-tp` (saves corrected step value) and dynamically checked inside `_generic_px` in `trader_dashboard.py` at runtime.
- **Directional Trailing Logic** (inside `_generic_px`):
  - **BUY (Long)**: Stop Loss trails **UP** when `max_ltp` rises.
  - **SELL (Short/Option-selling)**: Stop Loss trails **DOWN** when `min_ltp` drops.
- **Candle Close Checkbox Integration**:
  - Stamped on positions as tags: `"SL_CANDLE_CLOSE:true"` and `"TP_CANDLE_CLOSE:true"`.
  - When present, the exit check inside `pos_monitor_loop` checks target/SL breach conditions against the **last closed 1-minute candle's close price** instead of the live tick LTP.
  - Displayed in open positions table grid using a `🕯️` emoji next to the target/SL text.

