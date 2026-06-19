"""
kite_broker.py — Zerodha Kite Connect order placement

DATA  → Dhan API (free, always)
ORDERS → Kite Connect (yahan aata hai — aapka actual Zerodha account)

Kite token daily refresh hota hai. Dashboard ke Control tab mein
"Kite Token" section se paste karo.

Config (data/config.json):
  {
    "kite_api_key"    : "your_api_key",
    "kite_api_secret" : "your_api_secret",
    "kite_access_token": "today's access token"   ← daily update
  }

Token generate karne ka flow (rozana subah):
  1. Browser mein jaao:
     https://kite.trade/connect/login?api_key=YOUR_KEY&v=3
  2. Zerodha login karo → aapko redirect milega ek URL pe jisme
     ?request_token=XXXX hoga
  3. Woh request_token dashboard ke Control tab mein paste karo
  4. Dashboard automatically exchange karke access_token save kar lega
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("kite_broker")

# config.json ki path (project root/data/)
_CONFIG_FILE = Path(__file__).resolve().parent.parent / "data" / "config.json"


def _load_kite():
    """KiteConnect object banao — config.json se credentials lo."""
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        raise RuntimeError("kiteconnect not installed. Run: pip install kiteconnect")

    cfg   = json.loads(_CONFIG_FILE.read_text())
    api_key      = cfg.get("kite_api_key", "")
    access_token = cfg.get("kite_access_token", "")

    if not api_key or not access_token:
        raise RuntimeError("kite_api_key ya kite_access_token config.json mein nahi hai")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite


def exchange_request_token(request_token):
    """
    request_token → access_token exchange karo aur config.json mein save karo.
    Dashboard is function ko call karta hai jab user request_token paste karta hai.
    Returns: access_token (string) ya error message
    """
    try:
        from kiteconnect import KiteConnect
        cfg        = json.loads(_CONFIG_FILE.read_text())
        api_key    = cfg.get("kite_api_key", "")
        api_secret = cfg.get("kite_api_secret", "")
        if not api_key or not api_secret:
            return None, "kite_api_key / kite_api_secret missing in config.json"

        kite = KiteConnect(api_key=api_key)
        data = kite.generate_session(request_token, api_secret=api_secret)
        access_token = data["access_token"]

        # config.json mein save karo
        cfg["kite_access_token"] = access_token
        _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
        log.info(f"[Kite] Access token saved — valid till midnight")
        return access_token, None
    except Exception as e:
        return None, str(e)


def place_order(side, trad_sym, qty, product="MIS", order_type="MARKET",
                price=0.0, tag="ALGO", log_ref=None):
    """
    Kite Connect pe order place karo.

    side      : "BUY" ya "SELL"
    trad_sym  : Zerodha format — e.g. "NIFTY26JUN23950CE"
                (Dhan format "NIFTY-Jun2026-23950-CE" se alag hai — convert zaroori)
    qty       : actual shares (lots × lot_size)
    product   : "MIS" = intraday, "NRML" = overnight
    order_type: "MARKET" ya "LIMIT"
    price     : sirf LIMIT order ke liye

    Returns: order_id (string) ya None on failure
    """
    lg = log_ref or log
    try:
        from kiteconnect import KiteConnect
        kite = _load_kite()

        params = {
            "variety"         : kite.VARIETY_REGULAR,
            "exchange"        : kite.EXCHANGE_NFO,
            "tradingsymbol"   : trad_sym,
            "transaction_type": kite.TRANSACTION_TYPE_BUY if side == "BUY" else kite.TRANSACTION_TYPE_SELL,
            "quantity"        : qty,
            "product"         : kite.PRODUCT_MIS if product == "MIS" else kite.PRODUCT_NRML,
            "order_type"      : kite.ORDER_TYPE_MARKET if order_type == "MARKET" else kite.ORDER_TYPE_LIMIT,
            "price"           : price if order_type == "LIMIT" else 0,
            "tag"             : tag[:20] if tag else "ALGO",   # Kite max 20 chars
        }
        order_id = kite.place_order(**params)
        lg.info(f"  [KITE] {side} {trad_sym}  qty={qty}  orderId={order_id}")
        return order_id
    except Exception as e:
        lg.error(f"  [KITE ERR] {side} {trad_sym}: {e}")
        return None


def get_positions(log_ref=None):
    """
    Zerodha pe open positions fetch karo (intraday MIS).
    Returns list of position dicts.
    """
    lg = log_ref or log
    try:
        kite = _load_kite()
        pos  = kite.positions()
        return pos.get("day", [])   # day = intraday positions
    except Exception as e:
        lg.error(f"[KITE] get_positions error: {e}")
        return []


def get_ltp(instruments):
    """
    Live LTP fetch karo.
    instruments: list of "NFO:NIFTY26JUN23950CE" format strings
    Returns dict {instrument: ltp}
    """
    try:
        kite  = _load_kite()
        quote = kite.ltp(instruments)
        return {k: v["last_price"] for k, v in quote.items()}
    except Exception:
        return {}


# ── Symbol format conversion ────────────────────────────────────────────────
# Dhan format  : "NIFTY-Jun2026-23950-CE"
# Kite format  : "NIFTY26JUN23950CE"
# Conversion needed before placing order on Kite.

_MONTH_MAP = {
    "Jan": "JAN", "Feb": "FEB", "Mar": "MAR", "Apr": "APR",
    "May": "MAY", "Jun": "JUN", "Jul": "JUL", "Aug": "AUG",
    "Sep": "SEP", "Oct": "OCT", "Nov": "NOV", "Dec": "DEC",
}

def dhan_sym_to_kite(dhan_sym):
    """
    Convert Dhan trading symbol to Kite format.
    "NIFTY-Jun2026-23950-CE" → "NIFTY26JUN23950CE"

    Note: This is a best-effort conversion.
    For exact symbols, use kite.instruments("NFO") dump.
    """
    try:
        parts  = dhan_sym.split("-")   # ["NIFTY", "Jun2026", "23950", "CE"]
        name   = parts[0]              # "NIFTY"
        mon_yr = parts[1]              # "Jun2026"
        strike = parts[2]              # "23950"
        opt    = parts[3]              # "CE" / "PE"

        month  = mon_yr[:3]            # "Jun"
        year   = mon_yr[3:][2:]        # "2026" → "26"
        mon_k  = _MONTH_MAP.get(month, month.upper())

        return f"{name}{year}{mon_k}{strike}{opt}"   # "NIFTY26JUN23950CE"
    except Exception:
        return dhan_sym   # fallback: return as-is
