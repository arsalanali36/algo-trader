"""invariant_guard — proactive 'app == reality + always-true rules hold?' sentinel.
Each invariant is exercised against a temp DB + a faked Kite broker (real trades.db
never touched). This is the automatic unknown-catcher: it fires on divergence
without knowing the bug's name.
"""
import os
import sys
import tempfile
from pathlib import Path

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
for _p in ("_data", "_core", "_ops", "brokers", "."):
    sys.path.insert(0, os.path.join(BASE, _p))

fails = []


def check(name, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + name)
    if not ok:
        print(f"          got={got!r} want={want!r}")
        fails.append(name)


import order_store
import brokers
import invariant_guard as ig
from datetime import datetime, timezone, timedelta

TODAY = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
KITE_SYM = "NIFTY26JUL24150CE"
DHAN_SYM = "NIFTY-Jul2026-24150-CE"


class FakeKite:
    def __init__(self, net):
        self._net = net
    def positions(self):
        return dict(self._net)
    def resolve_dhan(self, ksym):
        return ("43231", DHAN_SYM, 75) if ksym == KITE_SYM else (None, None, None)


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    order_store.DB_PATH = Path(path)
    order_store.init_db()
    return path


def _cleanup(path):
    for s in ("", "-wal", "-shm"):
        try:
            os.remove(path + s)
        except OSError:
            pass


def _rec(**kw):
    d = dict(source="webhook", strategy="arschain_MAIN", mode="live", broker="kite",
             symbol="NIFTY", instrument="options", trad_sym=DHAN_SYM, sec_id="43231",
             segment="NSE_FNO", status="filled", tags=[], ts=f"{TODAY} 10:00:00")
    d.update(kw)
    side = d.pop("side"); qty = d.pop("qty"); price = d.pop("price")
    order_store.record(side, qty, price, **d)


def reds(v):
    return sorted({x.invariant for x in v if x.severity == "RED"})


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== A. CLEAN — app net == broker net → no violations ===")
p = _fresh_db()
try:
    _rec(side="SELL", qty=65, price=97.95)                 # app net −65
    brokers.get_broker = lambda name="kite": FakeKite({KITE_SYM: -65})
    v = ig.check_all(TODAY)
    check("A1 zero RED when app matches broker", reds(v), [])
finally:
    order_store.DB_PATH = Path(BASE) / "data" / "trades.db"; _cleanup(p)


print("\n=== B. PHANTOM — app open long, broker FLAT → app_vs_broker fires ===")
p = _fresh_db()
try:
    _rec(side="BUY", qty=65, price=101.5, source="manual", strategy="manual")   # app +65
    brokers.get_broker = lambda name="kite": FakeKite({})                        # broker flat
    v = ig.check_all(TODAY)
    check("B1 app_vs_broker RED", "app_vs_broker" in reds(v), True)
finally:
    order_store.DB_PATH = Path(BASE) / "data" / "trades.db"; _cleanup(p)


print("\n=== C. BLANK SYMBOL / ₹0 PRICE / DUP TRADE-ID / INSANE MTM ===")
p = _fresh_db()
try:
    brokers.get_broker = lambda name="kite": FakeKite({KITE_SYM: 0})
    # blank symbol
    _rec(side="SELL", qty=65, price=50.0, trad_sym="", sec_id="99")
    # ₹0 price
    _rec(side="SELL", qty=65, price=0.0, trad_sym="NIFTY-Jul2026-24200-PE", sec_id="88")
    # duplicate trade-id: a strategy exit (corr=555) + a manual row (corr=MANUAL_TID_555)
    _rec(side="BUY", qty=65, price=10.0, trad_sym="NIFTY-Jul2026-24300-CE", sec_id="77", correlation_id="555")
    _rec(side="BUY", qty=65, price=10.0, trad_sym="NIFTY-Jul2026-24300-CE", sec_id="77",
         correlation_id="MANUAL_TID_555", source="manual", strategy="manual")
    # insane implied value
    _rec(side="SELL", qty=99999, price=999.0, trad_sym="NIFTY-Jul2026-24400-PE", sec_id="66")
    v = ig.check_all(TODAY)
    r = reds(v)
    check("C1 blank_symbol RED", "blank_symbol" in r, True)
    check("C2 bad_price_qty RED", "bad_price_qty" in r, True)
    check("C3 duplicate_trade_id RED", "duplicate_trade_id" in r, True)
    check("C4 mtm_sane RED", "mtm_sane" in r, True)
finally:
    order_store.DB_PATH = Path(BASE) / "data" / "trades.db"; _cleanup(p)


print("\n=== D. broker unreachable → UNKNOWN (never a false all-clear) ===")
p = _fresh_db()
try:
    def _boom(name="kite"):
        raise RuntimeError("kite down")
    brokers.get_broker = _boom
    v = ig.check_all(TODAY)
    check("D1 broker-down reported as UNKNOWN not clean",
          any(x.severity == "UNKNOWN" and x.invariant == "app_vs_broker" for x in v), True)
finally:
    order_store.DB_PATH = Path(BASE) / "data" / "trades.db"; _cleanup(p)


print()
if fails:
    print(f"RESULT: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("RESULT: all passed ✅")
