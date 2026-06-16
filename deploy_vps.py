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
    "trader_dashboard.py",
    "save_daily_summary.py",
]

SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no"]
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no", HOST]

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ERROR: {r.stderr.strip()}")
        sys.exit(1)

print("\n🚀 Deploying to VPS...\n")

for f in FILES:
    print(f"  uploading {f}...")
    run(SCP + [f, f"{HOST}:{REMOTE_DIR}/{f}"])
    print(f"  ✅ {f}")

print("\n  Restarting dashboard...")
run(SSH + ["systemctl restart algo-dashboard"])
print("  ✅ Dashboard restarted\n")

print("Done! Open: http://72.61.173.32:5099\n")
