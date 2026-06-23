import pandas as pd
import numpy as np
from datetime import time, timedelta

def backtest(df, cfg):
    """
    Full-control backtest for a Bollinger Band strategy with stop-loss and intraday force-exit.

    Parameters
    ----------
    df : pandas.DataFrame
        Must contain columns: time, open, high, low, close, volume.
        'time' must be datetime-like (naive IST assumed).  Oldest -> newest.
    cfg : dict
        Configuration parameters.  Recognised keys:
            bb_window      (default 20)
            bb_std         (default 2.0)
            ema_52_period  (default 52)
            ema_100_period (default 100)
            atr_len        (default 14)
            sl_pct         (default 0.5)      # stop-loss % of entry price
            tp_pct         (default 0.0)      # take-profit % (0 = disabled)
            allow_short    (default False)
            max_trades_per_day (default None)
            symbol         (default 'NIFTY')
            timeframe      (default '5m')
    Returns
    -------
    trades : list of dict
        Each trade: {entry_time, entry_price, side, exit_time, exit_price, exit_reason}
    df : pandas.DataFrame
        Original DataFrame with added indicator columns and signal columns.
    plot_spec : dict or None
        Indicator plot definitions (bands in teal, thick).
    """
    # ---------- defaults ----------
    bb_window = cfg.get('bb_window', 20)
    bb_std = cfg.get('bb_std', 2.0)
    ema_52_period = cfg.get('ema_52_period', 52)
    ema_100_period = cfg.get('ema_100_period', 100)
    atr_len = cfg.get('atr_len', 14)
    sl_pct = cfg.get('sl_pct', 0.5)          # percentage
    tp_pct = cfg.get('tp_pct', 0.0)          # percentage, 0 = disabled
    allow_short = cfg.get('allow_short', False)
    max_trades_per_day = cfg.get('max_trades_per_day', None)
    timeframe_str = cfg.get('timeframe', '5m')

    # parse timeframe in minutes
    if timeframe_str.endswith('m'):
        tf_min = int(timeframe_str[:-1])
    else:
        tf_min = 5  # fallback

    # ensure time column is datetime
    if not pd.api.types.is_datetime64_any_dtype(df['time']):
        df['time'] = pd.to_datetime(df['time'])

    # make a copy to avoid modifying original
    df = df.copy()

    # ---------- indicators ----------
    # Bollinger Bands
    df['bb_middle'] = df['close'].rolling(window=bb_window).mean()
    df['bb_std'] = df['close'].rolling(window=bb_window).std(ddof=0)  # population std
    df['bb_upper'] = df['bb_middle'] + bb_std * df['bb_std']
    df['bb_lower'] = df['bb_middle'] - bb_std * df['bb_std']

    # EMAs
    df['ema_52'] = df['close'].ewm(span=ema_52_period, adjust=False).mean()
    df['ema_100'] = df['close'].ewm(span=ema_100_period, adjust=False).mean()

    # ATR
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            (df['high'] - df['close'].shift()).abs(),
            (df['low'] - df['close'].shift()).abs()
        )
    )
    df['atr'] = df['tr'].rolling(window=atr_len).mean()

    # body percentage
    df['body_pct'] = (df['close'] - df['open']) / df['open'] * 100.0

    # drop temporary columns
    df.drop('tr', axis=1, inplace=True)

    # ---------- simulation ----------
    trades = []
    position = None          # dict: side, entry_price, entry_time, stop_loss, take_profit
    pending_entry = None     # dict: side, signal_bar_idx

    end_time = time(15, 15)  # IST

    # helper: check if a timestamp is at or after 15:15
    def is_after_end(t):
        return t.time() >= end_time

    # helper: check if bar ends at or after 15:15
    def bar_ends_after_end(t):
        return (t + timedelta(minutes=tf_min)).time() >= end_time

    # loop over bars (index)
    for i in range(len(df)):
        row = df.iloc[i]
        t = row['time']
        o, h, l, c = row['open'], row['high'], row['low'], row['close']

        # ----- handle pending entry (open at current bar's open) -----
        if pending_entry is not None and position is None:
            # cannot enter if bar starts at or after 15:15
            if is_after_end(t):
                pending_entry = None
            else:
                # enter at open
                side = pending_entry['side']
                entry_price = o
                entry_time = t
                if side == 'Long':
                    sl = entry_price * (1 - sl_pct / 100.0)
                    tp = entry_price * (1 + tp_pct / 100.0) if tp_pct > 0 else None
                else:  # Short
                    sl = entry_price * (1 + sl_pct / 100.0)
                    tp = entry_price * (1 - tp_pct / 100.0) if tp_pct > 0 else None
                position = {
                    'side': side,
                    'entry_price': entry_price,
                    'entry_time': entry_time,
                    'stop_loss': sl,
                    'take_profit': tp,
                    'entry_idx': i
                }
                pending_entry = None
                # continue to process exits for this same bar (entry and exit same bar possible)

        # ----- exit logic if in position -----
        if position is not None:
            exit_price = None
            exit_reason = None

            # force exit if bar ends at or after 15:15
            if bar_ends_after_end(t):
                exit_price = c
                exit_reason = 'end_of_day'
            else:
                side = position['side']
                sl = position['stop_loss']
                tp = position['take_profit']

                if side == 'Long':
                    # check stop-loss first
                    if l <= sl:
                        exit_price = sl
                        exit_reason = 'stop_loss'
                    # then take-profit (if enabled)
                    elif tp is not None and h >= tp:
                        exit_price = tp
                        exit_reason = 'take_profit'
                    # then band exit (upper)
                    elif h >= row['bb_upper']:
                        exit_price = row['bb_upper']
                        exit_reason = 'upper_band'
                else:  # Short
                    if h >= sl:
                        exit_price = sl
                        exit_reason = 'stop_loss'
                    elif tp is not None and l <= tp:
                        exit_price = tp
                        exit_reason = 'take_profit'
                    elif l <= row['bb_lower']:
                        exit_price = row['bb_lower']
                        exit_reason = 'lower_band'

            if exit_price is not None:
                # record trade
                trades.append({
                    'entry_time': position['entry_time'],
                    'entry_price': position['entry_price'],
                    'side': position['side'],
                    'exit_time': t,
                    'exit_price': exit_price,
                    'exit_reason': exit_reason
                })
                position = None

        # ----- entry signal (based on current bar's close, for next bar) -----
        if position is None and pending_entry is None:
            # skip if no sufficient data for indicators
            if not np.isnan(row['bb_lower']) and not np.isnan(row['bb_upper']):
                # long signal
                if row['close'] < row['bb_lower']:
                    pending_entry = {'side': 'Long', 'signal_bar_idx': i}
                # short signal
                elif allow_short and row['close'] > row['bb_upper']:
                    pending_entry = {'side': 'Short', 'signal_bar_idx': i}

        # (optional max_trades_per_day – we ignore for simplicity)

    # if any position remains open, close at last bar's close (should not happen with force-exit)
    if position is not None:
        trades.append({
            'entry_time': position['entry_time'],
            'entry_price': position['entry_price'],
            'side': position['side'],
            'exit_time': df.iloc[-1]['time'],
            'exit_price': df.iloc[-1]['close'],
            'exit_reason': 'forced_close'
        })

    # ---------- add signal columns for plotting ----------
    df['long_entry_signal'] = np.nan
    df['short_entry_signal'] = np.nan
    df['long_exit_signal'] = np.nan
    df['short_exit_signal'] = np.nan

    # mark entry/exit on the bars where they occurred (we have indices from trades)
    for trade in trades:
        # find entry index (approximate)
        entry_time = trade['entry_time']
        exit_time = trade['exit_time']
        # find indices where time matches (could be multiple, but we assume unique)
        entry_idx = df[df['time'] == entry_time].index
        exit_idx = df[df['time'] == exit_time].index
        if len(entry_idx) > 0:
            idx = entry_idx[0]
            if trade['side'] == 'Long':
                df.at[idx, 'long_entry_signal'] = trade['entry_price']
            else:
                df.at[idx, 'short_entry_signal'] = trade['entry_price']
        if len(exit_idx) > 0:
            idx = exit_idx[0]
            if trade['side'] == 'Long':
                df.at[idx, 'long_exit_signal'] = trade['exit_price']
            else:
                df.at[idx, 'short_exit_signal'] = trade['exit_price']

    # ---------- plot_spec ----------
    plot_spec = {
        'indicators': [
            {'name': 'BB Upper', 'column': 'bb_upper', 'color': 'teal', 'width': 2},
            {'name': 'BB Lower', 'column': 'bb_lower', 'color': 'teal', 'width': 2},
            {'name': 'BB Middle', 'column': 'bb_middle', 'color': 'teal', 'width': 1},
        ],
        'signals': [
            {'type': 'buy', 'column': 'long_entry_signal', 'color': 'green', 'marker': '^'},
            {'type': 'sell', 'column': 'short_entry_signal', 'color': 'red', 'marker': 'v'},
            {'type': 'exit_buy', 'column': 'long_exit_signal', 'color': 'blue', 'marker': 'o'},
            {'type': 'exit_sell', 'column': 'short_exit_signal', 'color': 'orange', 'marker': 'o'},
        ]
    }

    return trades, df, plot_spec