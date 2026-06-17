"""
sample_ema.py — scaffold strategy: EMA fast/slow crossover.

Used to exercise the universe engine end-to-end before the user's real Pine
strategy is converted. Config keys: fast_ema (9), slow_ema (20).
"""


def evaluate(df, cfg, pos):
    if df is None or len(df) < int(cfg.get("slow_ema", 20)) + 2:
        return None
    fast = int(cfg.get("fast_ema", 9))
    slow = int(cfg.get("slow_ema", 20))
    close = df["close"].astype(float)
    ef = close.ewm(span=fast, adjust=False).mean()
    es = close.ewm(span=slow, adjust=False).mean()

    # use last CLOSED candle (-2) vs the one before (-3) to confirm the cross
    cf, cs = ef.iloc[-2], es.iloc[-2]
    pf, ps = ef.iloc[-3], es.iloc[-3]

    if pf <= ps and cf > cs:
        return "BUY"
    if pf >= ps and cf < cs:
        return "SELL"
    return None
