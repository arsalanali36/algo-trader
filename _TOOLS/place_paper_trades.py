import sys
import os

# Setup paths to allow imports
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from order_store import record, init_db

def place_trades():
    init_db()
    
    # Stocks and their last close prices from the screenshot
    stocks = [
        ("COHANCE", 466.95),
        ("GARUDA", 195.13),
        ("PPLPHARMA", 170.82),
        ("MEESHO", 180.04),
        ("NAUKRI", 1019.05),
        ("BALUFORGE", 472.30),
        ("ANTHEM", 781.65),
        ("SWANCORP", 330.80),
        ("SHRINGARMS", 209.49),
        ("GESHIP", 1482.90),
        ("CONCOR", 476.65),
        ("PAYTM", 1094.20),
        ("HDFCLIFE", 598.25)
    ]

    for sym, price in stocks:
        # Placing a PAPER BUY order of 1 quantity for each stock
        record(
            side="BUY",
            qty=1,
            price=price,
            source="manual",
            strategy="52_EMA_Scanner",
            mode="paper",
            broker="dhan",
            symbol=sym,
            instrument="EQUITY",
            trad_sym=sym,
            status="paper",
            tags=["chartink"]
        )
        print(f"Placed PAPER BUY order for {sym} at {price}")

    print("All paper trades successfully injected into the database!")

if __name__ == "__main__":
    place_trades()
