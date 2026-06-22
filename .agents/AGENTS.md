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
