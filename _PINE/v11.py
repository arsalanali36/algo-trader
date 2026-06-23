"""
Bollinger Band Bounce Strategy
- Buy when price bounces from lower band (closes above lower band after being below/at)
- Exit when price touches upper band
- Intraday only: forced exit at 15:15 IST
"""

import pandas as pd
import numpy as np

# Optional config header defaults
# symbol: NIFTY
# timeframe: 5m
# qty: 1


def evaluate(df, cfg, pos):
    """
    Simple Bollinger Band bounce strategy.
    
    Args:
        df: pandas DataFrame with columns: time, open, high, low, close, volume
        cfg: dict with parameters (use cfg.get('key', default))
        pos: 'LONG' | 'SHORT' | None
    
    Returns:
        'BUY' | 'SELL' | 'EXIT' | None
    """
    
    # Guard against insufficient data
    if len(df) < 20:
        return None
    
    # Get parameters with defaults
    period = cfg.get('bb_period', 20)
    std_dev = cfg.get('bb_std', 2.0)
    
    # Current and previous bars
    curr = df.iloc[-1]
    prev = df.iloc[-2]
    
    # Calculate Bollinger Bands
    close = df['close']
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    upper_band = sma + (std * std_dev)
    lower_band = sma - (std * std_dev)
    
    # Current and previous band values
    curr_lower = lower_band.iloc[-1]
    curr_upper = upper_band.iloc[-1]
    prev_lower = lower_band.iloc[-2]
    prev_upper = upper_band.iloc[-2]
    
    # Current and previous close
    curr_close = curr['close']
    prev_close = prev['close']
    
    # No position: look for bounce from lower band
    if pos is None:
        # Buy signal: price was at or below lower band, now closes above it (bounce)
        if prev_close <= prev_lower and curr_close > curr_lower:
            return 'BUY'
        return None
    
    # In LONG position: exit at upper band or force exit conditions
    elif pos == 'LONG':
        # Exit at upper band touch
        if curr_close >= curr_upper:
            return 'EXIT'
        return None
    
    # SHORT position (not used in this strategy, but handle gracefully)
    elif pos == 'SHORT':
        return None
    
    return None


def backtest(df, cfg):
    """
    Full backtest implementation with trade tracking.
    
    Args:
        df: pandas DataFrame with columns: time, open, high, low, close, volume
        cfg: dict with parameters
    
    Returns:
        (trades, df, plot_spec)
    """
    
    # Get parameters
    period = cfg.get('bb_period', 20)
    std_dev = cfg.get('bb_std', 2.0)
    
    # Calculate Bollinger Bands
    close = df['close']
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    df['bb_upper'] = sma + (std * std_dev)
    df['bb_lower'] = sma - (std * std_dev)
    df['bb_middle'] = sma
    
    # Initialize tracking variables
    trades = []
    position = None  # 'LONG' or None
    entry_time = None
    entry_price = None
    
    # Force exit time: 15:15 IST (convert to datetime if available)
    force_exit_time = pd.Timestamp('15:15:00').time()
    
    # Iterate through bars (starting from period+1 to have valid bands)
    for i in range(period + 1, len(df)):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        curr_time = curr_bar['time']
        
        # Force exit at 15:15 IST
        if position == 'LONG' and curr_time.time() >= force_exit_time:
            # Exit position
            trades.append({
                'entry_time': entry_time,
                'entry_price': entry_price,
                'side': 'Long',
                'exit_time': curr_time,
                'exit_price': curr_bar['close'],
                'exit_reason': 'Force Exit (15:15 IST)'
            })
            position = None
            entry_time = None
            entry_price = None
            continue
        
        # Get band values
        curr_lower = curr_bar['bb_lower']
        curr_upper = curr_bar['bb_upper']
        prev_lower = prev_bar['bb_lower']
        prev_upper = prev_bar['bb_upper']
        
        curr_close = curr_bar['close']
        prev_close = prev_bar['close']
        
        # Entry logic: bounce from lower band
        if position is None:
            if prev_close <= prev_lower and curr_close > curr_lower:
                # Check if we're not past force exit time
                if curr_time.time() < force_exit_time:
                    position = 'LONG'
                    entry_time = curr_time
                    entry_price = curr_close
        
        # Exit logic: touch upper band
        elif position == 'LONG':
            if curr_close >= curr_upper:
                trades.append({
                    'entry_time': entry_time,
                    'entry_price': entry_price,
                    'side': 'Long',
                    'exit_time': curr_time,
                    'exit_price': curr_close,
                    'exit_reason': 'Upper Band Touch'
                })
                position = None
                entry_time = None
                entry_price = None
    
    # Handle any open position at the end (shouldn't happen with force exit, but just in case)
    if position == 'LONG':
        last_bar = df.iloc[-1]
        trades.append({
            'entry_time': entry_time,
            'entry_price': entry_price,
            'side': 'Long',
            'exit_time': last_bar['time'],
            'exit_price': last_bar['close'],
            'exit_reason': 'End of Data'
        })
    
    # Prepare plot spec
    plot_spec = {
        'type': 'line',
        'series': [
            {'name': 'close', 'color': 'black'},
            {'name': 'bb_upper', 'color': 'red', 'linestyle': 'dashed'},
            {'name': 'bb_lower', 'color': 'green', 'linestyle': 'dashed'},
            {'name': 'bb_middle', 'color': 'blue', 'linestyle': 'dotted'}
        ]
    }
    
    return trades, df, plot_spec