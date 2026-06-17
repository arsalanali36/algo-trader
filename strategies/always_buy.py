"""always_buy.py — TEST strategy: BUY when flat. Used to exercise the engine's
routing + order + caps path without waiting for a real crossover. Not for live."""


def evaluate(df, cfg, pos):
    if df is None or df.empty:
        return None
    return "BUY" if pos is None else None
