# symbol: NIFTY
# timeframe: 5m
# qty: 1
# bb_window: 20
# bb_std: 2
# max_trades_per_day: 0
# sl_pct: 0.0
# tp_pct: 0.0

import pandas as pd
import numpy as np

def backtest(df, cfg):
    period = cfg.get('bb_window', 20)
    std_dev = cfg.get('bb_std', 2.0)
    sl_pct = cfg.get('sl_pct', 0.0) / 100.0
    tp_pct = cfg.get('tp_pct', 0.0) / 100.0
    max_trades = cfg.get('max_trades_per_day', 0)
    
    close = df['close']
    sma = close.rolling(window=period).mean()
    std = close.rolling(window=period).std()
    df['bb_upper'] = sma + (std * std_dev)
    df['bb_lower'] = sma - (std * std_dev)
    
    trades = []
    position = None
    entry_time = None
    entry_price = None
    
    force_exit_time = pd.Timestamp('15:15:00').time()
    
    trades_today = 0
    current_date = None
    
    for i in range(period + 1, len(df)):
        curr_bar = df.iloc[i]
        prev_bar = df.iloc[i-1]
        curr_time = curr_bar['time']
        
        # Reset daily trade limit tracker
        bar_date = curr_time.date()
        if current_date != bar_date:
            current_date = bar_date
            trades_today = 0
            
        # Intraday force exit at 15:15 IST
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
            continue
            
        curr_lower = curr_bar['bb_lower']
        curr_upper = curr_bar['bb_upper']
        prev_lower = prev_bar['bb_lower']
        
        curr_close = curr_bar['close']
        prev_close = prev_bar['close']
        curr_high = curr_bar['high']
        curr_low = curr_bar['low']
        
        if position is None:
            if max_trades == 0 or trades_today < max_trades:
                # Entry Long: price closes back inside the band after being below or at the lower band
                if prev_close <= prev_lower and curr_close > curr_lower:
                    if curr_time.time() < force_exit_time:
                        position = 'LONG'
                        entry_time = curr_time
                        entry_price = curr_close
                        trades_today += 1
        
        elif position == 'LONG':
            exit_signal = False
            exit_reason = ''
            
            # Exit Long: candle high touches the upper band
            if curr_high >= curr_upper:
                exit_signal = True
                exit_reason = 'Upper Band Touch'
            elif sl_pct > 0 and curr_low <= entry_price * (1 - sl_pct):
                exit_signal = True
                exit_reason = f'Stop Loss ({sl_pct*100:.2f}%)'
            elif tp_pct > 0 and curr_high >= entry_price * (1 + tp_pct):
                exit_signal = True
                exit_reason = f'Take Profit ({tp_pct*100:.2f}%)'
                
            if exit_signal:
                trades.append({
                    'entry_time': entry_time,
                    'entry_price': entry_price,
                    'side': 'Long',
                    'exit_time': curr_time,
                    'exit_price': curr_bar['close'],
                    'exit_reason': exit_reason
                })
                position = None
                
    # Close any open positions at the end of data
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
        
    # Chart plotting configuration
    from _CHARTING import spec as chspec
    plot_spec = chspec.build_plot_spec(df, indicators=[
        {"name": "BB Upper", "series": df["bb_upper"], "type": "line", "color": "#008080", "lineWidth": 3},
        {"name": "BB Lower", "series": df["bb_lower"], "type": "line", "color": "#008080", "lineWidth": 3}
    ])
    
    return trades, df, plot_spec