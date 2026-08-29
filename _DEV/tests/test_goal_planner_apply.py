"""
test_goal_planner_apply.py — Goal Planner ke APPLY path ka guard.

Ye test asli nifty_config.json ko KABHI nahi chhuta — module ke paths ko ek temp copy pe
point karta hai. Jo cheezein yahan lock ki gayi hain wo isliye hain ki inme se koi bhi
chup-chaap toote to asli paise ka risk hai:

  1. lots + capital_rs likhe jaate hain
  2. `mode` aur `active` KABHI nahi badalte (paper→live planner se possible nahi)
  3. live-mode member ho to bina typed confirm ke kuch nahi likhta
  4. paper_only=True live members ko chhodta hai
  5. khuli position wali strategy ke lots NAHI badalte (queue hote hain)
  6. rollback poora config wapas laata hai
  7. readback verify (jo likha, wahi wapas mila)

Run: python -X utf8 _DEV/tests/test_goal_planner_apply.py
"""
import os
import sys
import json
import shutil
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
for _p in (_ROOT, os.path.join(_ROOT, "_ops")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import goal_planner as gp  # noqa: E402

FAIL = []


def chk(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAIL.append(msg)


BASE_CFG = {
    "papertest_v1": {"active": True, "mode": "paper", "lots": 2, "symbol": "NIFTY"},
    "livetest_v1": {"active": True, "mode": "live", "qty": 3, "symbol": "BANKNIFTY"},
    "opentest_v1": {"active": True, "mode": "paper", "lots": 1, "symbol": "NIFTY"},
    "_risk": {"per_strategy": {"papertest_v1": {"capital_rs": None, "max_loss_rs": 5000}}},
}

PLAN = {
    "name": "test", "target": 30000, "to_date": "2026-09-30", "dd_budget": 60000,
    "capital": 500000,
    "members": [
        {"id": "P1", "label": "Paper One", "config_key": "papertest_v1",
         "lots": 5, "capital_cap_rs": 250000},
        {"id": "L1", "label": "Live One", "config_key": "livetest_v1",
         "lots": 7, "capital_cap_rs": 400000},
        {"id": "O1", "label": "Open One", "config_key": "opentest_v1",
         "lots": 4, "capital_cap_rs": 100000},
        {"id": "X1", "label": "Missing", "config_key": "nope_v1",
         "lots": 9, "capital_cap_rs": 999},
    ],
}


def setup(tmp, open_legs=None):
    cfg_path = os.path.join(tmp, "nifty_config.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(json.loads(json.dumps(BASE_CFG)), fh, indent=1)
    gp.NIFTY_CFG = cfg_path
    gp.PLAN_PATH = os.path.join(tmp, "roadmap_plan.json")
    gp.AUDIT_PATH = os.path.join(tmp, "audit.jsonl")
    gp.BACKUP_DIR = os.path.join(tmp, "backups")
    gp._open_legs_by_strategy = lambda: dict(open_legs or {})
    gp._resolve_key = lambda ck: str(ck or "").lower()
    return cfg_path


def read(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    print("\n=== 1. confirm ke bina live member = kuch nahi likhta ===")
    tmp = tempfile.mkdtemp()
    cfg_path = setup(tmp)
    before = read(cfg_path)
    r = gp.apply_plan(PLAN)
    chk(not r["ok"], "confirm ke bina apply REFUSE hua")
    chk(read(cfg_path) == before, "config bilkul nahi badla (byte-identical)")
    chk(gp.CONFIRM_TOKEN in (r.get("reason") or ""), "reason me confirm token bataya")

    print("\n=== 2. galat token bhi refuse ===")
    r = gp.apply_plan(PLAN, confirm="haan karo")
    chk(not r["ok"], "galat confirm string se apply nahi hua")
    chk(read(cfg_path) == before, "config phir bhi nahi badla")

    print("\n=== 3. sahi token = lots + capital_rs likhe, mode/active NAHI ===")
    r = gp.apply_plan(PLAN, confirm=gp.CONFIRM_TOKEN)
    cfg = read(cfg_path)
    chk(r["ok"], "apply ok")
    chk(cfg["papertest_v1"]["lots"] == 5, "paper strategy lots 2 -> 5")
    chk(cfg["livetest_v1"]["qty"] == 7, "live strategy `qty` field detect hua, 3 -> 7")
    chk(cfg["_risk"]["per_strategy"]["papertest_v1"]["capital_rs"] == 250000,
        "capital_rs likha gaya")
    chk(cfg["_risk"]["per_strategy"]["papertest_v1"]["max_loss_rs"] == 5000,
        "per_strategy ke doosre fields chhue nahi gaye")
    chk(cfg["papertest_v1"]["mode"] == "paper" and cfg["livetest_v1"]["mode"] == "live",
        "mode DONO me waisa hi hai (planner mode kabhi nahi badalta)")
    chk(cfg["papertest_v1"]["active"] is True and cfg["livetest_v1"]["active"] is True,
        "active flag chhua nahi gaya")
    chk(cfg["papertest_v1"]["symbol"] == "NIFTY", "baaki config fields intact")
    chk("nope_v1" not in cfg, "config me na-maujood key create NAHI hui (skip)")
    chk(all(v["ok"] for v in r["verify"]), "readback verify sab ok")

    print("\n=== 4. rollback = poora config wapas ===")
    rb = gp.rollback()
    chk(rb["ok"], "rollback ok")
    chk(read(cfg_path) == before, "config exactly pehle jaisa (byte-identical)")

    print("\n=== 5. khuli position wali strategy ke lots NAHI badalte (queue) ===")
    tmp2 = tempfile.mkdtemp()
    cfg2 = setup(tmp2, open_legs={"opentest_v1": 2})
    r = gp.apply_plan(PLAN, confirm=gp.CONFIRM_TOKEN)
    cfg = read(cfg2)
    chk(r["ok"], "apply ok")
    chk(cfg["opentest_v1"]["lots"] == 1, "khuli position wali strategy lots UNCHANGED (1)")
    chk("O1" in (r["plan"]["queued"] or []), "wo strategy queued list me hai")
    chk(cfg["papertest_v1"]["lots"] == 5, "baaki strategies phir bhi apply hui")

    print("\n=== 6. paper_only = live member chhoot jaata hai, confirm bhi nahi chahiye ===")
    tmp3 = tempfile.mkdtemp()
    cfg3 = setup(tmp3)
    r = gp.apply_plan(PLAN, paper_only=True)
    cfg = read(cfg3)
    chk(r["ok"], "paper-only apply bina confirm ke chala")
    chk(cfg["papertest_v1"]["lots"] == 5, "paper strategy apply hui")
    chk(cfg["livetest_v1"]["qty"] == 3, "LIVE strategy bilkul nahi chhui (3 hi hai)")
    chk("L1" in (r["plan"]["skipped"] or []), "live member skipped list me")

    print("\n=== 7. 0-lot member ko chhua nahi jaata (band karna alag kaam hai) ===")
    tmp4 = tempfile.mkdtemp()
    cfg4 = setup(tmp4)
    plan0 = json.loads(json.dumps(PLAN))
    plan0["members"][0]["lots"] = 0
    r = gp.apply_plan(plan0, confirm=gp.CONFIRM_TOKEN)
    cfg = read(cfg4)
    chk(cfg["papertest_v1"]["lots"] == 2, "0-lot member ke lots UNCHANGED (2)")
    chk(cfg["papertest_v1"]["active"] is True, "0-lot member band bhi nahi hui")

    print("\n=== 8. preview koi write nahi karta ===")
    tmp5 = tempfile.mkdtemp()
    cfg5 = setup(tmp5)
    snap = read(cfg5)
    pv = gp.preview_apply(PLAN)
    chk(read(cfg5) == snap, "preview ke baad config byte-identical")
    chk(pv["needs_confirm"] is True, "live member ki wajah se needs_confirm True")
    rows = {r["id"]: r for r in pv["rows"]}
    chk(rows["P1"]["lots_from"] == 2 and rows["P1"]["lots_to"] == 5, "preview diff sahi")
    chk(rows["X1"]["skip_reason"] is not None, "missing key ka skip_reason bataya")
    chk(all(r["mode_changes"] is False for r in pv["rows"]), "koi mode change nahi dikhata")

    for d in (tmp, tmp2, tmp3, tmp4, tmp5):
        shutil.rmtree(d, ignore_errors=True)

    print("\n" + "=" * 62)
    if FAIL:
        print(f"FAILED: {len(FAIL)}")
        for f in FAIL:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
