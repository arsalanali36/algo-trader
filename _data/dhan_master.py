import csv
import logging
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Setup logging
log = logging.getLogger("dhan_master")

BASE_DIR = Path(__file__).resolve().parent.parent
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

    # CE: higher strike = more OTM → positive offset goes right
    # PE: lower strike = more OTM → positive offset must go left
    if option_type == "PE":
        target_idx = atm_idx - offset
    else:
        target_idx = atm_idx + offset
    target_idx = max(0, min(len(strikes) - 1, target_idx))
    target_strike = strikes[target_idx]

    for c in contracts:
        if c["strike"] == target_strike:
            return c["sec_id"], c["trad_sym"], c.get("lot_size", 1)

    return None, None, None


def _near_month_monthly_expiry_str(symbol):
    """Cache key of the NEAR-MONTH MONTHLY expiry (not the nearest weekly).

    Matches bs_option._next_monthly_expiry (last expiry-weekday of the month, roll
    to next month if already past): the monthly contract is simply the LAST expiry
    inside its calendar month, so we take the earliest such month-last expiry that
    is still >= today. Cache-driven — reflects the actual listed monthly, so it
    stays correct even if NSE shifts the expiry weekday. Returns the exp_str or None.
    """
    if symbol not in _options_cache:
        return None
    now = ist_now()
    parsed = []
    for exp in _options_cache[symbol].keys():
        try:
            parsed.append((datetime.strptime(exp, "%Y-%m-%d %H:%M:%S"), exp))
        except Exception:
            continue
    if not parsed:
        return None
    parsed.sort(key=lambda x: x[0])
    month_last = {}                      # (year, month) -> (exp_date, exp_str); sorted asc → last wins
    for ed, exp in parsed:
        month_last[(ed.year, ed.month)] = (ed, exp)
    cands = sorted((v for v in month_last.values() if v[0].date() >= now.date()),
                   key=lambda x: x[0])
    return cands[0][1] if cands else None


def get_monthly_option_contract(symbol, spot_price, option_type, offset=0):
    """Same ATM±offset resolution as get_option_contract, but on the NEAR-MONTH
    MONTHLY expiry instead of the nearest weekly. The Overnight-ORB strategy uses
    this so its live instrument matches the backtest, which priced a monthly ATM
    buy (bs_option.reprice_positional) — a weekly held overnight bleeds far more
    theta AND dies on its expiry day; monthly avoids both. Rule 10 (backtest fidelity).
    """
    if not _options_cache:
        build_cache()
    if symbol not in _options_cache:
        log.error(f"Symbol {symbol} not found in options cache")
        return None, None, None

    exp_str = _near_month_monthly_expiry_str(symbol)
    if not exp_str:
        log.error(f"No monthly expiry found for {symbol}")
        return None, None, None

    contracts = [c for c in _options_cache[symbol][exp_str] if c["type"] == option_type]
    if not contracts:
        return None, None, None

    strikes = sorted(set(c["strike"] for c in contracts))
    if not strikes:
        return None, None, None

    atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
    atm_idx = strikes.index(atm_strike)
    # same offset convention as get_option_contract (PE inverted so +offset = OTM)
    target_idx = atm_idx - offset if option_type == "PE" else atm_idx + offset
    target_idx = max(0, min(len(strikes) - 1, target_idx))
    target_strike = strikes[target_idx]

    for c in contracts:
        if c["strike"] == target_strike:
            return c["sec_id"], c["trad_sym"], c.get("lot_size", 1)
    return None, None, None


def _monthly_expiry_strs(symbol):
    """Sorted list of month-last MONTHLY expiry cache-keys that are still >= today.
    Index 0 = near month, 1 = next month, 2 = far month, ...  (Same month-last
    logic as _near_month_monthly_expiry_str — the monthly contract is the LAST
    expiry inside its calendar month.) Empty list if none / symbol unknown."""
    if symbol not in _options_cache:
        return []
    now = ist_now()
    parsed = []
    for exp in _options_cache[symbol].keys():
        try:
            parsed.append((datetime.strptime(exp, "%Y-%m-%d %H:%M:%S"), exp))
        except Exception:
            continue
    if not parsed:
        return []
    parsed.sort(key=lambda x: x[0])
    month_last = {}                      # (year, month) -> (exp_date, exp_str); sorted asc => last wins
    for ed, exp in parsed:
        month_last[(ed.year, ed.month)] = (ed, exp)
    cands = sorted((v for v in month_last.values() if v[0].date() >= now.date()),
                   key=lambda x: x[0])
    return [c[1] for c in cands]


