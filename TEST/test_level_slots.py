"""Standalone test — _ops/level_slots.py state machine (no broker/Dhan).
Run: python TEST/test_level_slots.py"""
import os, sys, json, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "_ops")); sys.path.insert(0, str(ROOT / "_CHARTING"))
import level_slots as ls

tmp = Path(tempfile.mkdtemp()) / "level_slots.json"
ls._FILE = tmp
n_pass = 0
def ok(cond, msg):
    global n_pass
    if not cond:
        print("FAIL:", msg); sys.exit(1)
    n_pass += 1; print("  ok:", msg)

ls.ensure_fixed()
ok(set(ls.list_underlyings()) == {"NIFTY", "BANKNIFTY", "BTC"}, "fixed tabs seeded")
ok(len(ls.list_slots("NIFTY")) == 4, "4 slots per underlying")
o, m = ls.remove_underlying("NIFTY"); ok(not o, "fixed tab cannot be removed")
o, m = ls.add_underlying("RELIANCE", {"lot": 500}); ok(o and len(ls.list_slots("RELIANCE")) == 4, "stock added w/ 4 slots")

sid = "NIFTY:I1"
# validation
o, m = ls.save_slot(sid, {"level": 0}); ok(not o, "level 0 rejected")
o, m = ls.save_slot(sid, {"level": 24700, "zone": 15, "exit": {"enabled": {"rs": False, "ip": False, "il": False}}})
ok(not o and "exit" in m.lower(), "no exit → rejected: " + m)
cfg = {"level": 24700, "zone": 15, "zone_unit": "pt", "from_dir": "below", "patterns": ["engulf", "hammer", "inside"],
       "hedge_delta": 0.25, "lots": 2, "tf": "5m", "valid_till": "23:59", "mode": "live",
       "exit": {"ip_sl": 25, "ip_tg": 50, "enabled": {"rs": False, "ip": True, "il": False}, "confirm_mode": "close"}}
o, s = ls.save_slot(sid, cfg); ok(o, "valid config saved")
ok(s["mode"] == "paper", "mode hard-locked paper (asked live)")
ok(s["exit"]["ip_sl"] == 25 and s["exit"]["enabled"]["ip"], "exit fields kept")

# arm refusals
o, m = ls.arm(sid, spot_now=24800); ok(not o and "UPAR" in m, "arm refused: spot already above resistance")
o, s = ls.arm(sid, spot_now=24600); ok(o and s["status"] == "armed", "armed")
ok(len(ls.active_slots()) == 1, "active_slots lists it")

# state machine: bars approach, zone candle w/ bear engulf, next candle breaks low
def bar(t, o, h, l, c): return {"time": t, "open": o, "high": h, "low": l, "close": c}
s = ls.get_slot(sid)
s2, fire, ch = ls.advance(s, [bar(1, 24600, 24620, 24590, 24610)], "10:00")
ok(not fire and s2["status"] == "armed", "far from zone: stays armed")
# green candle then bearish engulf inside zone 24685-24715
b_prev = bar(2, 24680, 24700, 24675, 24698)     # green, overlaps zone
b_pat  = bar(3, 24705, 24712, 24672, 24676)     # red, engulfs prev body (o>=pc, c<=po)
s3, fire, ch = ls.advance(s2, [bar(1, 24600, 24620, 24590, 24610), b_prev], "10:05")
ok(s3["status"] == "in_zone", "prev candle in zone (no pattern yet): " + s3["last_msg"])
s4, fire, ch = ls.advance(s3, [b_prev, b_pat], "10:10")
ok(s4["status"] == "pattern" and s4["pattern"]["name"] == "bear_engulf", "bear engulf detected: " + s4["last_msg"])
ok(s4["pattern"]["break_level"] == 24672, "break level = pattern candle LOW")
# same candle again → no change
s4b, fire, ch = ls.advance(s4, [b_prev, b_pat], "10:11"); ok(not ch and not fire, "same closed candle not re-evaluated")
# next candle does NOT break (close above low) → reset to armed
b_nb = bar(4, 24676, 24720, 24674, 24715)   # no break, not inside/pattern
s5, fire, ch = ls.advance(s4, [b_pat, b_nb], "10:15")
ok(not fire and s5["status"] in ("armed", "in_zone") and "pattern" not in s5, "no break → reset (re-watch): " + s5["last_msg"])
# rebuild pattern then break
b5 = bar(5, 24688, 24725, 24680, 24697)   # not inside prev, no pattern
s6, f6, _ = ls.advance(s5, [b_nb, b5], "10:20")
ok(f6 is False and s6["status"] in ("armed", "in_zone") and "pattern" not in s6, "plain zone candle: no pattern")
b_pat2 = bar(6, 24702, 24710, 24670, 24678)
s7, fire, _ = ls.advance(s6, [b5, b_pat2], "10:25")
ok(fire is False and s7["status"] == "pattern" and s7["pattern"]["name"] == "bear_engulf", "second pattern (bear engulf)")
b_brk = bar(7, 24678, 24682, 24650, 24655)
s8, fire, _ = ls.advance(s7, [b_pat2, b_brk], "10:30")
ok(fire is True, "next candle close < low → FIRE")
# wick mode: close inside but wick below
s8w, firew, _ = ls.advance(s7, [b_pat2, bar(7, 24678, 24682, 24660, 24675)], "10:30", entry_confirm="wick")
ok(firew is True, "wick mode fires on low < break level")
s8c, firec, _ = ls.advance(s7, [b_pat2, bar(7, 24678, 24682, 24660, 24675)], "10:30", entry_confirm="close")
ok(firec is False, "close mode does NOT fire on wick-only")

