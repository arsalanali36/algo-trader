"""
base_broker.py — abstract broker interface.

Every broker (Dhan, Kite) implements this so the engine never talks to a
specific broker SDK directly. Swap brokers by changing config only.

Conventions (broker-agnostic):
  seg          : "NSE_EQ" | "NSE_FNO" | "IDX_I"   (logical segment)
  instrument   : "EQUITY" | "OPTIDX" | "OPTSTK" | "INDEX"
  side         : "BUY" | "SELL"
  order_type   : "MARKET" | "LIMIT"
  candles      : pandas DataFrame with columns [time, open, high, low, close, volume]
  quote        : {"ltp": float, "bid": float|None, "ask": float|None}
  order result : {"status": "filled"|"rejected"|"pending"|"paper",
                  "order_id": str|None, "fill_price": float|None,
                  "reason": str, "raw": <broker response>}
"""

from abc import ABC, abstractmethod


class BaseBroker(ABC):

    @abstractmethod
    def name(self) -> str:
        """Short broker id, e.g. 'dhan'."""

    @abstractmethod
    def intraday_candles(self, sec_id, seg, instrument, days: int = 5,
                         interval: int = 1):
        """Return intraday OHLC as a DataFrame [time,open,high,low,close,volume].
        Real-time data from broker's intraday API."""

    @abstractmethod
    def quote(self, sec_id, seg) -> dict:
        """Return {'ltp':float, 'bid':float|None, 'ask':float|None}."""

    @abstractmethod
    def place_order(self, side, sec_id, seg, qty, order_type="MARKET",
                    price=0.0, trad_sym=None, tag=None, product=None) -> dict:
        """Place a real order. Returns the order-result dict described above.
        product: 'MIS'/'INTRADAY' (default) or 'NRML'/'MARGIN' for overnight."""

    @abstractmethod
    def funds(self) -> dict:
        """Return {'available': float, 'raw': ...} or {} on failure."""

    def subscribe_feed(self, sec_ids, on_tick=None):
        """Optional: start a live bid/ask feed. Implemented per-broker in Phase 1
        (Dhan) — default no-op so brokers without a feed still work."""
        return None

    def order_status(self, order_id):
        """Optional: re-query a real order's CURRENT status by id (e.g.
        'TRADED'/'REJECTED'/'PENDING'). A broker's initial place_order response
        can say "accepted" while a price-band/freeze reject only arrives a
        moment later (async) — callers that care about a confirmed fill, not
        just an accepted submission, should call this ~1-1.5s after placing.
        Default: None (unsupported/unknown) so brokers without this wired
        degrade gracefully — caller must treat None as "couldn't confirm,
        don't downgrade a known-good status on it"."""
        return None

    def get_fill(self, order_id):
        """Optional: return (status_str, fill_price) for a placed order.
        status_str: 'TRADED' | 'REJECTED' | 'PENDING' | None
        fill_price: actual average fill price float, or None.
        Default: (None, None) — callers treat as "could not confirm"."""
        return None, None

    def positions(self) -> dict:
        """Return current net positions as a dict.
        Kite: {kite_tradingsymbol: net_qty}  (net_qty 0 = flat, <0 = short)
        Dhan: {sec_id_str: net_qty}
        Default: {} — broker that doesn't implement this degrades gracefully
        (broker_sync will skip reconciliation for that broker)."""
        return {}
