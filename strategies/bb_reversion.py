"""
bb_reversion.py — Bollinger Bands Mean Reversion Strategy.

Entry:
- BUY: Price closes below the Lower Bollinger Band.
- SELL (Short): Price closes above the Upper Bollinger Band (if allow_short is True).

Exit:
- EXIT Long: Price closes above the Middle Band (SMA 20).
- EXIT Short: Price closes below the Middle Band (SMA 20).

Optimized for Indian Equity Daily timeframe (where shorting overnight is typically not allowed, 
so allow_short is False by default).
"""

def evaluate(df, cfg, pos):
    window = int(cfg.get("bb_window", 20))
    if df is None or len(df) < window + 2:
        return None
    
    std_dev = float(cfg.get("bb_std", 2.0))
    # Convert string boolean from json to actual bool if needed, though get() handles raw types.
    allow_short = cfg.get("allow_short", False)
    if isinstance(allow_short, str):
        allow_short = allow_short.lower() == "true"
    
    close = df["close"].astype(float)
    
    # Calculate Bollinger Bands using pandas rolling
    sma = close.rolling(window=window).mean()
    std = close.rolling(window=window).std()
    upper_band = sma + (std * std_dev)
    lower_band = sma - (std * std_dev)
    
    # Current values (last closed candle is index -2, since -1 is forming)
    c_close = close.iloc[-2]
    c_sma = sma.iloc[-2]
    c_upper = upper_band.iloc[-2]
    c_lower = lower_band.iloc[-2]

    # Evaluate based on current position state
    if pos is None:
        # No open position, look for entries
        if c_close < c_lower:
            return "BUY"
        elif allow_short and c_close > c_upper:
            return "SELL"
            
    elif pos == "LONG":
        # Mean reversion achieved -> exit at middle band
        if c_close > c_sma:
            return "EXIT"
            
    elif pos == "SHORT":
        # Mean reversion achieved -> exit at middle band
        if c_close < c_sma:
            return "EXIT"

    return None