# claim one-shot
ls.apply_runtime(sid, s7)
ok(ls.claim(sid) is True, "claim ok from pattern state")
ok(ls.claim(sid) is False, "second claim refused")
ls.set_result(sid, True, "paper entered", {"group_id": "g1"})
ok(ls.get_slot(sid)["status"] == "entered", "entered")
o, m = ls.arm(sid, 24600); ok(not o and "ho chuki" in m, "re-arm blocked after fire today")
# failed fire → re-armable
ls.set_result("NIFTY:I2", False, "x")   # harmless on unconfigured
# expiry by valid_till
o, s = ls.save_slot("NIFTY:I2", dict(cfg, level=24480, valid_till="23:58", from_dir="above",
                                     exit={"il_sl": 24400, "il_tg": 24650, "enabled": {"rs": False, "ip": False, "il": True}}))
ok(o, "I2 saved (bullish)")
o, s = ls.arm("NIFTY:I2", 24550); ok(o, "I2 armed (spot above support ok)")
s9, fire, ch = ls.advance(s, [bar(9, 1, 2, 0.5, 1)], "23:59")
ok(s9["status"] == "expired", "past valid_till → expired")
# bullish pattern: hammer at support then next candle breaks HIGH
o, s = ls.save_slot("NIFTY:P1", {"level": 140, "zone": 3, "from_dir": "below", "lots": 1,
                                  "contract": {"opt": "CE", "strike": 24600, "sec_id": "111", "trad_sym": "NIFTY-24600-CE"},
                                  "exit": {"rs_sl": 1500, "rs_tg": 3000, "enabled": {"rs": True, "ip": False, "il": False}}})
ok(o and s["kind"] == "prem" and s["contract"]["sec_id"] == "111", "premium slot w/ contract")
o, s = ls.save_slot("NIFTY:P2", {"level": 120, "zone": 3, "from_dir": "below", "exit": {"rs_sl": 1, "enabled": {"rs": True}}})
ok(not o, "premium slot without contract rejected")
# day roll resets runtime
with ls._LOCK:
    d = ls._read(); d["day"] = "2000-01-01"; ls._write(d)
rows = ls.list_slots("NIFTY"); i1 = [r for r in rows if r["slot"] == "I1"][0]
ok(i1["fired"] is False and i1["status"] in ("entered", "idle"), "new day: fired cleared, re-armable")
ok(ls.option_side({"from_dir": "below"}) == ("CE", -1) and ls.option_side({"from_dir": "above"}) == ("PE", 1), "option side mapping")
print(f"\nALL {n_pass} checks passed")
