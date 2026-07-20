"""Regression test for the 2026-07-20 webhook recovery bug (nameless position).

After a mid-day restart, the live SHORT was rebuilt from order_store by
_recover_wh_state(). It read p.get("trad_sym") — but order_store's open dict
exposes the option symbol as "sym" (order_store._as_open), NOT "trad_sym" — so
every RECOVERED webhook position came back with a BLANK symbol. The 10:00
reversal then tried to close it, sent the broker a nameless order → REJECTED
("no trad_sym"), which aborted the reverse-long too. The short stayed open; the
user closed it by hand.

range_trader / rsi / universe recovery all read "sym" correctly — webhook was the
lone drift. This test locks the whole chain end-to-end (no DB writes — it drives
order_store's real _net_rows() and webhook_executor's real functions, isolating
only external deps like scrip-master + config):

  A  order_store open row exposes the symbol as "sym", NEVER "trad_sym" (contract)
  B  _recover_wh_state() rebuilds a NON-EMPTY opt_trad_sym (the real fix)
  C  _do_exit() refuses to send a nameless close order (defense-in-depth)
  D  a failed signal is NOT swallowed by dedup — TradingView's retry re-attempts
  E  architecture_audit check #10 (RECOVER-FIELD) fires on the buggy pattern
"""
import ast
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(BASE)
for _p in ("_data", "_core", "_ops", "_TOOLS", "."):
    sys.path.insert(0, os.path.join(BASE, _p))

fails = []


def check(name, got, want):
    ok = got == want
    print(("  PASS  " if ok else "  FAIL  ") + name)
    if not ok:
        print(f"          got={got!r} want={want!r}")
        fails.append(name)


SYM = "NIFTY-Jul2026-24150-CE"   # arschain_MAIN SHORT sells CE
SEC = "43231"
KEY = "arschain_MAIN|NIFTY"


def raw_row(**over):
    """A raw order_store DB row (COLUMNS schema) for one open live SELL leg."""
    r = {"id": 1, "ts": "2026-07-20 09:35:08", "date": "2026-07-20",
         "source": "webhook", "strategy": "arschain_MAIN", "mode": "live",
         "broker": "kite", "symbol": "NIFTY", "instrument": "options",
         "trad_sym": SYM, "sec_id": SEC, "segment": "NSE_FNO",
         "side": "SELL", "qty": 130, "price": 97.95,
         "correlation_id": "TVWH_NIFTY_1", "broker_order_id": "260720150262281",
         "status": "open", "tags": json.dumps([]), "product_type": "NRML",
         "group_id": ""}
    r.update(over)
    return r


import order_store           # noqa: E402
import dhan_master           # noqa: E402
import smart_order           # noqa: E402
import webhook_executor as wh  # noqa: E402  (runs a real read-only recovery at import)

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== A. order_store contract — open row symbol lives under 'sym' ===")
# Real order_store transformation (_net_rows) — no replicated shaping, no DB.
res = order_store._net_rows([raw_row()])
open_rows = res["open"]
check("A1 exactly one open row", len(open_rows), 1)
row = open_rows[0] if open_rows else {}
check("A2 symbol is under 'sym'", row.get("sym"), SYM)
check("A3 there is NO 'trad_sym' key (the bug's trap)", row.get("trad_sym"), None)
check("A4 sec_id present", str(row.get("sec_id")), SEC)

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== B. _recover_wh_state() rebuilds a NON-EMPTY symbol (the fix) ===")
_orig_tf = order_store.trades_for
_orig_cfg = wh._strat_cfg
_orig_ot = dhan_master.get_option_type_by_sec_id
try:
    order_store.trades_for = lambda *a, **k: {"open": [row], "details": [], "count": 0}
    wh._strat_cfg = lambda s: {"long_opt_type": "PE", "short_opt_type": "CE", "opt_action": "SELL"}
    dhan_master.get_option_type_by_sec_id = lambda sid: "CE"   # CE ⇒ SHORT for arschain
    wh._wh_state.clear()
    wh._recover_wh_state()
    st = wh._wh_state.get(KEY) or {}
    check("B1 position recovered", bool(st.get("position")), True)
    check("B2 opt_trad_sym is the REAL symbol (was '' before fix)", st.get("opt_trad_sym"), SYM)
    check("B3 direction resolved SHORT (CE via config)", st.get("direction"), "SHORT")
    check("B4 sec_id preserved", st.get("opt_sec_id"), SEC)
finally:
    order_store.trades_for = _orig_tf
    wh._strat_cfg = _orig_cfg
    dhan_master.get_option_type_by_sec_id = _orig_ot

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== C. _do_exit() refuses to send a NAMELESS close order ===")
_orig_exec = smart_order.execute
_orig_gts = dhan_master.get_trad_sym_for_sec_id


def _boom(*a, **k):
    raise AssertionError("smart_order.execute was called with a blank symbol!")


try:
    smart_order.execute = _boom
    dhan_master.get_trad_sym_for_sec_id = lambda sid: ""   # sec_id also can't resolve
    wh._wh_state[KEY] = {"position": "SHORT", "direction": "SHORT",
                         "opt_trad_sym": "", "opt_sec_id": "999999",
                         "opt_qty": 130, "opt_action": "SELL",
                         "entry_premium": 97.95, "broker": "kite",
                         "instrument": "options"}
    rx = wh._do_exit("arschain_MAIN", "NIFTY", {"mode": "live", "broker": "kite"},
                     reason="REVERSAL")
    check("C1 nameless exit BLOCKED (ok=False)", rx.get("ok"), False)
    check("C2 reason names the missing symbol", "symbol missing" in (rx.get("msg") or ""), True)
    check("C3 broker order NEVER placed (execute not called)", True, True)
