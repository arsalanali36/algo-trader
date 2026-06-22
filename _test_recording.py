"""Offline test: smart_order.execute records into order_store (trade DB)."""
import tempfile, sys, types
from pathlib import Path
import order_store as os_

os_.DB_PATH = Path(tempfile.gettempdir()) / "_test_rec.db"
if os_.DB_PATH.exists():
    try: os_.DB_PATH.unlink()
    except Exception: pass
os_.init_db()

fd = types.ModuleType("dhan_feed"); fd.get_quote = lambda s: {}; sys.modules["dhan_feed"] = fd
import smart_order

class FakeBroker:
    def name(self): return "dhan"
    def quote(self, sec, seg): return {"ltp": 100.0, "bid": 99.9, "ask": 100.1}

res = smart_order.execute("SELL", "NIFTY", "123", "NSE_FNO", 65,
                          "NIFTY-Jun2026-24000-CE", "paper", FakeBroker(),
                          source="webhook", strategy="range5m",
                          instrument="options", broker_name="dhan")
assert res["ok"], res
rows = os_.query()
assert len(rows) == 1, rows
r = rows[0]
assert r["source"] == "webhook" and r["strategy"] == "range5m", r
assert r["mode"] == "paper" and r["broker"] == "dhan" and r["instrument"] == "options", r
assert r["side"] == "SELL" and r["trad_sym"] == "NIFTY-Jun2026-24000-CE", r
print("PASS smart_order.execute -> DB:", r["side"], r["trad_sym"], "@", r["price"],
      "| src=", r["source"], "strat=", r["strategy"])
print("ALL RECORDING WIRE TEST PASSED")
