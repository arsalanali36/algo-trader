import datetime
from _TOOLS.backtest_engine import ensure_equity_data
from universe import NIFTY50

def run_bulk_download():
    date_from = "2025-01-01"
    date_to = datetime.date.today().isoformat()
    
    print(f"Starting bulk download for Nifty 50 from {date_from} to {date_to}...")
    
    for i, symbol in enumerate(NIFTY50, 1):
        print(f"\n[{i}/{len(NIFTY50)}] Processing {symbol}...")
        try:
            ensure_equity_data(symbol, date_from, date_to)
        except Exception as e:
            print(f"Error downloading {symbol}: {e}")
            
    print("\nBulk download complete! All data is saved in ._TRADING DATA/Equity")

if __name__ == "__main__":
    run_bulk_download()
