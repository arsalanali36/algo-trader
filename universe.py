"""
universe.py — symbol universe + security-id / option routing resolvers.

- NIFTY50 constituents (editable).
- equity_secid(symbol)          -> NSE_EQ security id (from scrip master).
- index_spot_secid(idx)         -> IDX_I security id for NIFTY/BANKNIFTY.
- stock_option_atm / index_option_atm -> reuse dhan_master resolver.

No yfinance — spot prices come from the broker (Dhan Data API / feed).
"""

import csv
from pathlib import Path

import dhan_master

BASE_DIR     = Path(__file__).resolve().parent
SCRIP_MASTER = BASE_DIR / "data" / "api-scrip-master.csv"

# Dhan index security ids (IDX_I segment)
INDEX_SECID = {"NIFTY": "13", "BANKNIFTY": "25"}

# Nifty-50 (Dhan trading symbols). Edit freely; unknown ones are skipped.
NIFTY50 = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT", "AXISBANK",
    "SBIN", "BHARTIARTL", "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND", "ONGC",
    "NTPC", "POWERGRID", "M&M", "TATAMOTORS", "TATASTEEL", "JSWSTEEL", "ADANIENT",
    "ADANIPORTS", "COALINDIA", "HCLTECH", "TECHM", "GRASIM", "HINDALCO", "DRREDDY",
    "CIPLA", "BAJAJFINSV", "BRITANNIA", "EICHERMOT", "HEROMOTOCO", "BPCL",
    "INDUSINDBK", "APOLLOHOSP", "TATACONSUM", "BAJAJ-AUTO", "SBILIFE", "HDFCLIFE",
    "LTIM", "SHRIRAMFIN", "ADANIGREEN",
]

_eq_cache = {}   # symbol -> sec_id (NSE_EQ)
_eq_loaded = False


def _load_equity_map():
    """Build equity symbol -> sec_id map once from the scrip master."""
    global _eq_loaded
    if _eq_loaded:
        return
    try:
        with open(SCRIP_MASTER, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if (row.get("SEM_EXM_EXCH_ID") == "NSE"
                        and row.get("SEM_SEGMENT") == "E"
                        and row.get("SEM_SERIES") == "EQ"):
                    sym = row.get("SEM_TRADING_SYMBOL", "")
                    if sym and sym not in _eq_cache:
                        _eq_cache[sym] = row.get("SEM_SMST_SECURITY_ID", "")
    except Exception:
        pass
    _eq_loaded = True


def equity_secid(symbol):
    _load_equity_map()
    return _eq_cache.get(symbol)


def index_spot_secid(idx):
    return INDEX_SECID.get(idx)


def resolve_universe(name="nifty50", custom=None):
    """Return list of symbols for a universe name (or custom list)."""
    if custom:
        return list(custom)
    if name == "nifty50":
        return list(NIFTY50)
    return list(NIFTY50)


def equity_secids(symbols):
    """{symbol: sec_id} for symbols that exist as NSE_EQ. Skips unknowns."""
    out = {}
    for s in symbols:
        sid = equity_secid(s)
        if sid:
            out[s] = sid
    return out


def stock_option_atm(symbol, spot, opt_type, offset=0):
    """ATM (+offset) option on a STOCK. Returns (sec_id, trading_symbol)."""
    return dhan_master.get_option_contract(symbol, spot, opt_type, offset)


def index_option_atm(idx, spot, opt_type, offset=0):
    """ATM (+offset) option on NIFTY/BANKNIFTY. Returns (sec_id, trading_symbol)."""
    return dhan_master.get_option_contract(idx, spot, opt_type, offset)
