import sys, os, tempfile, importlib
# point auto_straddle at a temp state file
tmp = tempfile.mkdtemp()
os.environ.setdefault("DUMMY","1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_ops"))
import auto_straddle as ast
# redirect its state file to temp
from pathlib import Path
ast._FILE = Path(tmp) / "auto_straddle.json"

fails=[]
def ok(n,c): print(("PASS " if c else "FAIL ")+n); (fails.append(n) if not c else None)

# fresh day, BANKNIFTY: not fired yet
ok("fresh: BANKNIFTY not fired", ast.fired_alert_today("BANKNIFTY") is False)
ok("fresh: NIFTY not fired", ast.fired_alert_today("NIFTY") is False)

# first alert entry → mark
ast.mark_alert("BANKNIFTY")
ok("after mark: BANKNIFTY BLOCKED (no re-entry)", ast.fired_alert_today("BANKNIFTY") is True)
ok("NIFTY still allowed (per-symbol, not global)", ast.fired_alert_today("NIFTY") is False)

# marker PERSISTS even after position 'exits' (no open straddle) — simulate empty straddles
raw = ast._read_raw(); ok("marker persisted in state", "BANKNIFTY" in raw.get("fired_alert", []))

# belt-and-suspenders: a recorded alert straddle also blocks (even without mark)
ast._FILE.write_text(__import__("json").dumps({"day": ast._today_ist(),
    "straddles":[{"symbol":"NIFTY","source":"alert:gamma_spike","status":"closed"}],
    "fired_920":[], "fired_alert":[]}))
ok("recorded alert straddle blocks NIFTY (belt-suspenders)", ast.fired_alert_today("NIFTY") is True)

# day rollover clears the marker (new day → allowed again)
ast._FILE.write_text(__import__("json").dumps({"day":"2020-01-01",
    "straddles":[], "fired_920":["BANKNIFTY"], "fired_alert":["BANKNIFTY"]}))
ok("day rollover: BANKNIFTY allowed again next day", ast.fired_alert_today("BANKNIFTY") is False)

# fired_920 still works independently (not broken by new field)
ast._FILE.write_text(__import__("json").dumps({"day":ast._today_ist(),
    "straddles":[], "fired_920":["NIFTY"], "fired_alert":[]}))
ok("fired_920 still works", ast.fired_920_today("NIFTY") is True)
ok("fired_920 doesn't leak into alert guard", ast.fired_alert_today("NIFTY") is False)

print("\n"+("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
