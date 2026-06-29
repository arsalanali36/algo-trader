"""
dhan_broker.py — Dhan implementation of BaseBroker.

- intraday candles  : dhanhq SDK  intraday_minute_data  (real-time, paid Data API)
- quote / ltp       : REST /v2/marketfeed/ltp           (proven working)
- orders            : REST /v2/orders                   (proven working live)
- live bid/ask feed : dhan_feed.py (Phase 1) via subscribe_feed

IPv4 force (DH-905) at module top — Dhan rejects IPv6 on the VPS.
"""

import json
import socket
from pathlib import Path
from typing import Optional

import requests

from .base_broker import BaseBroker
import dhan_rate_limiter as _rl

# --- IPv4 force (DH-905) — MUST be before any Dhan network call ---
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4

BASE_DIR    = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"
ORDERS_URL  = "https://api.dhan.co/v2/orders"
LTP_URL     = "https://api.dhan.co/v2/marketfeed/ltp"

# logical seg -> Dhan marketfeed/ltp segment key
_LTP_SEG = {"NSE_EQ": "NSE_EQ", "NSE_FNO": "NSE_FNO", "IDX_I": "IDX_I"}


class DhanBroker(BaseBroker):

    def __init__(self, creds: Optional[dict] = None):
        if creds:
            self.token = creds["jwt_token"]
            self.cid   = creds["client_id"]
        else:
            cfg = json.loads(CONFIG_FILE.read_text())
            self.token = cfg["jwt_token"]
            self.cid   = cfg["client_id"]
        self._sdk = None  # lazy — only build when candles needed

    # ---- helpers ----
    def name(self) -> str:
        return "dhan"

    def _hdrs(self):
        return {"access-token": self.token, "client-id": self.cid,
                "Content-Type": "application/json"}

    def _get_sdk(self):
        # `DhanContext` doesn't exist in the installed dhanhq==2.0.2 (same
        # mismatch already found+fixed in dhan_feed.py — TRAP #11/#12 — but
        # this call site was missed back then). The installed `dhanhq` class
        # takes (client_id, access_token) directly.
        if self._sdk is None:
            from dhanhq import dhanhq as _dhanhq_cls
            self._sdk = _dhanhq_cls(self.cid, self.token)
        return self._sdk

    # ---- data ----
    def intraday_candles(self, sec_id, seg, instrument, days: int = 5,
                         interval: int = 1):
        import datetime as _dt
        import pandas as pd
        today = _dt.date.today()
        frm = (today - _dt.timedelta(days=days)).isoformat()
        to  = today.isoformat()
        _rl.acquire("candle")
        r = self._get_sdk().intraday_minute_data(
            str(sec_id), seg, instrument, frm, to, interval)
        if r.get("status") != "success":
            raise RuntimeError(f"Dhan candles failed: {str(r)[:200]}")
        d = r.get("data", {}) or {}
        df = pd.DataFrame({
            "time":   d.get("timestamp", []),
            "open":   d.get("open", []),
            "high":   d.get("high", []),
            "low":    d.get("low", []),
            "close":  d.get("close", []),
            "volume": d.get("volume", []),
        })
        # Dhan timestamp is epoch seconds in UTC -> shift to IST (+5:30)
        if not df.empty:
            df["time"] = pd.to_datetime(df["time"], unit="s") + pd.Timedelta(hours=5, minutes=30)
        return df

    def quote(self, sec_id, seg) -> dict:
        # Cross-process shared cache first — every strategy process (range_trader,
        # rsi_trader, webhook, universe_trader, ...) shares this same Dhan account's
        # ~1 req/sec LTP limit. Reusing whatever ANY process fetched in the last
        # few seconds turns "N processes hitting Dhan" into ~1 real call per
        # symbol per window, which is what actually fixes DH-904 429s under load
        # (a per-process retry/cache alone doesn't, since other processes keep
        # consuming the same shared limit regardless of what this one does).
        import shared_ltp_cache
        cached = shared_ltp_cache.get(sec_id, max_age=3.0)
        if cached:
            return {"ltp": cached, "bid": None, "ask": None}

        key = _LTP_SEG.get(seg, "NSE_EQ")
        try:
            _rl.acquire("ltp")
            r = requests.post(LTP_URL, json={key: [int(sec_id)]},
                              headers=self._hdrs(), timeout=5)
            if r.status_code == 429:
                _rl.note_429()
            if r.status_code == 200:
                node = r.json().get("data", {}).get(key, {}) or {}
                for _sid, v in node.items():
                    ltp = float(v.get("last_price") or v.get("ltp") or 0) or None
                    if ltp:
                        shared_ltp_cache.put(sec_id, ltp)
                    return {"ltp": ltp, "bid": None, "ask": None}
        except Exception:
            pass

        # last resort: a slightly-stale value from ANY process beats failing outright
        stale = shared_ltp_cache.get_stale(sec_id, max_age=15.0)
        return {"ltp": stale, "bid": None, "ask": None}

    # ---- orders ----
    def place_order(self, side, sec_id, seg, qty, order_type="MARKET",
                    price=0.0, trad_sym=None, tag=None) -> dict:
        import time as _t
        body = {
            "dhanClientId":    self.cid,
            "correlationId":   (tag or f"BK_{trad_sym or sec_id}")[:25] + f"_{int(_t.time())}",
            "transactionType": side,
            "exchangeSegment": seg,
            "productType":     "INTRADAY",
            "orderType":       order_type,
            "validity":        "DAY",
            "securityId":      str(sec_id),
            "tradingSymbol":   trad_sym or "",
            "quantity":        int(qty),
            "price":           round(float(price), 2) if order_type == "LIMIT" else 0,
            "triggerPrice":    0,
        }
        try:
            _rl.acquire("order")
            r = requests.post(ORDERS_URL, json=body, headers=self._hdrs(), timeout=10)
            if r.status_code == 429:
                _rl.note_429()
            raw = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            if r.status_code == 200:
                oid = (raw or {}).get("orderId") if isinstance(raw, dict) else None
                status = (raw or {}).get("orderStatus", "") if isinstance(raw, dict) else ""
                st = "rejected" if str(status).upper() == "REJECTED" else "pending"
                return {"status": st, "order_id": oid, "fill_price": None,
                        "reason": str(status or "submitted"), "raw": raw}
            return {"status": "rejected", "order_id": None, "fill_price": None,
                    "reason": f"HTTP {r.status_code}: {str(raw)[:200]}", "raw": raw}
        except Exception as e:
            return {"status": "rejected", "order_id": None, "fill_price": None,
                    "reason": str(e), "raw": None}

    def order_status(self, order_id):
        if not order_id:
            return None
        try:
            _rl.acquire("order")
            r = requests.get(f"{ORDERS_URL}/{order_id}", headers=self._hdrs(), timeout=6)
            if r.status_code != 200:
                return None
            d = r.json()
            if isinstance(d, list) and d:
                d = d[0]
            if isinstance(d, dict):
                return str(d.get("orderStatus") or "").upper() or None
        except Exception:
            pass
        return None

    def funds(self) -> dict:
        try:
            _rl.acquire("account")
            r = self._get_sdk().get_fund_limits()
            if r.get("status") == "success":
                d = r.get("data", {}) or {}
                return {"available": float(d.get("availabelBalance", 0) or 0),
                        "collateral": float(d.get("collateralAmount", 0) or 0), "raw": d}
        except Exception:
            pass
        # NOTE: caller (risk_gate.check_broker_funds) must treat {} as
        # "balance unknown" and fail-closed, not "balance is zero/fine".
        return {}
