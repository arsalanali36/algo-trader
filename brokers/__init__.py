"""
brokers/ — broker abstraction layer (switchable: Dhan now, Zerodha Kite later).

Usage:
    from brokers import get_broker
    bk = get_broker("dhan")          # reads creds from data/config.json
    df = bk.intraday_candles(sec_id, "NSE_EQ", "EQUITY")
    q  = bk.quote(sec_id, "NSE_EQ")
    r  = bk.place_order("BUY", sec_id, "NSE_EQ", qty, "LIMIT", price)

Config drives the active broker: nifty_config.json -> "broker": "dhan".
No yfinance anywhere — candles come from the broker's data API.
"""

from .base_broker import BaseBroker  # noqa: F401


def get_broker(name: str = "dhan", creds: dict | None = None) -> BaseBroker:
    name = (name or "dhan").lower()
    if name == "dhan":
        from .dhan_broker import DhanBroker
        return DhanBroker(creds)
    if name in ("kite", "zerodha"):
        from .kite_broker import KiteBroker
        return KiteBroker(creds)
    raise ValueError(f"Unknown broker: {name}")