def _next_month_monthly_expiry_str(symbol):
    """Cache key of the NEXT-MONTH monthly expiry (skips the near month).
    None if only the near month is listed."""
    strs = _monthly_expiry_strs(symbol)
    return strs[1] if len(strs) >= 2 else None


def get_next_monthly_option_contract(symbol, spot_price, option_type, offset=0):
    """Same ATM±offset resolution as get_monthly_option_contract, but on the
    NEXT-MONTH monthly expiry (skips the near month).

    Purpose: stock F&O is PHYSICALLY settled — Zerodha blocks fresh stock-option
    MIS BUYs in the near-month's expiry week ("Fresh buy orders are not allowed
    ... compulsory physical delivery. Try next month's expiry."). Rolling the
    contract to next month sidesteps that block. Index options are cash-settled
    and never need this — callers must gate on stock-vs-index themselves.
    Returns (None,None,None) if no next-month contract is listed."""
    if not _options_cache:
        build_cache()
    if symbol not in _options_cache:
        log.error(f"Symbol {symbol} not found in options cache")
        return None, None, None

    exp_str = _next_month_monthly_expiry_str(symbol)
    if not exp_str:
        log.error(f"No next-month monthly expiry found for {symbol}")
        return None, None, None

    contracts = [c for c in _options_cache[symbol][exp_str] if c["type"] == option_type]
    if not contracts:
        return None, None, None

    strikes = sorted(set(c["strike"] for c in contracts))
    if not strikes:
        return None, None, None

    atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
    atm_idx = strikes.index(atm_strike)
    # same offset convention as get_option_contract (PE inverted so +offset = OTM)
    target_idx = atm_idx - offset if option_type == "PE" else atm_idx + offset
    target_idx = max(0, min(len(strikes) - 1, target_idx))
    target_strike = strikes[target_idx]

    for c in contracts:
        if c["strike"] == target_strike:
            return c["sec_id"], c["trad_sym"], c.get("lot_size", 1)
    return None, None, None


def list_expiries(symbol):
    """Every listed option expiry for `symbol` that is still >= today, each with
    display metadata — so a UI can offer a SPECIFIC expiry (weekly OR monthly),
    not just 'nearest' / 'next-month'. Sorted ascending. Each item:
        {"key": "<cache key>", "date": "YYYY-MM-DD", "label": "04 Aug", "monthly": bool}
    monthly = the LAST listed expiry inside its calendar month (same month-last
    rule as _monthly_expiry_strs). Empty list if symbol unknown."""
    if not _options_cache:
        build_cache()
    if symbol not in _options_cache:
        return []
    now = ist_now()
    parsed = []
    for exp in _options_cache[symbol].keys():
        try:
            d = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if d.date() >= now.date():
            parsed.append((d, exp))
    if not parsed:
        return []
    parsed.sort(key=lambda x: x[0])
    month_last = {}                        # (year, month) -> exp key; sorted asc => last wins
    for d, exp in parsed:
        month_last[(d.year, d.month)] = exp
    monthly_keys = set(month_last.values())
    return [{"key": exp, "date": d.strftime("%Y-%m-%d"),
             "label": d.strftime("%d %b"), "monthly": exp in monthly_keys}
            for d, exp in parsed]


