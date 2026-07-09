"""Offline test: multi-strategy webhook routing (no network)."""
import sys, types
from datetime import datetime

import webhook_executor as wh

# ── fake config: global + active strategies (avoid auto-register-skip) ───────
_FAKE_CFG = {
    "webhooks": {
        "global": {"secret_token": "wh_test", "daily_amount_cap": 5000, "global_max_trades": 0},
        "default": {"active": True, "max_trades_per_day": 2},
        "range5m": {"active": True, "max_trades_per_day": 2},
        "ema1m":   {"active": True, "max_trades_per_day": 2},
    }
}
wh._raw_cfg = lambda: dict(_FAKE_CFG)

# ── stub out all network calls ──────────────────────────────────────────────
wh.ist_now = lambda: datetime(2026, 6, 22, 10, 0, 0)        # fixed 10:00 IST (no cutoff)
wh._index_spot = lambda symbol: 24000.0

import dhan_master, smart_order
_fake_contracts = {}
def _fake_contract(symbol, spot, opt_type, offset):
    sec = f"SEC_{symbol}_{opt_type}_{offset}"
    return sec, f"{symbol}-Jun2026-{int(spot)}-{opt_type}", 65
dhan_master.get_option_contract = _fake_contract
dhan_master.get_equity_info = lambda s: ("13", "IDX_I", "INDEX")

_orders = []
def _fake_execute(side, sym, sec_id, seg, qty, trad_sym, mode, broker, **kw):
    _orders.append((side, sym, trad_sym, qty, mode))
    return {"ok": True, "price": 100.0, "src": "test", "status": "paper",
            "reason": "paper", "order_id": None}
smart_order.execute = _fake_execute

# dhan_feed.add no-op
sys.modules.setdefault("dhan_feed", types.ModuleType("dhan_feed"))
import dhan_feed
dhan_feed.add = lambda *a, **k: None
dhan_feed.get_quote = lambda *a, **k: {}

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name

# ── 1. config map loads global + default ────────────────────────────────────
whs = wh._all_webhooks()
check("global block present", "global" in whs and whs["global"].get("secret_token"))
check("default strat present", "default" in whs)
check("secret helper works", wh.webhook_secret() == whs["global"]["secret_token"])

# ── 2. two strategies, same symbol → two isolated positions ─────────────────
wh._wh_state.clear(); wh._trades_today.clear(); wh._day_realized = 0.0; wh._seen.clear()
r1 = wh.handle_signal({"id": "a1", "strategy": "range5m", "symbol": "NIFTY",
                       "signal": "ENTRY", "action": "buy"})
r2 = wh.handle_signal({"id": "a2", "strategy": "ema1m", "symbol": "NIFTY",
                       "signal": "ENTRY", "action": "buy"})
check("range5m entry ok", r1["ok"])
check("ema1m entry ok", r2["ok"])
check("two isolated positions", set(wh._wh_state.keys()) == {"range5m|NIFTY", "ema1m|NIFTY"})

# ── 3. same strat, two instruments → two positions, same config ─────────────
wh._wh_state.clear(); wh._trades_today.clear(); wh._seen.clear()
wh.handle_signal({"id": "b1", "strategy": "range5m", "symbol": "NIFTY",
                  "signal": "ENTRY", "action": "buy"})
wh.handle_signal({"id": "b2", "strategy": "range5m", "symbol": "BANKNIFTY",
                  "signal": "ENTRY", "action": "sell"})
check("multi-instrument isolated",
      set(wh._wh_state.keys()) == {"range5m|NIFTY", "range5m|BANKNIFTY"})

# ── 4. duplicate position blocked, dedup works ──────────────────────────────
dup = wh.handle_signal({"id": "b3", "strategy": "range5m", "symbol": "NIFTY",
                        "signal": "ENTRY", "action": "buy"})
check("second entry same key blocked", not dup["ok"])
ded = wh.handle_signal({"id": "b1", "strategy": "range5m", "symbol": "NIFTY",
                        "signal": "ENTRY", "action": "buy"})
check("dedup id ignored", ded["ok"] and "duplicate" in ded["msg"])

# ── 5. per-(strat,symbol) max trades/day cap ────────────────────────────────
wh._wh_state.clear(); wh._trades_today.clear(); wh._seen.clear()
# default max_trades_per_day = 2; enter+exit twice then third blocked
for i in range(2):
    wh.handle_signal({"id": f"c_e{i}", "strategy": "default", "symbol": "NIFTY",
                      "signal": "ENTRY", "action": "buy"})
    wh.handle_signal({"id": f"c_x{i}", "strategy": "default", "symbol": "NIFTY",
                      "signal": "EXIT"})
blocked = wh.handle_signal({"id": "c_e2", "strategy": "default", "symbol": "NIFTY",
                            "signal": "ENTRY", "action": "buy"})
check("max trades/day blocks 3rd", not blocked["ok"] and "max" in blocked["msg"])

# ── 6. exit records realized pnl ────────────────────────────────────────────
wh._wh_state.clear(); wh._trades_today.clear(); wh._day_realized = 0.0; wh._seen.clear()
wh.handle_signal({"id": "d1", "strategy": "default", "symbol": "NIFTY",
                  "signal": "ENTRY", "action": "buy"})   # SELL CE @100 (opt_action SELL)
# patch execute to exit at 80 (premium fell → SELL profit)
smart_order.execute = lambda *a, **k: {"ok": True, "price": 80.0, "status": "paper",
                                       "reason": "paper", "order_id": None}
wh.handle_signal({"id": "d2", "strategy": "default", "symbol": "NIFTY", "signal": "EXIT"})
check("realized pnl tracked (SELL 100 to 80, qty65 = +1300)",
      abs(wh._day_realized - 1300.0) < 1e-6)

# ── 7. unknown strategy → auto-register (inactive), no trade ────────────────
wh._wh_state.clear(); wh._trades_today.clear(); wh._seen.clear()
_registered = []
wh._register_strategy = lambda s: _registered.append(s)   # stub: no disk write
smart_order.execute = _fake_execute
unk = wh.handle_signal({"id": "e1", "strategy": "brand_new", "symbol": "NIFTY",
                        "signal": "ENTRY", "action": "buy"})
check("unknown strategy not traded", not unk["ok"] and "register" in unk["msg"])
check("unknown strategy registered", _registered == ["brand_new"])
check("no position created for unknown", "brand_new|NIFTY" not in wh._wh_state)

print("\nALL WEBHOOK MULTI-STRATEGY TESTS PASSED ✅")