except AssertionError as e:
    check("C3 broker order NEVER placed (execute not called)", f"CALLED: {e}", "not-called")
finally:
    smart_order.execute = _orig_exec
    dhan_master.get_trad_sym_for_sec_id = _orig_gts
    wh._wh_state.clear()

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== D. dedup must NOT swallow a retry of a FAILED signal ===")
_orig_entry = wh._do_entry
_orig_whs = wh._all_webhooks
calls = {"n": 0}
try:
    wh._all_webhooks = lambda: {"tstrat": {"active": True}}

    def _fail_entry(*a, **k):
        calls["n"] += 1
        return {"ok": False, "msg": "simulated transient failure"}

    wh._do_entry = _fail_entry
    wh._seen.clear()
    pl = {"id": "555", "strategy": "tstrat", "symbol": "NIFTY", "signal": "ENTRY", "action": "buy"}
    wh.handle_signal(pl)
    r2 = wh.handle_signal(pl)   # same id — must RETRY because #1 failed
    check("D1 failed signal RETRIED (both deliveries processed)", calls["n"], 2)
    check("D2 retry not reported as duplicate", r2.get("msg"), "simulated transient failure")

    # success path: dedup SHOULD hold (no double entry)
    calls["n"] = 0

    def _ok_entry(*a, **k):
        calls["n"] += 1
        return {"ok": True, "msg": "done"}

    wh._do_entry = _ok_entry
    wh._seen.clear()
    pl2 = {"id": "777", "strategy": "tstrat", "symbol": "NIFTY", "signal": "ENTRY", "action": "buy"}
    wh.handle_signal(pl2)
    s2 = wh.handle_signal(pl2)  # same id — success first time ⇒ 2nd deduped
    check("D3 successful signal DEDUPED (retry ignored)", calls["n"], 1)
    check("D4 2nd delivery reported duplicate", s2.get("msg"), "duplicate ignored")
finally:
    wh._do_entry = _orig_entry
    wh._all_webhooks = _orig_whs
    wh._seen.clear()

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== E. architecture_audit check #10 (RECOVER-FIELD) fires on the bug ===")
import architecture_audit as aa   # noqa: E402


def _find(src):
    tree = ast.parse(src)
    out = []
    aa.check_recover_field("x.py", src, tree, out)
    return [f for f in out if f.check == "RECOVER-FIELD"]


BUGGY = ("def _recover():\n"
         "    opens = order_store.trades_for(d).get('open') or []\n"
         "    for p in opens:\n"
         "        return p.get('trad_sym')\n")
FIXED = ("def _recover():\n"
         "    opens = order_store.trades_for(d).get('open') or []\n"
         "    for p in opens:\n"
         "        return p.get('sym')\n")
SUBSCRIPT = ("def _recover():\n"
             "    for p in order_store.trades_for(d).get('open') or []:\n"
             "        return p['trad_sym']\n")
check("E1 FIRES on p.get('trad_sym') over open rows", len(_find(BUGGY)), 1)
check("E2 SILENT on the fixed p.get('sym')", len(_find(FIXED)), 0)
check("E3 FIRES on p['trad_sym'] subscript form", len(_find(SUBSCRIPT)), 1)

# ─────────────────────────────────────────────────────────────────────────────
print("\n=== F. UNIVERSAL guard — smart_order.execute() blocks a nameless order (ALL strategies) ===")
_orig_gts2 = dhan_master.get_trad_sym_for_sec_id
_orig_mp = smart_order.marketable_price


class _DummyBroker:
    def place_order(self, *a, **k):
        raise AssertionError("broker.place_order was called with a blank symbol!")


try:
    smart_order.marketable_price = lambda *a, **k: (None, None)   # no network in test
    # (F1) sec_id also unresolvable → BLOCK, broker never touched
    dhan_master.get_trad_sym_for_sec_id = lambda sid: ""
    rf = smart_order.execute("BUY", "NIFTY", "999999", "NSE_FNO", 130, "", "live",
                             _DummyBroker(), log=lambda *a, **k: None, strategy="tstrat")
    check("F1 nameless order BLOCKED (ok=False)", rf.get("ok"), False)
    check("F2 reason == no_symbol", rf.get("reason"), "no_symbol")
    check("F3 broker.place_order NEVER called", True, True)
    # (F4) blank symbol but sec_id RESOLVES → guard lets it past (fails later on
    # price, NOT on symbol) — proves self-heal, not a false block
    dhan_master.get_trad_sym_for_sec_id = lambda sid: SYM
    rh = smart_order.execute("BUY", "NIFTY", SEC, "NSE_FNO", 130, "", "live",
                             _DummyBroker(), log=lambda *a, **k: None, strategy="tstrat")
    check("F4 self-heal lets it PAST the symbol guard (reason != no_symbol)",
          rh.get("reason") != "no_symbol", True)
except AssertionError as e:
    check("F3 broker.place_order NEVER called", f"CALLED: {e}", "not-called")
finally:
    dhan_master.get_trad_sym_for_sec_id = _orig_gts2
    smart_order.marketable_price = _orig_mp

# ─────────────────────────────────────────────────────────────────────────────
print()
if fails:
    print(f"RESULT: {len(fails)} FAILED -> {fails}")
    sys.exit(1)
print("RESULT: all passed ✅")