def get_option_contract_for_expiry(symbol, spot_price, option_type, offset, expiry):
    """ATM±offset contract resolution on a SPECIFIC expiry. `expiry` = a full cache
    key ("YYYY-MM-DD HH:MM:SS") or a date ("YYYY-MM-DD"); matched by exact key
    first, then date-prefix. SAME PE-inverted offset convention as
    get_option_contract (+offset = OTM for both CE and PE — offset is a
    non-negative OTM magnitude; never pass a negative PE offset). Returns
    (None,None,None) if the expiry or the target contract can't be resolved."""
    if not _options_cache:
        build_cache()
    if symbol not in _options_cache:
        log.error(f"Symbol {symbol} not found in options cache")
        return None, None, None
    keys = list(_options_cache[symbol].keys())
    exp_key = None
    if expiry in keys:
        exp_key = expiry
    else:
        ed = str(expiry)[:10]              # YYYY-MM-DD prefix
        for k in keys:
            if k[:10] == ed:
                exp_key = k
                break
    if not exp_key:
        log.error(f"Expiry {expiry} not listed for {symbol}")
        return None, None, None

    contracts = [c for c in _options_cache[symbol][exp_key] if c["type"] == option_type]
    if not contracts:
        return None, None, None
    strikes = sorted(set(c["strike"] for c in contracts))
    if not strikes:
        return None, None, None

    atm_strike = min(strikes, key=lambda x: abs(x - spot_price))
    atm_idx = strikes.index(atm_strike)
    # PE inverted so +offset = OTM (matches get_option_contract). offset is a
    # non-negative magnitude — the sign is applied HERE (pe-offset-ok: resolver).
    target_idx = atm_idx - offset if option_type == "PE" else atm_idx + offset
    target_idx = max(0, min(len(strikes) - 1, target_idx))
    target_strike = strikes[target_idx]
    for c in contracts:
        if c["strike"] == target_strike:
            return c["sec_id"], c["trad_sym"], c.get("lot_size", 1)
    return None, None, None


def trading_days_to_near_monthly_expiry(symbol):
    """NSE trading days from TODAY up to (and including) the near-month monthly
    expiry for `symbol`. Today itself counts as 0 (i.e. today IS expiry -> 0).
    Returns None if the near-month expiry can't be resolved.

    Used to decide the physical-settlement 'switch to next month' window for
    stock options. Uses market_calendar (weekend + NSE holiday aware); if that
    import is unavailable, falls back to a plain calendar-day count."""
    exp_str = _near_month_monthly_expiry_str(symbol)
    if not exp_str:
        return None
    try:
        exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").date()
    except Exception:
        return None
    today = ist_now().date()
    if exp_date < today:
        return None
    if exp_date == today:
        return 0
    try:
        import market_calendar as _mc
        _is_td = _mc.is_trading_day
    except Exception:
        return (exp_date - today).days      # calendar-day fallback
    n = 0
    d = today
    while d < exp_date:
        d = d + timedelta(days=1)
        if _is_td(d):
            n += 1
    return n


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


def get_expiry_for_sec_id(sec_id):
    """Exact expiry date for a sec_id — needed because Dhan's trad_sym for
    INDEX options (NIFTY/BANKNIFTY) omits the day ("NIFTY-Jun2026-24100-PE"),
    unlike stock options which include it. Any code that needs the real
    expiry date (e.g. kite_broker.resolve_kite_symbol matching against Kite's
    instrument dump) must look it up here by sec_id, not parse it out of the
    trad_sym string — string-parsing silently produces a wrong/empty date for
    every index contract. Returns a `date` or None."""
    if not _options_cache:
        build_cache()
    if not sec_id:
        return None
    sec_id = str(sec_id)
    for symbol_contracts in _options_cache.values():
        for exp_str, contracts in symbol_contracts.items():
            for c in contracts:
                if str(c.get("sec_id")) == sec_id:
                    try:
                        return datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").date()
                    except Exception:
                        return None
    return None


_equity_cache = {}  # symbol -> (sec_id, seg, instrument)

