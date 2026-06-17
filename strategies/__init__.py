"""
strategies/ — pluggable strategies. Each module exposes evaluate(df, cfg, pos).

Engine loads by name:  strategies.load("sample_ema").evaluate(...)
User's Pine Script gets converted into a module here (Phase 5).
"""

import importlib


def load(name):
    """Import strategies.<name> and return the module (must have evaluate())."""
    mod = importlib.import_module(f"strategies.{name}")
    if not hasattr(mod, "evaluate"):
        raise AttributeError(f"strategy '{name}' has no evaluate(df, cfg, pos)")
    return mod
