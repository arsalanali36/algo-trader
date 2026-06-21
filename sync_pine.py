#!/usr/bin/env python3
"""
sync_pine.py — Pine version store (dashboard "Pine > History") ko local (Windows)
aur VPS ke beech sync karta hai. Smart UNION merge: kabhi koi version drop nahi
hota — dono taraf ke saare versions mila ke DONO pe likh deta hai (ekdum identical).

Usage:
    python sync_pine.py            # local <-> VPS union merge, dono ko same kar do

Workflow: Pine version ek hi jagah save karo (recommend: LOCAL dashboard pe),
phir ye chala do. (Dono jagah save kiya ho to bhi union sambhaal lega.)

Store = _PINE/versions.json + har version ka v{N}.pine snapshot + *_latest.pine.
"""
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

HOST = "root@72.61.173.32"
KEY  = r"C:\Users\arsal\.ssh\khazana_ed25519"
RDIR = "/root/CODE3B- TV BACKTEST ENGINE/_PINE"          # VPS _PINE (space ok — scp SFTP literal)
LDIR = Path(__file__).resolve().parent / "_PINE"          # local _PINE

SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no"]
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", HOST]


def run(cmd, fatal=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()[:300]
        if fatal:
            print("  ERROR:", msg); sys.exit(1)
        return None
    return r.stdout


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main():
    if not (LDIR / "versions.json").exists():
        print("Local versions.json nahi mila:", LDIR); sys.exit(1)

    tmp = LDIR / "_vps_versions.tmp.json"
    print("Fetching VPS versions.json ...")
    run(SCP + [f"{HOST}:{RDIR}/versions.json", str(tmp)])

    local = load(LDIR / "versions.json")
    vps   = load(tmp)
    tmp.unlink()

    by_id_local = {v["version"]: v for v in local}
    by_id_vps   = {v["version"]: v for v in vps}

    # collision warn: same id, different content
    for vid in set(by_id_local) & set(by_id_vps):
        if by_id_local[vid].get("name") != by_id_vps[vid].get("name"):
            print(f"  ⚠️  version id {vid} dono jagah alag strategy ka hai — "
                  f"local rakha ja raha hai (manual dekho).")

    # UNION (local wins on id clash)
    merged = dict(by_id_vps)
    merged.update(by_id_local)
    merged_list = sorted(merged.values(), key=lambda v: v["version"])

    vps_only_ids   = [vid for vid in by_id_vps   if vid not in by_id_local]
    local_only_ids = [vid for vid in by_id_local if vid not in by_id_vps]

    # 1) VPS-only versions ke snapshots local me laao
    for vid in vps_only_ids:
        run(SCP + [f"{HOST}:{RDIR}/v{vid}.pine", str(LDIR / f"v{vid}.pine")], fatal=False)
    # VPS *_latest.pine jo local me nahi
    vps_ls = run(SSH + [f"ls -1 '{RDIR}'/*_latest.pine 2>/dev/null || true"], fatal=False) or ""
    for line in vps_ls.splitlines():
        fn = os.path.basename(line.strip())
        if fn and not (LDIR / fn).exists():
            run(SCP + [f"{HOST}:{RDIR}/{fn}", str(LDIR / fn)], fatal=False)

    # 2) merged index local me likho
    (LDIR / "versions.json").write_text(
        json.dumps(merged_list, indent=2, ensure_ascii=False), encoding="utf-8")

    # 3) merged store VPS pe push (versions.json + saare v*.pine + *_latest.pine)
    push_files = ["versions.json"]
    push_files += [f"v{v['version']}.pine" for v in merged_list]
    push_files += [os.path.basename(p) for p in glob.glob(str(LDIR / "*_latest.pine"))]
    seen, pushed = set(), 0
    for f in push_files:
        if f in seen:
            continue
        seen.add(f)
        lp = LDIR / f
        if lp.exists():
            run(SCP + [str(lp), f"{HOST}:{RDIR}/{f}"])
            pushed += 1

    print(f"\n✅ Sync done — dono identical ({len(merged_list)} versions).")
    if vps_only_ids:
        print(f"   VPS -> local laaye: {vps_only_ids}")
    if local_only_ids:
        print(f"   local -> VPS bheje: {local_only_ids}")
    print(f"   {pushed} files VPS pe mirror. Dono dashboards refresh karo.")


if __name__ == "__main__":
    main()
