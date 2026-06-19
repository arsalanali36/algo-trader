import csv
import logging
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Setup logging
log = logging.getLogger("dhan_master")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
MASTER_CSV = DATA_DIR / "api-scrip-master.csv"

def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)

def download_master_if_needed():
    # Only download if not downloaded today
    today = ist_now().strftime("%Y-%m-%d")
    flag_file = DATA_DIR / f"master_{today}.flag"
    
    if flag_file.exists() and MASTER_CSV.exists():
        return True

    log.info("Downloading Dhan Scrip Master...")
    url = "https://images.dhan.co/api-data/api-scrip-master.csv"
    try:
        urllib.request.urlretrieve(url, str(MASTER_CSV))
        
        for f in DATA_DIR.glob("master_*.flag"):
            f.unlink()
            
        flag_file.touch()
        log.info("Dhan Scrip Master downloaded successfully.")
        return True
    except Exception as e:
        log.error(f"Failed to download Dhan master: {e}")
        return False

_options_cache = {}

def build_cache():
    global _options_cache
    if not MASTER_CSV.exists():
        if not download_master_if_needed():
            return
            
    log.info("Building Options Cache from Scrip Master...")
    _options_cache = {}
    
    try:
        with open(MASTER_CSV, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                exch = row.get("SEM_EXM_EXCH_ID")
                inst = row.get("SEM_INSTRUMENT_NAME")
                
                if exch != "NSE" and exch != "NFO":
                    continue
                if inst not in ("OPTIDX", "OPTSTK"):
                    continue
                
                trad_sym = row.get("SEM_TRADING_SYMBOL", "")
                if not trad_sym:
                    continue
                    
                # Extract symbol from trading symbol: "NIFTY-28Aug2024-24500-CE" -> "NIFTY"
                symbol = trad_sym.split('-')[0]
                
                expiry = row.get("SEM_EXPIRY_DATE")
                
                try:
                    strike = float(row.get("SEM_STRIKE_PRICE", 0))
                except:
                    continue
                    
                opt_type = row.get("SEM_OPTION_TYPE") # CE or PE
                sec_id = row.get("SEM_SMST_SECURITY_ID")
                
                if symbol not in _options_cache:
                    _options_cache[symbol] = {}
                if expiry not in _options_cache[symbol]:
                    _options_cache[symbol][expiry] = []
                    
                _options_cache[symbol][expiry].append({
                    "strike": strike,
                    "type": opt_type,
                    "sec_id": sec_id,
                    "trad_sym": trad_sym,
                    "lot_size": int(float(row.get("SEM_LOT_UNITS") or 1))
                })
        log.info("Options Cache built successfully.")
    except Exception as e:
        log.error(f"Error reading scrip master: {e}")

def get_option_contract(symbol, spot_price, option_type, offset=0):
    if not _options_cache:
        build_cache()
        
    if symbol not in _options_cache:
        log.error(f"Symbol {symbol} not found in options cache")
        return None, None, None

    expiries = list(_options_cache[symbol].keys())

    valid_expiries = []
    now = ist_now()
    for exp in expiries:
        try:
            exp_date = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S")
            if exp_date.date() >= now.date():
                valid_expiries.append((exp_date, exp))
        except:
            continue

    if not valid_expiries:
        log.error(f"No valid expiries found for {symbol}")
        return None, None, None

    valid_expiries.sort(key=lambda x: x[0])
    nearest_expiry_str = valid_expiries[0][1]

    contracts = _options_cache[symbol][nearest_expiry_str]
    contracts = [c for c in contracts if c["type"] == option_type]

    if not contracts:
        return None, None, None

    strikes = sorted(list(set(c["strike"] for c in contracts)))

    if not strikes:
        return None, None, None

    atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
    atm_idx = strikes.index(atm_strike)

    target_idx = atm_idx + offset
    target_idx = max(0, min(len(strikes) - 1, target_idx))
    target_strike = strikes[target_idx]

    for c in contracts:
        if c["strike"] == target_strike:
            return c["sec_id"], c["trad_sym"], c.get("lot_size", 1)

    return None, None, None

def get_sec_id_for_trad_sym(trad_sym):
    """Resolve sec_id for an exact trading symbol, picking the nearest NON-expired
    expiry. Same trading symbol (e.g. NIFTY-Jun2026-24050-CE) can map to multiple
    expiries since the day is not in the symbol — never return an expired contract."""
    if not _options_cache:
        build_cache()
    if not trad_sym:
        return None
    symbol = trad_sym.split('-')[0]
    if symbol not in _options_cache:
        return None
    now = ist_now()
    best = None  # (exp_date, sec_id)
    for exp_str, contracts in _options_cache[symbol].items():
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if exp_date.date() < now.date():
            continue
        for c in contracts:
            if c["trad_sym"] == trad_sym:
                if best is None or exp_date < best[0]:
                    best = (exp_date, c["sec_id"])
    return best[1] if best else None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_master_if_needed()
    build_cache()
    sec_id, sym = get_option_contract("NIFTY", 24500, "PE", 0)
    print(f"NIFTY ATM PE: {sym} ({sec_id})")
