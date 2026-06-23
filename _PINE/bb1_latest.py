"""
Bollinger Band Bounce Strategy - Python Implementation
- Buy when price bounces from lower band (closes above lower band after being below/at)
- Exit when price touches upper band
- Intraday only: forced exit at 15:15 IST
- Bollinger Bands in Pink Color
"""

import pandas as pd
import numpy as np


def evaluate(df, cfg, pos):
    """
    Bollinger Band bounce strategy with pink bands.
    
    Args:
        df: pandas DataFrame with columns: time, open, high, low, close, volume
        cfg: dict with parameters
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
    
    # SHORT position (if allowed)
    elif pos == 'SHORT':
        allow_short = cfg.get('allow_short', False)
        if allow_short:
            # Exit short at lower band touch
            if curr_close <= curr_lower:
                return 'EXIT'
        return None
    
    return None


def backtest(df, cfg):
    """
    Full backtest with pink Bollinger Bands.
    
    Args:
        df: pandas DataFrame with columns: time, open, high, low, close, volume
        cfg: dict with parameters
    
    Returns:
        (trades, df, plot_spec)
    """
    
    # Get parameters
    period = cfg.get('bb_window', 20)
    std_dev = cfg.get('bb_std', 2.0)
    allow_short = cfg.get('allow_short', False)
    sl_pct = cfg.get('sl_pct', 0.5) / 100  # Convert to decimal
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
    position = None  # 'LONG' or 'SHORT'
    entry_time = None
    entry_price = None
    entry_bar_high = None
    entry_bar_low = None
    
    # Force exit time: 15:15 IST
    force_exit_time = pd.Timestamp('15:15:00').time()
    
    # Iterate through bars
    for i in range(period + 1, len(df)):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        curr_time = curr_bar['time']
        
        # Force exit at 15:15 IST
        if position in ['LONG', 'SHORT'] and curr_time.time() >= force_exit_time:
            trades.append({
                'entry_time': entry_time,
                'entry_price': entry_price,
                'side': 'Long' if position == 'LONG' else 'Short',
                'exit_time': curr_time,
                'exit_price': curr_bar['close'],
                'exit_reason': 'Force Exit (15:15 IST)'
            })
            position = None
            entry_time = None
            entry_price = None
            entry_bar_high = None
            entry_bar_low = None
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
        
        # Entry logic: bounce from bands
        if position is None:
            # Long entry: bounce from lower band
            if prev_close <= prev_lower and curr_close > curr_lower:
                if curr_time.time() < force_exit_time:
                    position = 'LONG'
                    entry_time = curr_time
                    entry_price = curr_close
                    entry_bar_high = curr_high
                    entry_bar_low = curr_low
            
            # Short entry: bounce from upper band (if allowed)
            elif allow_short and prev_close >= prev_upper and curr_close < curr_upper:
                if curr_time.time() < force_exit_time:
                    position = 'SHORT'
                    entry_time = curr_time
                    entry_price = curr_close
                    entry_bar_high = curr_high
                    entry_bar_low = curr_low
        
        # Exit logic
        elif position == 'LONG':
            # Exit conditions for long
            exit_signal = False
            exit_reason = ''
            
            # 1. Touch upper band
            if curr_high >= curr_upper:
                exit_signal = True
                exit_reason = 'Upper Band Touch'
            
            # 2. Stop loss
            elif sl_pct > 0 and curr_low <= entry_price * (1 - sl_pct):
                exit_signal = True
                exit_reason = f'Stop Loss ({sl_pct*100:.1f}%)'
            
            # 3. Take profit
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
                entry_bar_high = None
                entry_bar_low = None
        
        elif position == 'SHORT':
            # Exit conditions for short
            exit_signal = False
            exit_reason = ''
            
            # 1. Touch lower band
            if curr_low <= curr_lower:
                exit_signal = True
                exit_reason = 'Lower Band Touch'
            
            # 2. Stop loss
            elif sl_pct > 0 and curr_high >= entry_price * (1 + sl_pct):
                exit_signal = True
                exit_reason = f'Stop Loss ({sl_pct*100:.1f}%)'
            
            # 3. Take profit
            elif tp_pct > 0 and curr_low <= entry_price * (1 - tp_pct):
                exit_signal = True
                exit_reason = f'Take Profit ({tp_pct*100:.1f}%)'
            
            if exit_signal:
                trades.append({
                    'entry_time': entry_time,
                    'entry_price': entry_price,
                    'side': 'Short',
                    'exit_time': curr_time,
                    'exit_price': curr_close,
                    'exit_reason': exit_reason
                })
                position = None
                entry_time = None
                entry_price = None
                entry_bar_high = None
                entry_bar_low = None
    
    # Handle any open position at the end
    if position in ['LONG', 'SHORT']:
        last_bar = df.iloc[-1]
        trades.append({
            'entry_time': entry_time,
            'entry_price': entry_price,
            'side': 'Long' if position == 'LONG' else 'Short',
            'exit_time': last_bar['time'],
            'exit_price': last_bar['close'],
            'exit_reason': 'End of Data'
        })
    
    # Prepare plot spec with PINK Bollinger Bands
    plot_spec = {
        'type': 'line',
        'series': [
            {'name': 'close', 'color': 'black', 'width': 1},
            {'name': 'bb_upper', 'color': '#FF69B4', 'width': 2, 'linestyle': 'solid'},  # Pink
            {'name': 'bb_lower', 'color': '#FF69B4', 'width': 2, 'linestyle': 'solid'},  # Pink
            {'name': 'bb_middle', 'color': '#FFB6C1', 'width': 1.5, 'linestyle': 'dashed'},  # Light Pink
        ],
        'fill': {
            'between': ['bb_upper', 'bb_lower'],
            'color': 'rgba(255, 105, 180, 0.1)'  # Semi-transparent pink
        },
        'markers': {
            'buy': {'color': 'green', 'shape': '^', 'size': 8},
            'sell': {'color': 'red', 'shape': 'v', 'size': 8},
            'exit': {'color': 'orange', 'shape': 'o', 'size': 6}
        }
    }
    
    return trades, df, plot_spec