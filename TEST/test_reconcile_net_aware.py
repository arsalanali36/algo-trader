"""Replay of the 2026-07-20 phantom-double-count incident + net-aware reconcile fix.

What happened live: webhook exit failed, the user manually bought 2×65 to close an
arschain SHORT (130). broker_sync's ghost-sync recorded ONE 130-qty exit row
(corr=1822718); reconcile then matched the two REAL 65-qty fills by exact-qty
signature, found no 65-qty row (only the 130 one), and inserted BOTH as "manual"
→ the same 130 counted twice → two phantom open BUY longs, while Kite was flat.

Fix: reconcile is now NET-AWARE — it records a manual fill only if it moves
order_store's net for that contract TOWARD the broker's real net. Broker flat +
book flat ⇒ nothing inserted. Plus a trade-id cross-path dedup (a fill already
referenced by a strategy exit's correlation_id is never re-recorded).

Runs the REAL broker_sync.reconcile_manual_trades against a TEMP db (real
trades.db is never touched) with a faked Kite broker.
"""
import os
import sys
import tempfile
from datetime import datetime, timezone, timedelta

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
import broker_sync
import brokers

TODAY = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
DHAN_SYM = "NIFTY-Jul2026-24150-CE"
KITE_SYM = "NIFTY26JUL24150CE"


class FakeKite:
    """Minimal stand-in for KiteBroker used by reconcile_manual_trades."""
    def __init__(self, trades, net):
        self._trades = trades
        self._net = net           # {kite_sym: signed_net_qty}

    def trades(self):
        return self._trades

    def positions(self):
        return dict(self._net)

    def resolve_dhan(self, kite_sym):
        if kite_sym == KITE_SYM:
            return ("43231", DHAN_SYM, 75)
        return (None, None, None)


def _fill(tid, oid, side, qty, px, sym=KITE_SYM):
    return {"trade_id": tid, "order_id": oid, "transaction_type": side,
            "quantity": qty, "average_price": px, "tradingsymbol": sym,
            "fill_timestamp": f"{TODAY} 10:02:30"}


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    order_store.DB_PATH = __import__("pathlib").Path(path)   # both order_store + reconcile read this
    order_store.init_db()
    return path


def _open_syms():
    return [p for p in order_store.trades_for(TODAY).get("open", []) if p.get("sym") == DHAN_SYM]


def _cleanup(path):
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except OSError:
            pass   # WAL sidecar / file-lock on Windows — temp file, OS reclaims it


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== A. THE INCIDENT — flat broker, arschain round-trip already in book ===")
path = _fresh_db()
try:
    # arschain SHORT (130) that the user's manual buys closed
    order_store.record("SELL", 130, 97.95, source="webhook", strategy="arschain_MAIN",
                       mode="live", broker="kite", symbol="NIFTY", instrument="options",
                       trad_sym=DHAN_SYM, sec_id="43231", segment="NSE_FNO",
                       correlation_id="TVWH_NIFTY", status="filled", tags=[],
                       ts=f"{TODAY} 09:35:06")
    # broker_sync's ghost-exit: ONE 130-qty BUY, referencing ONE real fill's trade_id
    order_store.record("BUY", 130, 101.85, source="webhook", strategy="arschain_MAIN",
                       mode="live", broker="kite", symbol="NIFTY", instrument="options",
                       trad_sym=DHAN_SYM, sec_id="43231", segment="NSE_FNO",
                       correlation_id="1822718", status="filled",
                       tags=["EXTERNALLY_CLOSED", "MANUAL_EXIT_BROKER"],
                       ts=f"{TODAY} 10:02:55")
    check("A0 arschain nets flat in book (no open leg)", len(_open_syms()), 0)

    # the two REAL manual fills (65 + 65). One shares the ghost-exit's trade_id.
    fake = FakeKite(
        trades=[_fill("1818745", "OID_A", "BUY", 65, 101.5),
                _fill("1822718", "OID_B", "BUY", 65, 101.85)],
        net={})                                   # Kite is FLAT
    brokers.get_broker = lambda name="kite": fake

    res = broker_sync.reconcile_manual_trades(date=TODAY, broker_name="kite", log=lambda *a, **k: None)
    check("A1 reconcile inserted NOTHING (net in sync)", res.get("manual_inserted"), 0)
    check("A2 still no phantom open long", len(_open_syms()), 0)
finally:
    order_store.DB_PATH = __import__("pathlib").Path(BASE) / "data" / "trades.db"   # restore
    _cleanup(path)


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== B. GENUINE manual trade the app never saw — MUST still be recorded ===")
path = _fresh_db()
try:
    fake = FakeKite(
        trades=[_fill("9001", "OID_X", "BUY", 65, 120.0)],
        net={KITE_SYM: 65})                       # broker really holds +65
    brokers.get_broker = lambda name="kite": fake
    res = broker_sync.reconcile_manual_trades(date=TODAY, broker_name="kite", log=lambda *a, **k: None)
    check("B1 genuine untracked manual BUY recorded", res.get("manual_inserted"), 1)
    check("B2 shows as one open long", len(_open_syms()), 1)

    # running it AGAIN must not double-insert (net now matches)
    res2 = broker_sync.reconcile_manual_trades(date=TODAY, broker_name="kite", log=lambda *a, **k: None)
    check("B3 re-run adds nothing (idempotent, net matches)", res2.get("manual_inserted"), 0)
    check("B4 still exactly one open long", len(_open_syms()), 1)
finally:
    order_store.DB_PATH = __import__("pathlib").Path(BASE) / "data" / "trades.db"
    _cleanup(path)


# ─────────────────────────────────────────────────────────────────────────────
print()
if fails:
    print(f"RESULT: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("RESULT: all passed ✅")
