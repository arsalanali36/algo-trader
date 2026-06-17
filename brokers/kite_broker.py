"""
kite_broker.py — Zerodha Kite Connect implementation (STUB / future).

Same interface as DhanBroker. Filled in when the user takes Kite Connect.
The engine code never changes — only the config 'broker' key switches here.

Kite mapping notes (for later):
  - candles  : kite.historical_data(instrument_token, from, to, "minute")
  - quote    : kite.quote(["NSE:RELIANCE"]) -> depth.buy[0].price / sell[0].price
  - orders   : kite.place_order(variety, exchange, tradingsymbol, transaction_type,
               quantity, product="MIS", order_type="LIMIT", price=...)
  - sec_id   : Kite uses instrument_token, not Dhan security_id -> needs a
               separate instruments dump (kite.instruments()).
"""

from .base_broker import BaseBroker


class KiteBroker(BaseBroker):
    def __init__(self, creds: dict | None = None):
        self.creds = creds

    def name(self) -> str:
        return "kite"

    def _todo(self, what):
        raise NotImplementedError(
            f"KiteBroker.{what} not implemented yet — Zerodha Kite Connect "
            f"integration is planned. Use broker='dhan' for now.")

    def intraday_candles(self, sec_id, seg, instrument, days=5, interval=1):
        self._todo("intraday_candles")

    def quote(self, sec_id, seg):
        self._todo("quote")

    def place_order(self, side, sec_id, seg, qty, order_type="MARKET",
                    price=0.0, trad_sym=None, tag=None):
        self._todo("place_order")

    def funds(self):
        return {}
