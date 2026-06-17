#!/usr/bin/env python3
"""
deploy_vps.py — VPS pe files push karo (SCP via ED25519 key)
Usage: python deploy_vps.py
"""

import subprocess
import sys

HOST       = "root@72.61.173.32"
KEY        = r"C:\Users\arsal\.ssh\khazana_ed25519"
REMOTE_DIR = "/root/code4"

# Yeh files deploy hongi — secrets aur logs nahi
FILES = [
    "nifty_ema_trader.py",
    "rsi_trader.py",
    "range_trader.py",
    "trader_dashboard.py",
    "dhan_master.py",
    "save_daily_summary.py",
    "backtest_dashboard.html",
]

SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no"]
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", HOST]

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ERROR: {r.stderr.strip()}")
        sys.exit(1)

print("\nDeploying to VPS...\n")

# folders ensure karo
run(SSH + [f"mkdir -p {REMOTE_DIR}/templates {REMOTE_DIR}/brokers {REMOTE_DIR}/strategies"])

for f in FILES:
    print(f"  uploading {f}...")
    run(SCP + [f, f"{HOST}:{REMOTE_DIR}/{f}"])
    print(f"  OK {f}")

# package folders (brokers/, strategies/) — push all .py
import glob as _glob
for pkg in ("brokers", "strategies"):
    for f in _glob.glob(f"{pkg}/*.py"):
        f = f.replace("\\", "/")
        print(f"  uploading {f}...")
        run(SCP + [f, f"{HOST}:{REMOTE_DIR}/{f}"])
        print(f"  OK {f}")

# templates/index.html
print("  uploading templates/index.html...")
run(SCP + ["templates/index.html", f"{HOST}:{REMOTE_DIR}/templates/index.html"])
print("  OK templates/index.html")

print("\n  Restarting dashboard...")
run(SSH + ["systemctl restart algo-dashboard"])
print("  OK Dashboard restarted\n")

print("Done! Open: http://72.61.173.32:5099\n")
