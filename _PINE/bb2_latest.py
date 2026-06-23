"""
Bollinger Band Bounce Strategy
- Buy when price bounces from lower band (closes above lower band after being below/at)
- Exit when price touches upper band
- Intraday only: forced exit at 15:15 IST
- Bollinger Bands in thick white color
"""

import pandas as pd
import numpy as np


def evaluate(df, cfg, pos):
    """
    Simple Bollinger Band bounce strategy with thick white bands.
    
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
    period = cfg.get('bb_window', 20)
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
    
    # In LONG position: exit at upper band
    elif pos == 'LONG':
        # Exit at upper band touch
        if curr_close >= curr_upper:
            return 'EXIT'
        return None
    
    # SHORT position (not used in this strategy)
    elif pos == 'SHORT':
        return None
    
    return None


def backtest(df, cfg):
    """
    Full backtest implementation with thick white Bollinger Bands.
    
    Args:
        df: pandas DataFrame with columns: time, open, high, low, close, volume
        cfg: dict with parameters
    
    Returns:
        (trades, df, plot_spec)
    """
    
    # Get parameters
    period = cfg.get('bb_window', 20)
    std_dev = cfg.get('bb_std', 2.0)
    sl_pct = cfg.get('sl_pct', 0.5) / 100
    tp_pct = cfg.get('tp_pct', 0) / 100
    
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
    
    # Force exit time: 15:15 IST
    force_exit_time = pd.Timestamp('15:15:00').time()
    
    # Iterate through bars
    for i in range(period + 1, len(df)):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        curr_time = curr_bar['time']
        
        # Force exit at 15:15 IST
        if position == 'LONG' and curr_time.time() >= force_exit_time:
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
        curr_high = curr_bar['high']
        curr_low = curr_bar['low']
        
        # Entry logic: bounce from lower band
        if position is None:
            if prev_close <= prev_lower and curr_close > curr_lower:
                if curr_time.time() < force_exit_time:
                    position = 'LONG'
                    entry_time = curr_time
                    entry_price = curr_close
        
        # Exit logic: touch upper band or SL/TP
        elif position == 'LONG':
            exit_signal = False
            exit_reason = ''
            
            # Exit at upper band touch
            if curr_high >= curr_upper:
                exit_signal = True
                exit_reason = 'Upper Band Touch'
            
            # Stop loss
            elif sl_pct > 0 and curr_low <= entry_price * (1 - sl_pct):
                exit_signal = True
                exit_reason = f'Stop Loss ({sl_pct*100:.1f}%)'
            
            # Take profit
            elif tp_pct > 0 and curr_high >= entry_price * (1 + tp_pct):
                exit_signal = True
                exit_reason = f'Take Profit ({tp_pct*100:.1f}%)'
            
            if exit_signal:
                trades.append({
                    'entry_time': entry_time,
                    'entry_price': entry_price,
                    'side': 'Long',
                    'exit_time': curr_time,
                    'exit_price': curr_close,
                    'exit_reason': exit_reason
                })
                position = None
                entry_time = None
                entry_price = None
    
    # Handle any open position at the end
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
    
    # Prepare plot spec with THICK WHITE Bollinger Bands
    plot_spec = {
        'type': 'line',
        'series': [
            {'name': 'close', 'color': 'black', 'width': 1},
            {'name': 'bb_upper', 'color': 'white', 'width': 4, 'linestyle': 'solid'},   # Thick white
            {'name': 'bb_lower', 'color': 'white', 'width': 4, 'linestyle': 'solid'},   # Thick white
            {'name': 'bb_middle', 'color': '#CCCCCC', 'width': 2, 'linestyle': 'dashed'}, # Light gray
        ],
        'fill': {
            'between': ['bb_upper', 'bb_lower'],
            'color': 'rgba(255, 255, 255, 0.05)'  # Very light white fill
        },
        'markers': {
            'buy': {'color': 'green', 'shape': '^', 'size': 10},
            'exit': {'color': 'red', 'shape': 'v', 'size': 10}
        }
    }
    
    return trades, df, plot_spec