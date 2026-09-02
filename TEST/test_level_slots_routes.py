"""Route smoke for /api/level-slots* via Flask test_client (forged login session,
temp state file, NO Dhan/broker calls — arm is expected to refuse without a price
unless the shared LTP cache happens to be warm). Run: python -X utf8 TEST/test_level_slots_routes.py"""
import sys, json, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import _paths  # noqa
import level_slots as ls
ls._FILE = Path(tempfile.mkdtemp()) / "level_slots.json"
import trader_dashboard as td

app = td.app
app.config["TESTING"] = True
c = app.test_client()
with c.session_transaction() as sess:
    sess["auth_user"] = "test"

n = 0
def ok(cond, msg):
    global n
    if not cond:
        print("FAIL:", msg); sys.exit(1)
    n += 1; print("  ok:", msg)

r = c.get("/level-slots"); ok(r.status_code == 200 and b"LEVEL SPREAD SLOTS" in r.data, "page renders")
d = c.get("/api/level-slots").get_json(); ok(d["ok"] and set(d["underlyings"]) >= {"NIFTY", "BANKNIFTY", "BTC"} and len(d["slots"]) == 12, "list: 3 fixed × 4 slots")
ok(d["mode"] == "paper", "mode paper")
d = c.get("/api/level-slots/search?q=RELI").get_json(); ok(d["ok"] and "RELIANCE" in d["symbols"], "search F&O: RELIANCE")
d = c.post("/api/level-slots/underlying", json={"sym": "XYZNOTREAL"}).get_json(); ok(not d["ok"], "non-F&O rejected: " + d["msg"])
d = c.post("/api/level-slots/underlying", json={"sym": "RELIANCE"}).get_json(); ok(d["ok"], "RELIANCE added")
d = c.get("/api/level-slots").get_json(); ok(len(d["slots"]) == 16, "16 slots now")
d = c.delete("/api/level-slots/underlying/NIFTY").get_json(); ok(not d["ok"], "fixed tab remove refused")
bad = c.post("/api/level-slots/NIFTY:I1", json={"level": 24700}).get_json(); ok(not bad["ok"], "save w/o exit rejected: " + bad["msg"])
good = c.post("/api/level-slots/NIFTY:I1", json={"level": 24700, "zone": 15, "from_dir": "below", "lots": 2, "tf": "5m",
    "exit": {"ip_sl": 25, "ip_tg": 50, "enabled": {"rs": False, "ip": True, "il": False}, "confirm_mode": "close"}}).get_json()
ok(good["ok"] and good["slot"]["level"] == 24700 and good["slot"]["mode"] == "paper", "save ok + paper lock")
d = c.post("/api/level-slots/NIFTY:I1/arm").get_json(); print("   arm →", d["msg"])
ok("msg" in d, "arm route answers (refuses w/o price or arms)")
d = c.post("/api/level-slots/NIFTY:I1/disarm").get_json(); ok(d["ok"], "disarm ok")
d = c.get("/api/level-slots/NIFTY:I1/chart?tf=5m").get_json(); ok("bars" in d, f"chart route answers (bars={len(d.get('bars', []))}, ok={d.get('ok')})")
d = c.get("/api/level-slots/contracts?sym=NIFTY&opt=CE").get_json(); ok("rows" in d, f"contracts route answers (ok={d.get('ok')} rows={len(d.get('rows', []))})")
d = c.delete("/api/level-slots/underlying/RELIANCE").get_json(); ok(d["ok"], "RELIANCE removed")
# unauthenticated → gate
c2 = app.test_client(); r = c2.get("/api/level-slots"); ok(r.status_code in (401, 302), f"login gate ({r.status_code})")
print(f"\nALL {n} route checks passed")
