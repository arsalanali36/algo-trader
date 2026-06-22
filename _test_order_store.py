"""Offline test: order_store SQLite trade DB."""
import tempfile, os
from pathlib import Path
import order_store as os_

os_.DB_PATH = Path(tempfile.gettempdir()) / "_test_trades.db"
if os_.DB_PATH.exists():
    os_.DB_PATH.unlink()

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name); assert cond, name

os_.init_db()
D = "2026-06-22"
# webhook range5m NIFTY: SELL 65 @100 (entry) then BUY 65 @80 (exit) → +1300
os_.record("SELL", 65, 100.0, source="webhook", strategy="range5m", mode="paper",
           broker="dhan", symbol="NIFTY", instrument="options",
           trad_sym="NIFTY-Jun2026-24000-CE", ts=D+" 09:20:00")
os_.record("BUY", 65, 80.0, source="webhook", strategy="range5m", mode="paper",
           broker="dhan", symbol="NIFTY", instrument="options",
           trad_sym="NIFTY-Jun2026-24000-CE", ts=D+" 09:50:00")
# manual SBIN equity BUY 1 @800 (still open)
os_.record("BUY", 1, 800.0, source="manual", strategy="", mode="live", broker="kite",
           symbol="SBIN", instrument="equity", trad_sym="SBIN", ts=D+" 10:05:00")

rows = os_.query(date=D)
check("3 rows recorded", len(rows) == 3)
check("filter source=manual → 1", len(os_.query(date=D, source="manual")) == 1)
check("filter mode=live → 1", len(os_.query(date=D, mode="live")) == 1)
check("filter broker=kite → 1", len(os_.query(date=D, broker="kite")) == 1)
check("filter strategy=range5m → 2", len(os_.query(date=D, strategy="range5m")) == 2)

t = os_.trades_for(D)
check("1 completed trade", t["count"] == 1)
d0 = t["details"][0]
check("completed pnl +1300", abs(d0["pnl"] - 1300.0) < 1e-6)
check("completed tagged webhook/range5m", d0["source"] == "webhook" and d0["strategy"] == "range5m")
check("1 open position", len(t["open"]) == 1)
check("open is SBIN equity manual", t["open"][0]["sym"] == "SBIN" and t["open"][0]["source"] == "manual")
check("distinct sources", set(os_.distinct("source", D)) == {"webhook", "manual"})

print("\nALL ORDER_STORE TESTS PASSED")
