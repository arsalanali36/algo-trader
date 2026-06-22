import pandas as pd
from _TOOLS.backtest_engine import _run_bb

cfg = {
    "symbol": "HDFCBANK",
    "timeframe": "1m",
    "bb_std": 2,
    "bb_window": 20,
    "max_trades_per_day": 0,
    "entry_long": "c_close < c_lower",
    "exit_long": "c_close < (entry_price - (2 * c_atr))"
}

try:
    trades, df, spec = _run_bb("2026-06-01", "2026-06-20", cfg)
    print(f"Total trades: {len(trades)}")
    if len(trades) > 0:
        print(trades[0])
except Exception as e:
    import traceback
    traceback.print_exc()
