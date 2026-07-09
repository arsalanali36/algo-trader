"""strategies.backtest — pluggable backtest strategy definitions.

Each module exposes evaluate(df, cfg, pos) OR backtest(df, cfg). Loaded by
name via strategies.load() / backtest_engine (leaf-based, backward-compatible
with legacy strategies.<name> paths). New AI-built strategies graduate here
from strategies/lab/ once proven.
"""