def build_equity_cache():
    global _equity_cache
    if _equity_cache:
        return _equity_cache
    if not MASTER_CSV.exists():
        download_master_if_needed()
    result = {}
    # Indices: known Dhan sec_ids (not in the equity CSV rows)
    result["NIFTY"]     = ("13",  "IDX_I", "INDEX")
    result["BANKNIFTY"] = ("25",  "IDX_I", "INDEX")
    try:
        with open(MASTER_CSV, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                exch = row.get("SEM_EXM_EXCH_ID", "")
                inst = row.get("SEM_INSTRUMENT_NAME", "")
                if exch != "NSE" or inst not in ("EQUITY", "EQUITY-ETF"):
                    continue
                sym    = row.get("SEM_TRADING_SYMBOL", "").strip()
                sec_id = row.get("SEM_SMST_SECURITY_ID", "").strip()
                if sym and sec_id:
                    result[sym] = (sec_id, "NSE_EQ", "EQUITY")
    except Exception as e:
        log.error(f"build_equity_cache error: {e}")
    _equity_cache = result
    log.info(f"Equity cache built: {len(result)} symbols")
    return _equity_cache

def get_equity_info(symbol):
    """(sec_id, seg, instrument) for an equity/index symbol from master CSV."""
    if not _equity_cache:
        build_equity_cache()
    return _equity_cache.get(symbol)


_lot_by_secid = {}
def get_lot_size_by_sec_id(sec_id):
    """Lot size for an OPTION contract by its sec_id, straight from the scrip
    master (never hardcode lot sizes — they differ per underlying and change over
    time). Returns int lot_size, or None if not found — the caller MUST treat
    None as 'unknown' and not guess (per no-hardcode rule). Memoized per sec_id."""
    if sec_id is None:
        return None
    key = str(sec_id)
    if key in _lot_by_secid:
        return _lot_by_secid[key]
    if not _options_cache:
        build_cache()
    found = None
    for _sym_map in _options_cache.values():
        for _contracts in _sym_map.values():
            for _c in _contracts:
                if str(_c.get("sec_id")) == key:
                    found = int(_c.get("lot_size") or 0) or None
                    break
            if found:
                break
        if found:
            break
    _lot_by_secid[key] = found
    return found


_opttype_by_secid = {}
def get_option_type_by_sec_id(sec_id):
    """'CE' / 'PE' for an OPTION contract by its sec_id, read from the scrip
    master's own SEM_OPTION_TYPE column (cached as "type" by build_cache).

    Structured-field lookup on purpose — callers must NOT infer the option type
    by slicing a formatted trad_sym string (TRAP #13/#79). Memoized per sec_id.
    Returns None when the sec_id isn't in the master; the caller MUST treat None
    as 'unknown' and skip rather than guess (same contract as
    get_lot_size_by_sec_id)."""
    if sec_id is None:
        return None
    key = str(sec_id)
    if key in _opttype_by_secid:
        return _opttype_by_secid[key]
    if not _options_cache:
        build_cache()
    found = None
    for _sym_map in _options_cache.values():
        for _contracts in _sym_map.values():
            for _c in _contracts:
                if str(_c.get("sec_id")) == key:
                    found = (_c.get("type") or "").strip().upper() or None
                    break
            if found:
                break
        if found:
            break
    _opttype_by_secid[key] = found
    return found


_tradsym_by_secid = {}
def get_trad_sym_for_sec_id(sec_id):
    """Dhan trad_sym for an OPTION contract by its sec_id (reverse of
    get_sec_id_for_trad_sym). Needed when only a sec_id is on hand — e.g.
    risk_gate.kite_real_margin() resolving the Kite tradingsymbol via
    kite_broker.resolve_kite_symbol(), which parses name/strike/type out of
    the Dhan trad_sym string. Memoized per sec_id. Returns str or None —
    caller must treat None as 'unknown' and fall back, never guess."""
    if sec_id is None:
        return None
    key = str(sec_id)
    if key in _tradsym_by_secid:
        return _tradsym_by_secid[key]
    if not _options_cache:
        build_cache()
    found = None
    for _sym_map in _options_cache.values():
        for _contracts in _sym_map.values():
            for _c in _contracts:
                if str(_c.get("sec_id")) == key:
                    found = _c.get("trad_sym") or None
                    break
            if found:
                break
        if found:
            break
    _tradsym_by_secid[key] = found
    return found


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_master_if_needed()
    build_cache()
    sec_id, sym = get_option_contract("NIFTY", 24500, "PE", 0)
    print(f"NIFTY ATM PE: {sym} ({sec_id})")
