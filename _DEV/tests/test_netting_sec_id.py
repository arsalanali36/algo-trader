"""Regression guard — netting must pair legs only WITHIN the same sec_id.

trad_sym carries only month+year (no expiry DAY), so a weekly and a monthly of one
strike collapse to one string (NIFTY-Aug2026-24650-PE = both the 11-Aug weekly
sec 41019 AND the 26-Aug monthly sec 61786). If _net_rows FIFO-pairs across sec_ids
it manufactures phantom completed trades — live 2026-08-10 produced a -25,638 / -33%
"trade" (and a +30,979 twin) that never happened. This test replays that exact set.

Run: python -X utf8 -c "import sys; sys.path.insert(0,'_core'); \
      import runpy; runpy.run_path('_DEV/tests/test_netting_sec_id.py', run_name='__main__')"
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_core"))
import order_store as O  # noqa: E402


def _row(id, ts, side, qty, price, sec, trad_sym, strat="manual", src="manual",
         mode="live", status="filled"):
    return {"id": id, "ts": ts, "source": src, "strategy": strat, "mode": mode,
            "broker": "kite", "instrument": "OPTIDX", "symbol": "NIFTY", "tags": "",
            "sec_id": sec, "segment": "NSE_FNO", "product_type": "NRML",
            "group_id": "", "status": status, "side": side, "qty": qty,
            "price": price, "trad_sym": trad_sym}


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name


# ── Case 1: the live 2026-08-10 24650-PE set (weekly 41019 + monthly 61786) ──
TS = "NIFTY-Aug2026-24650-PE"
rows = [
    _row(1, "2026-08-10 13:42", "SELL", 260, 208.50, "61786", TS),   # monthly
    _row(2, "2026-08-10 13:47", "BUY",  325,  89.35, "41019", TS),   # weekly
    _row(3, "2026-08-10 13:47", "BUY",  260, 208.21, "61786", TS),   # monthly
    _row(4, "2026-08-10 13:47", "SELL",  65,  93.55, "41019", TS),   # weekly
    _row(5, "2026-08-10 13:50", "BUY",   65,  90.90, "41019", TS),   # weekly
    _row(6, "2026-08-10 14:53", "SELL", 325, 109.60, "41019", TS),   # weekly
]
d = O._net_rows(rows)
maxabs = max(abs(t["pnl"]) for t in d["details"])
check("no cross-expiry phantom (max |pnl| < 10000)", maxabs < 10000)
check("all legs net flat (no orphan open)", not d["open"])
# monthly 61786 nets to itself: SELL 208.5 -> BUY 208.21 = +75.4
monthly = [t for t in d["details"] if abs(t["entry_price"] - 208.50) < 0.01]
check("monthly leg nets to itself (~+75)", len(monthly) == 1 and abs(monthly[0]["pnl"] - 75.4) < 0.5)

# ── Case 2: same strike, SAME sec_id → must STILL net normally (no regression) ──
rows2 = [
    _row(10, "2026-08-10 10:00", "SELL", 65, 100.0, "41019", TS),
    _row(11, "2026-08-10 11:00", "BUY",  65,  80.0, "41019", TS),
]
d2 = O._net_rows(rows2)
check("same sec_id still nets (1 completed)", d2["count"] == 1)
check("same sec_id pnl correct (+1300)", abs(d2["details"][0]["pnl"] - 1300.0) < 0.01)

# ── Case 3: blank sec_id on one leg → falls back to trad_sym grouping (still nets) ──
rows3 = [
    _row(20, "2026-08-10 10:00", "SELL", 65, 100.0, "",      TS, src="strategy", strat="rsi_v1"),
    _row(21, "2026-08-10 11:00", "BUY",  65,  80.0, "41019", TS, src="manual",   strat="manual"),
]
d3 = O._net_rows(rows3)
check("blank sec_id still pairs (manual close of strategy leg)", d3["count"] == 1)

print("\nALL NETTING sec_id TESTS PASSED")
