import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# Setup paths to allow imports
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import universe
from _TOOLS.backtest_engine import ensure_and_load_symbol

def run_scanner():
    print("Running 52-Week EMA Crossover Scanner on NIFTY50 universe...")
    
    # End date is today
    date_to = datetime.now().strftime('%Y-%m-%d')
    # Load past 500 days of data to have enough runway to compute the 260-period EMA
    date_from = (datetime.now() - timedelta(days=500)).strftime('%Y-%m-%d')
    
    symbols = universe.resolve_universe("nifty50")
    
    matched_stocks = []
    
    import _TOOLS.backtest_engine as be
    
    for sym in symbols:
        try:
            # Skip download, use ONLY local cache directly
            if sym in ("NIFTY", "BANKNIFTY"):
                cont1 = be.load_1m_range(date_from, date_to)
            else:
                cont1 = be.load_equity_1m_range(sym, date_from, date_to)
                
            if cont1.empty:
                continue
                
            # Resample 1m data to Daily (1440 mins)
            df = be.resample(cont1, 1440)
            
            if df is None or df.empty or len(df) < 260:
                continue
            
            # Compute 52-week (260 daily period) EMA
            df['ema_52'] = df['close'].ewm(span=260, adjust=False).mean()
            
            last_bar = df.iloc[-1]
            prev_bar = df.iloc[-2]
            
            curr_close = float(last_bar['close'])
            prev_close = float(prev_bar['close'])
            
            curr_ema = float(last_bar['ema_52'])
            prev_ema = float(prev_bar['ema_52'])
            
            # Condition: price crossed above 52-EMA on the most recent completed/current bar
            if prev_close <= prev_ema and curr_close > curr_ema:
                matched_stocks.append({
                    'Symbol': sym,
                    'Date': last_bar['time'].strftime('%Y-%m-%d') if hasattr(last_bar['time'], 'strftime') else str(last_bar['time']),
                    'Close': round(curr_close, 2),
                    'EMA52': round(curr_ema, 2)
                })
        except Exception as e:
            # Graceful error handling for missing data
            pass
            
    if not matched_stocks:
        print("\nNo stocks found crossing above 52-EMA on the last bar.")
    else:
        print("\n=== SCANNER RESULTS: Stocks crossing above 52 EMA ===")
        print(f"{'Symbol':<15} | {'Date':<12} | {'Close':<10} | {'EMA52':<10}")
        print("-" * 55)
        for s in matched_stocks:
            print(f"{s['Symbol']:<15} | {s['Date']:<12} | {s['Close']:<10} | {s['EMA52']:<10}")
            
    return matched_stocks

if __name__ == "__main__":
    run_scanner()
