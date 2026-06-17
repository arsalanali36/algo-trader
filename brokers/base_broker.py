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
        Real-time data API — NO yfinance."""

    @abstractmethod
    def quote(self, sec_id, seg) -> dict:
        """Return {'ltp':float, 'bid':float|None, 'ask':float|None}."""

    @abstractmethod
    def place_order(self, side, sec_id, seg, qty, order_type="MARKET",
                    price=0.0, trad_sym=None, tag=None) -> dict:
        """Place a real order. Returns the order-result dict described above."""

    @abstractmethod
    def funds(self) -> dict:
        """Return {'available': float, 'raw': ...} or {} on failure."""

    def subscribe_feed(self, sec_ids, on_tick=None):
        """Optional: start a live bid/ask feed. Implemented per-broker in Phase 1
        (Dhan) — default no-op so brokers without a feed still work."""
        return None
