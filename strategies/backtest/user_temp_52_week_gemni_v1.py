# // --- General ---
# symbol: NIFTY
# timeframe: 1D
# qty: 1

# // --- Entry ---
# ema_period: 260

# // --- Exit ---
# atr_period: 14
# atr_multiplier: 2.0

import pandas as pd
import numpy as np

def backtest(df, cfg):
    ema_period = cfg.get('ema_period', 260)  # 52 weeks * 5 days = 260
    atr_period = cfg.get('atr_period', 14)
    atr_mult = cfg.get('atr_multiplier', 2.0)
    
    # Calculate 52-week EMA
    df['ema_52'] = df['close'].ewm(span=ema_period, adjust=False).mean()
    
    # Calculate ATR
    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['atr'] = tr.rolling(atr_period).mean()
    
    trades = []
    position = None
    entry_time = None
    entry_price = None
    
    # This column holds the Trailing SL value. 
    # It remains NaN when not in a trade, fulfilling the "visible only after entry" requirement.
    sl_line = np.full(len(df), np.nan)
    current_sl = None
    
    for i in range(max(ema_period, atr_period), len(df)):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        
        curr_time = curr_bar['time']
        curr_close = curr_bar['close']
        curr_low = curr_bar['low']
        
        prev_close = prev_bar['close']
        curr_ema = curr_bar['ema_52']
        prev_ema = prev_bar['ema_52']
        curr_atr = curr_bar['atr']
        
        if position is None:
            # Positional Entry: Price crosses above the 52-week EMA from below
            if prev_close <= prev_ema and curr_close > curr_ema:
                position = 'LONG'
                entry_time = curr_time
                entry_price = curr_close
                # Initialize SL based on ATR
                current_sl = curr_close - (atr_mult * curr_atr)
                sl_line[i] = current_sl
        
        elif position == 'LONG':
            # Trail the SL upwards only
            new_sl = curr_close - (atr_mult * curr_atr)
            if new_sl > current_sl:
                current_sl = new_sl
                
            # Check if SL is hit
            if curr_low <= current_sl:
                trades.append({
                    'entry_time': entry_time,
                    'entry_price': entry_price,
                    'side': 'Long',
                    'exit_time': curr_time,
                    'exit_price': current_sl,
                    'exit_reason': 'ATR Trailing SL Hit'
                })
                position = None
                current_sl = None
            else:
                # Plot the trailing SL line
                sl_line[i] = current_sl

    # Close any open positions at the end of the available data
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
        
    df['atr_sl_line'] = sl_line
        
    # Chart plotting configuration
    from _CHARTING import spec as chspec
    plot_spec = chspec.build_plot_spec(df, indicators=[
        {"name": "52-Wk EMA", "series": df["ema_52"], "type": "line", "color": "#FFFFFF", "lineWidth": 2},
        {"name": "ATR SL", "series": df["atr_sl_line"], "type": "line", "color": "#FFA500", "lineWidth": 2}
    ])
    
    return trades, df, plot_spec