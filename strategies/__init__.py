"""
strategies/ — strategy code home. Two subpackages + infra here at the root:

  strategies/backtest/  pluggable backtest strategies (evaluate/backtest contract)
  strategies/live/      live trader loops (range_trader, 01_rsi_v1, …)
  strategies/lab/       AI-built strategy lab (per-strategy folders + reports)

  base.py / custom_rule_engine.py / this __init__  = shared infra (stay at root).

Engine loads by name:  strategies.load("sample_ema").evaluate(...)
"""

import importlib


def resolve(name):
    """Return the strategy module for `name`, which may be a bare leaf
    ("sample_ema"), a legacy dotted path ("strategies.sample_ema") or the new
    one ("strategies.backtest.sample_ema"). Searches backtest/ first, then the
    legacy flat location — so existing nifty_config._module values keep working
    with NO migration after the 2026-07-09 restructure."""
    leaf = name.rsplit(".", 1)[-1]
    last_err = None
    for dotted in (f"strategies.backtest.{leaf}", f"strategies.{leaf}"):
        try:
            return importlib.import_module(dotted)
        except ModuleNotFoundError as e:
            last_err = e
            continue
    raise ModuleNotFoundError(
        f"strategy '{name}' not found in strategies.backtest or strategies") from last_err


def load(name):
    """Import a strategy by name and return the module (must have evaluate())."""
    mod = resolve(name)
    if not hasattr(mod, "evaluate"):
        raise AttributeError(f"strategy '{name}' has no evaluate(df, cfg, pos)")
    return mod
