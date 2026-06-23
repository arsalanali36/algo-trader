# Script Contract — CODE3B Backtest Engine

How to write a strategy that the **📜 Script** library can run. Two options:
**DSL rule-block** (lightest) or a **full Python file** (any logic). Paste/upload it in
the Script tab → pick language → Save → it appears in the Backtest Results dropdown.

> Pine scripts are stored for reference/version-history only — they run on TradingView,
> not in this engine. Only Python and DSL scripts are runnable here.

---

## Option A — DSL Rule-block (no .py file, runs via `custom_rule_engine`)

Paste `key = value` lines (`//` comments allowed). `entry_*` / `exit_*` are Python
expressions evaluated each bar; everything else is a parameter.

```
// --- Entry ---
bb_window   = 20
bb_std      = 2
entry_long  = c_close < c_lower
entry_short = allow_short and c_close > c_upper
// --- Exit ---
exit_long   = c_high >= c_upper
exit_short  = c_low  <= c_lower
sl_pct      = 0.5
tp_pct      = 0
// --- Other ---
symbol      = NIFTY
timeframe   = 5m
allow_short = false
max_trades_per_day = 0
```

**Variables in `entry_*` / `exit_*` expressions:**

| Var | Meaning |
|-----|---------|
| `c_open c_close c_high c_low` | current bar OHLC |
| `c_sma` | SMA(`bb_window`) |
| `c_upper c_lower` | Bollinger bands (SMA ± `bb_std`·σ) |
| `c_ema_52 c_ema_100` | EMA(`ema_52_period`=260) / EMA(`ema_100_period`=100) |
| `c_atr` | ATR(`atr_len`=14) |
| `c_body_pct` | abs(open−close)/close·100 |
| `entry_price entry_candle_high entry_candle_low` | open-position context (None if flat) |
| `allow_short`, `abs()` | short toggle, Python abs |

**Params:** `bb_window bb_std ema_52_period ema_100_period atr_len sl_pct tp_pct
allow_short max_trades_per_day symbol timeframe`

`sl_pct`/`tp_pct` are % of entry price (0 = off). 3:15 IST EOD exit is always enforced.

---

## Option B — Full Python file (any indicator/logic)

Implement **either** `evaluate` (simplest) **or** `backtest`:

```python
# symbol: NIFTY        <- optional config header (read on save)
# timeframe: 5m
# qty: 1

def evaluate(df, cfg, pos):
    """
    df  : pandas DataFrame, oldest->newest, columns time/open/high/low/close/volume.
          History UP TO AND INCLUDING the current bar (df.iloc[-1] = current).
    cfg : dict of params (cfg.get('x', default)).
    pos : 'LONG' | 'SHORT' | None.
    return: 'BUY' | 'SELL' | 'EXIT' | None.
    """
    if len(df) < 25:
        return None
    c = df['close']
    fast = c.ewm(span=9,  adjust=False).mean().iloc[-1]
    slow = c.ewm(span=21, adjust=False).mean().iloc[-1]
    if pos is None and fast > slow:
        return 'BUY'
    if pos == 'LONG' and fast < slow:
        return 'EXIT'
    return None
```

```python
def backtest(df, cfg):
    # full control. return (trades, df, plot_spec)
    # trades: list of {entry_time, entry_price, side('Long'/'Short'),
    #                  exit_time, exit_price, exit_reason}
    from _CHARTING import spec as chspec
    df['bb_upper'] = ...        # compute indicators as DataFrame columns
    df['bb_lower'] = ...
    plot_spec = chspec.build_plot_spec(df, indicators=[
        {"name": "BB Upper", "series": df["bb_upper"], "type": "line", "color": "#FF69B4"},
        {"name": "BB Lower", "series": df["bb_lower"], "type": "line", "color": "#FF69B4"},
    ])
    return trades, df, plot_spec
```

### Drawing indicators on the chart — IMPORTANT

The chart only understands the `plot_spec` produced by **`build_plot_spec`**. Do **not**
hand-build your own `{type, series, fill, markers}` dict — it will be silently ignored.

- Only `def backtest` can draw lines (the `evaluate` path returns no `plot_spec`).
- Each indicator: `{"name": str, "series": pandas.Series (aligned with df), "type":
  "line"|"histogram", "color": "#hex", "overlay": True}`. `overlay=False` = own bottom panel (e.g. RSI).
- The chart already draws candles + buy/sell/exit markers — only add **indicator lines** here.
- `plot_spec` may be `None` if you don't need any lines.

**Engine behaviour (evaluate path):** next-bar-open fill, `max_trades_per_day` cap,
reversal handling (opposite signal closes + flips), and a hard **15:15 IST force-exit /
no re-entry** (project rule — never hold overnight).

**Allowed:** pure pandas / numpy, and `from _CHARTING import indicators` (EMA/RSI/ATR/
VWAP/SMA/BBANDS). No network, no file I/O, no new pip installs.

**Data:** `symbol` = `NIFTY`/`BANKNIFTY` → index 1-min from Dhan; anything else →
equity 1-min. Resampled to `timeframe` before your code sees it.

---

## MASTER PROMPT (give this to DeepSeek / any AI)

```
You are writing a backtest strategy for my custom Python engine (NOT TradingView,
NOT backtrader). Output ONLY one self-contained Python file — no prose.

Implement EITHER:
(A) def evaluate(df, cfg, pos):
    # df: pandas DataFrame oldest->newest, cols time,open,high,low,close,volume,
    #     history up to and including the current bar (last row = current).
    # cfg: dict (cfg.get('x', default)). pos: 'LONG'|'SHORT'|None.
    # return exactly one of 'BUY'|'SELL'|'EXIT'|None. Guard len(df).
(B) def backtest(df, cfg):  # return (trades, df, plot_spec)

To draw indicator lines on the chart, ONLY this works (do not invent a plot_spec shape):
    from _CHARTING import spec as chspec
    df['x'] = ...   # compute as a DataFrame column
    plot_spec = chspec.build_plot_spec(df, indicators=[
        {"name":"X","series":df["x"],"type":"line","color":"#FF69B4"}])
    return trades, df, plot_spec
(evaluate() cannot draw lines — use backtest() if you need chart indicators.)

RULES:
- Pure pandas/numpy only (+ 'from _CHARTING import spec as chspec' for plot_spec). No network, file I/O, or extra installs.
- Intraday only: engine force-exits at 15:15 IST and blocks entries after — no overnight.
- Optional comment header for defaults:
    # symbol: NIFTY      (NIFTY/BANKNIFTY = index; else equity)
    # timeframe: 5m      (1m/3m/5m/15m/30m)
    # qty: 1
- Keep it simple — fewer conditions = more robust.

Strategy to implement: <DESCRIBE YOUR STRATEGY HERE>
```

The same prompt + DSL cheatsheet is available in the dashboard: **Script tab → 📋 Master Prompt**.
