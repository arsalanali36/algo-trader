#!/usr/bin/env python3
"""
deploy_vps.py — CODE3B ko VPS pe push karo (tarball via SCP, ek command me)

Kaise kaam karta hai:
  1. curated code files ka tarball banata hai (tarfile — Windows pe bhi spaced
     remote dir ke saath reliable, scp per-file quoting ka jhamela nahi)
  2. VPS pe purani files ka timestamped backup leta hai (rollback ke liye)
  3. tarball extract karta hai REMOTE_DIR me
  4. zaroori pip deps ensure karta hai (e.g. `ta`)
  5. algo-dashboard restart + health check

Usage:  python deploy_vps.py
        python deploy_vps.py --dry-run     # sirf list dikhao, kuch bhejo mat

NOTE: secrets (data/config.json), state (nifty_config.json, backtest_db.json,
logs/, results/, data/) aur Pine store (_PINE/ — sync_pine.py se sync hota hai)
JAAN-BOOJH KAR exclude hain. Inhe VPS pe alag manage karo.
"""

import os
import sys
import glob
import time
import tarfile
import tempfile
import subprocess

HOST       = "root@72.61.173.32"
KEY        = os.path.expanduser("~/.ssh/khazana_ed25519")
if not os.path.exists(KEY):
    for alt in [r"C:\Users\arsal\.ssh\khazana_ed25519", r"C:\Users\91933\.ssh\khazana_ed25519"]:
        if os.path.exists(alt):
            KEY = alt
            break
REMOTE_DIR = "/root/CODE3B- TV BACKTEST ENGINE"   # dir name me space hai — sab jagah quote
SERVICE    = "algo-dashboard"
PORT       = 5099
HERE       = os.path.dirname(os.path.abspath(__file__))

# pip deps jo VPS venv me honi chahiye (idempotent ensure)
EXTRA_PIP  = ["ta"]

# ---- Curated root-level files (tests/scratch/secrets/state NAHI) ----
ROOT_FILES = [
    "trader_dashboard.py",
    "smart_order.py",
    "order_store.py",
    "webhook_executor.py",
    "dhan_master.py",
    "dhan_feed.py",
    "universe.py",
    "mfe_routes.py",
    "auto_data_downloader.py",
    "optimize_strategy.py",
    "download_nifty50.py",
    "download_equity_history.py",
    "sync_data.py",
    "sync_pine.py",
    "risk_gate.py",
    "strategy_safety.py",
]

# ---- Folder globs (relative paths preserve hote hain tar me) ----
FOLDER_GLOBS = [
    "_CHARTING/*.py",
    "_TOOLS/*.py",
    "_TRADERS/*.py",
    "_TRADERS/*.json",
    "brokers/*.py",
    "strategies/*.py",
    "templates/*.html",
]

# in patterns wali files chhod do (curated globs me ghus na jaayein)
SKIP_SUBSTR = ["_test_", "scratch_test", "/test", "__pycache__", ".bak"]


def collect_files():
    files = []
    for f in ROOT_FILES:
        p = os.path.join(HERE, f)
        if os.path.isfile(p):
            files.append(f.replace("\\", "/"))
        else:
            print(f"  WARN: missing root file {f} (skip)")
    for pat in FOLDER_GLOBS:
        for p in glob.glob(os.path.join(HERE, pat)):
            rel = os.path.relpath(p, HERE).replace("\\", "/")
            if any(s in rel for s in SKIP_SUBSTR):
                continue
            files.append(rel)
    # de-dup, stable order
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.rstrip())
    if r.returncode != 0:
        print(f"  ERROR: {r.stderr.strip()}")
        sys.exit(1)
    return r


def main():
    dry = "--dry-run" in sys.argv
    files = collect_files()

    print(f"\nDeploying CODE3B -> {HOST}:{REMOTE_DIR}\n")
    print(f"  {len(files)} files:")
    for f in files:
        print(f"    {f}")
    if dry:
        print("\n  [dry-run] kuch nahi bheja.\n")
        return

    # 1) tarball banao
    tgz = os.path.join(tempfile.gettempdir(), "code3b_deploy.tgz")
    with tarfile.open(tgz, "w:gz") as tar:
        for f in files:
            tar.add(os.path.join(HERE, f), arcname=f)
    print(f"\n  tarball -> {tgz} ({os.path.getsize(tgz)//1024} KB)")

    SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no"]
    SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", HOST]

    # 2) upload
    print("  uploading tarball...")
    run(SCP + [tgz, f"{HOST}:/tmp/code3b_deploy.tgz"])

    # 3) remote: backup + extract + deps + restart  (spaced dir => quote)
    ts = int(time.time())
    pip_line = (f"venv/bin/pip install -q {' '.join(EXTRA_PIP)};"
                if EXTRA_PIP else "true;")
    remote = (
        f"cd '{REMOTE_DIR}' && "
        f"tar czf /tmp/code3b_backup_{ts}.tgz "
        f"$(tar tzf /tmp/code3b_deploy.tgz) 2>/dev/null; "
        f"echo 'backup -> /tmp/code3b_backup_{ts}.tgz'; "
        f"tar xzf /tmp/code3b_deploy.tgz -C '{REMOTE_DIR}/' && echo 'EXTRACT OK'; "
        f"{pip_line} "
        f"systemctl restart {SERVICE}; sleep 4; "
        f"echo -n 'service: '; systemctl is-active {SERVICE}; "
        f"echo -n 'http: '; curl -s -o /dev/null -w '%{{http_code}}\\n' http://localhost:{PORT}/"
    )
    print("  remote: backup -> extract -> deps -> restart...")
    run(SSH + [remote])

    print(f"\nDone! Open: http://72.61.173.32:{PORT}\n")


if __name__ == "__main__":
    main()
