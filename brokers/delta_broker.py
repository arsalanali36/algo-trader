"""
delta_broker.py — Delta Exchange India broker plugin (crypto options/futures).

Implements the SAME BaseBroker interface as CODE3B's dhan_broker.py / kite_broker.py,
so it plugs into execution_gateway / smart_order / pos_monitor with zero changes.

Conventions mapped for crypto (24/7 market):
  sec_id       : Delta product SYMBOL string, e.g. "C-BTC-81200-250826" or "BTCUSD"
                 (Delta's own unique id; we resolve symbol<->product_id via /v2/products)
  seg          : "CRYPTO_OPT" | "CRYPTO_FUT"   (informational; Delta doesn't need it)
  side         : "BUY" | "SELL"
  order_type   : "MARKET" | "LIMIT"

Credentials are OPTIONAL: public market-data (candles/quote) needs none.
Private calls (place_order/funds/positions/trades) require DELTA_API_KEY +
DELTA_API_SECRET (from env or a .env file); without them they degrade gracefully
(funds/positions -> {}, place_order -> rejected) so Step-1 paper/data runs safely.

Auth (Delta India): HMAC-SHA256 over  method + timestamp + path + query + body,
headers: api-key / timestamp / signature.  Trading keys need IP-whitelisting;
read-only keys do not.  Same IPv4-vs-IPv6 gotcha as Dhan/Kite on a VPS — CODE3B
already forces AF_INET globally, so no monkeypatch here.
"""
import os
import time
import hmac
import hashlib
import json as _json
import requests

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None

MAINNET_BASE = "https://api.india.delta.exchange"
TESTNET_BASE = "https://cdn-ind.testnet.deltaex.org"   # Delta India testnet (paper, real matching)


def _is_testnet():
    return os.getenv("DELTA_TESTNET", "").lower() in ("1", "true", "yes")


def _base_url():
    return TESTNET_BASE if _is_testnet() else MAINNET_BASE


BASE = MAINNET_BASE   # module default (public display); broker instance uses self.base


def _load_env_file():
    """Read KEY=VALUE lines from a sibling .env (never committed). Non-fatal."""
    # project root (one level up from brokers/) .env — never committed
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


_load_env_file()


