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
from typing import Optional

from .base_broker import BaseBroker

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
    Convert Dhan trading symbol to Kite format (STRING-GUESS fallback only —
    see resolve_kite_symbol() below for the reliable path).
    Dhan's real format is "NAME-DDMonYYYY-strike-CE/PE", e.g.
    "NIFTY-28Jun2026-23950-CE" (day IS present — a previous version of this
    function assumed no day, e.g. parsed "28Jun2026"[:3] as the month and got
    "28J" instead of "Jun", producing a garbage/non-existent Kite symbol on
    every single call). Even with the day parsed correctly, NIFTY's weekly
    expiries use a different Kite symbol scheme than monthly (single-letter
    month + day code) that this string guess does NOT reproduce — use
    resolve_kite_symbol() (Kite's own instrument dump, exact match) as the
    primary path; this function is only a last-resort fallback if that dump
    is unavailable (e.g. network down), logged loudly when used.
    """
    try:
        parts  = dhan_sym.split("-")   # ["NIFTY", "28Jun2026", "23950", "CE"]
        name   = parts[0]              # "NIFTY"
        dmy    = parts[1]              # "28Jun2026"
        strike = parts[2]              # "23950"
        opt    = parts[3]              # "CE" / "PE"

        day    = dmy[:2]               # "28"
        month  = dmy[2:5]              # "Jun"
        year   = dmy[5:][2:]           # "2026" → "26"
        mon_k  = _MONTH_MAP.get(month, month.upper())

        return f"{name}{year}{mon_k}{strike}{opt}"   # monthly-style guess, e.g. "NIFTY26JUN23950CE"
    except Exception:
        return dhan_sym   # fallback: return as-is


# ── Reliable Dhan->Kite symbol resolution via Kite's own instrument dump ──
# String-guessing a tradingsymbol is fragile (weekly vs monthly expiry codes
# differ, and the guess above can't represent NIFTY's weekly single-letter
# month+day scheme at all). Matching on (name, expiry date, strike, CE/PE)
# against kite.instruments("NFO") is exact and format-agnostic.
import datetime as _dt

_kite_instr_cache = {"date": None, "data": None}


def _get_kite_instruments(kite):
    today = _dt.date.today().isoformat()
    if _kite_instr_cache["date"] == today and _kite_instr_cache["data"]:
        return _kite_instr_cache["data"]
    instruments = kite.instruments("NFO")
    _kite_instr_cache["date"] = today
    _kite_instr_cache["data"] = instruments
    return instruments


def _parse_dhan_trad_sym(dhan_sym):
    """"NIFTY-28Jun2026-23950-CE" -> ("NIFTY", date(2026,6,28), 23950.0, "CE")"""
    parts = dhan_sym.split("-")
    name = parts[0]
    dmy = parts[1]
    strike = float(parts[2])
    opt_type = parts[3]
    day = int(dmy[:2])
    mon_str = dmy[2:5]
    year = int(dmy[5:])
    from datetime import datetime as _dtm
    month = _dtm.strptime(mon_str, "%b").month
    expiry = _dt.date(year, month, day)
    return name, expiry, strike, opt_type


def resolve_kite_symbol(kite, dhan_trad_sym, sec_id=None):
    """Exact Dhan-trad_sym -> Kite-tradingsymbol resolution via Kite's
    instrument dump (cached per-day). Returns None (never raises) if parsing
    or lookup fails — caller must fall back to dhan_sym_to_kite() and log
    loudly, never silently send a guessed symbol on a live order.

    `sec_id`, if given, is used to get the EXACT expiry date from
    dhan_master (by sec_id, not by parsing the trad_sym string) — Dhan's
    trad_sym omits the day for INDEX options (NIFTY/BANKNIFTY,
    "NIFTY-Jun2026-24100-PE"), unlike stock options which include it
    ("RELIANCE-28Jun2026-..."). Parsing a day out of a day-less string used
    to silently produce a wrong expiry and a guaranteed no-match. Found
    2026-06-29 — first live Kite test order, NIFTY, failed with "instrument
    ... does not exist" because of exactly this."""
    name, expiry, strike, opt_type = None, None, None, None
    try:
        name, expiry, strike, opt_type = _parse_dhan_trad_sym(dhan_trad_sym)
    except Exception:
        pass
    if sec_id:
        try:
            import sys as _s, os as _o
            _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
            if _root not in _s.path:
                _s.path.insert(0, _root)
            import dhan_master
            real_expiry = dhan_master.get_expiry_for_sec_id(sec_id)
            if real_expiry:
                expiry = real_expiry
        except Exception:
            pass
        if name is None or strike is None or opt_type is None:
            # trad_sym parse failed outright (e.g. ValueError on a day-less
            # string) — still recover name/strike/opt_type from the string,
            # just not the expiry (that came from sec_id above, or stays None).
            try:
                parts = dhan_trad_sym.split("-")
                name = name or parts[0]
                strike = strike if strike is not None else float(parts[2])
                opt_type = opt_type or parts[3]
            except Exception:
                pass
    if name is None or expiry is None or strike is None or opt_type is None:
        return None
    try:
        instruments = _get_kite_instruments(kite)
    except Exception:
        return None
    for ins in instruments:
        if ins.get("name") != name:
            continue
        if ins.get("instrument_type") != opt_type:
            continue
        try:
            if float(ins.get("strike") or 0) != strike:
                continue
        except Exception:
            continue
        exp = ins.get("expiry")
        if exp and hasattr(exp, "year") and not isinstance(exp, _dt.date):
            exp = exp.date()
        if exp != expiry:
            continue
        return ins.get("tradingsymbol")
    return None


# ── BaseBroker implementation — ORDERS go to Kite, DATA stays on Dhan (the
# documented design at the top of this file: free Dhan data API, real money
# orders via Kite Connect). quote()/intraday_candles() therefore delegate to
# DhanBroker rather than calling Kite's own (rate-limited, paid-tier-gated)
# market data endpoints — this is intentional, not a shortcut. ──
class KiteBroker(BaseBroker):

    def __init__(self, creds: Optional[dict] = None):
        if creds:
            self._cfg = dict(creds)
        else:
            self._cfg = json.loads(_CONFIG_FILE.read_text()) if _CONFIG_FILE.exists() else {}
        self._kite = None
        self._dhan = None  # lazy — data delegate

    def name(self) -> str:
        return "kite"

    def _get_kite(self):
        if self._kite is None:
            from kiteconnect import KiteConnect
            api_key = self._cfg.get("kite_api_key", "")
            access_token = self._cfg.get("kite_access_token", "")
            if not api_key or not access_token:
                raise RuntimeError("kite_api_key/kite_access_token missing — refresh today's Kite token in Control tab")
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)
            self._kite = kite
        return self._kite

    def _get_dhan(self):
        if self._dhan is None:
            from .dhan_broker import DhanBroker
            self._dhan = DhanBroker()
        return self._dhan

    # ---- data (delegated to Dhan by design) ----
    def intraday_candles(self, sec_id, seg, instrument, days: int = 5, interval: int = 1):
        return self._get_dhan().intraday_candles(sec_id, seg, instrument, days, interval)

    def quote(self, sec_id, seg) -> dict:
        return self._get_dhan().quote(sec_id, seg)

    # ---- orders (real Kite Connect calls) ----
    _SEG_TO_EXCHANGE = {"NSE_FNO": "NFO", "NSE_EQ": "NSE", "IDX_I": "NSE"}

    def place_order(self, side, sec_id, seg, qty, order_type="MARKET",
                    price=0.0, trad_sym=None, tag=None) -> dict:
        import kite_rate_limiter as _krl
        try:
            kite = self._get_kite()
        except Exception as e:
            return {"status": "rejected", "order_id": None, "fill_price": None,
                    "reason": f"kite auth: {e}", "raw": None}

        kite_sym = None
        if trad_sym:
            kite_sym = resolve_kite_symbol(kite, trad_sym, sec_id=sec_id)
            if not kite_sym:
                kite_sym = dhan_sym_to_kite(trad_sym)
                log.warning(f"[KITE] instrument-dump resolve failed for {trad_sym} — "
                            f"using string-guess fallback {kite_sym} (verify manually)")
        if not kite_sym:
            return {"status": "rejected", "order_id": None, "fill_price": None,
                    "reason": "no trad_sym to resolve a Kite symbol from", "raw": None}

        exchange = self._SEG_TO_EXCHANGE.get(seg, "NFO")
        params = {
            "variety": "regular",
            "exchange": exchange,
            "tradingsymbol": kite_sym,
            "transaction_type": "BUY" if side == "BUY" else "SELL",
            "quantity": int(qty),
            "product": "MIS",
            "order_type": "LIMIT" if order_type == "LIMIT" else "MARKET",
            "price": round(float(price), 2) if order_type == "LIMIT" else 0,
            "tag": (tag or "ALGO")[:20],
        }
        try:
            _krl.acquire("order")
            order_id = kite.place_order(**params)
            log.info(f"  [KITE] {side} {kite_sym} qty={qty} orderId={order_id}")
            return {"status": "pending", "order_id": order_id, "fill_price": None,
                    "reason": "submitted", "raw": params}
        except Exception as e:
            msg = str(e)
            if "429" in msg or "Too many" in msg.lower():
                _krl.note_429()
            log.error(f"  [KITE ERR] {side} {kite_sym}: {e}")
            return {"status": "rejected", "order_id": None, "fill_price": None,
                    "reason": msg, "raw": None}

    def order_status(self, order_id):
        if not order_id:
            return None
        import kite_rate_limiter as _krl
        try:
            kite = self._get_kite()
            _krl.acquire("order")
            hist = kite.order_history(order_id)
            if hist:
                return str(hist[-1].get("status") or "").upper() or None
        except Exception as e:
            log.warning(f"[KITE] order_status({order_id}) failed: {e}")
        return None

    def funds(self) -> dict:
        import kite_rate_limiter as _krl
        try:
            kite = self._get_kite()
            _krl.acquire("account")
            m = kite.margins()
            eq = (m or {}).get("equity", {}) or {}
            avail = eq.get("available", {}) or {}
            cash = float(avail.get("live_balance", avail.get("cash", 0)) or 0)
            return {"available": cash, "raw": m}
        except Exception:
            # NOTE (same contract as DhanBroker.funds()): caller must treat
            # {} as "balance unknown" and fail-closed, not "balance is fine".
            return {}