class DeltaBroker:
    """Delta Exchange India broker. Mirrors BaseBroker (duck-typed)."""

    def __init__(self, api_key=None, api_secret=None, testnet=None):
        self.api_key = api_key or os.getenv("DELTA_API_KEY", "")
        self.api_secret = api_secret or os.getenv("DELTA_API_SECRET", "")
        self.testnet = _is_testnet() if testnet is None else testnet
        self.base = TESTNET_BASE if self.testnet else MAINNET_BASE
        self._sess = requests.Session()
        self._sess.headers.update({"Accept": "application/json",
                                   "User-Agent": "khazana-delta/1.0"})
        self._prod_cache = {}   # symbol -> product dict
        self._prod_ts = 0.0

    # ---- identity ---------------------------------------------------------
    def name(self):
        return "delta"

    def has_creds(self):
        return bool(self.api_key and self.api_secret)

    # ---- signed request helper -------------------------------------------
    def _signed(self, method, path, params=None, body=None):
        if not self.has_creds():
            return None
        ts = str(int(time.time()))
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        payload = "" if body is None else _json.dumps(body, separators=(",", ":"))
        prehash = method + ts + path + query + payload
        sig = hmac.new(self.api_secret.encode(), prehash.encode(),
                       hashlib.sha256).hexdigest()
        headers = {"api-key": self.api_key, "timestamp": ts, "signature": sig,
                   "Content-Type": "application/json"}
        try:
            r = self._sess.request(method, self.base + path + query, data=payload or None,
                                   headers=headers, timeout=20)
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def _public(self, path, params=None):
        try:
            r = self._sess.get(self.base + path, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        return None

    # ---- product resolution (symbol <-> product_id) -----------------------
    def _products(self):
        if self._prod_cache and (time.time() - self._prod_ts) < 3600:
            return self._prod_cache
        out, after = {}, None
        while True:
            params = {"page_size": 1000}
            if after:
                params["after"] = after
            j = self._public("/v2/products", params)
            if not j:
                break
            for p in j.get("result", []):
                out[p.get("symbol")] = p
            after = (j.get("meta") or {}).get("after")
            if not after:
                break
        if out:
            self._prod_cache, self._prod_ts = out, time.time()
        return self._prod_cache or out

    def product_id(self, symbol):
        p = self._products().get(symbol)
        return p.get("id") if p else None

    # ---- market data (public, no creds) -----------------------------------
    def intraday_candles(self, sec_id, seg=None, instrument=None, days=5, interval=1):
        """OHLC DataFrame [time,open,high,low,close,volume]. sec_id = Delta symbol.
        interval in MINUTES (Delta resolution '1m'/'5m'/...)."""
        res = f"{int(interval)}m"
        end = int(time.time())
        start = end - int(days) * 86400
        j = self._public("/v2/history/candles",
                         {"symbol": sec_id, "resolution": res,
                          "start": start, "end": end})
        rows = (j or {}).get("result", []) if j else []
        rows = sorted(rows, key=lambda x: x["time"])   # oldest first
        if pd is None:
            return rows
        if not rows:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rows)
        df = df.rename(columns={"time": "time"})
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        return df[["time", "open", "high", "low", "close", "volume"]]

    def quote(self, sec_id, seg=None):
        """{'ltp':float,'bid':float|None,'ask':float|None} from live ticker."""
        j = self._public(f"/v2/tickers/{sec_id}")
        t = (j or {}).get("result") if j else None
        if not t:
            return {"ltp": 0.0, "bid": None, "ask": None}
        q = t.get("quotes") or {}
        def _f(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        ltp = _f(t.get("mark_price")) or _f(t.get("close")) or 0.0
        return {"ltp": ltp or 0.0, "bid": _f(q.get("best_bid")),
                "ask": _f(q.get("best_ask")), "greeks": t.get("greeks"),
                "iv": _f((q.get("mark_iv"))), "oi": _f(t.get("oi")),
                "spot": _f(t.get("spot_price"))}

    # ---- account / trading (signed; need creds) ---------------------------
    def funds(self):
        j = self._signed("GET", "/v2/wallet/balances")
        if not j or "result" not in j:
            return {}
        avail = 0.0
        for w in j["result"]:
            try:
                avail += float(w.get("available_balance", 0) or 0)
            except (TypeError, ValueError):
                pass
        return {"available": avail, "raw": j["result"]}

    def positions(self):
        j = self._signed("GET", "/v2/positions/margined")
        out = {}
        if j and "result" in j:
            for p in j["result"]:
                sym = (p.get("product") or {}).get("symbol") or str(p.get("product_id"))
                try:
                    out[sym] = int(p.get("size", 0) or 0)
                except (TypeError, ValueError):
                    out[sym] = 0
        return out

    def place_order(self, side, sec_id, seg=None, qty=1, order_type="MARKET",
                    price=0.0, trad_sym=None, tag=None, product=None):
        """Real order (needs a TRADING key + whitelisted IP). Step-1 does NOT call this."""
        pid = self.product_id(sec_id)
        if pid is None:
            return {"status": "rejected", "order_id": None, "fill_price": None,
                    "reason": f"unknown product {sec_id}", "raw": None}
        body = {"product_id": pid,
                "size": int(qty),
                "side": "buy" if side.upper() == "BUY" else "sell",
                "order_type": "market_order" if order_type.upper() == "MARKET"
                              else "limit_order"}
        if order_type.upper() == "LIMIT":
            body["limit_price"] = str(price)
        j = self._signed("POST", "/v2/orders", body=body)
        if not j or not j.get("success", j.get("result")):
            return {"status": "rejected", "order_id": None, "fill_price": None,
                    "reason": (j or {}).get("error", "order failed"), "raw": j}
        r = j.get("result", {})
        return {"status": r.get("state", "pending"),
                "order_id": str(r.get("id")),
                "fill_price": None, "reason": "", "raw": j}
