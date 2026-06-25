#!/usr/bin/env python3
"""
sync_vps_to_local.py - Download all data, configs, database, logs, and trading files
from the VPS to the local repository to populate the local dashboard.
"""

import os
import sys
import subprocess
import tarfile
import tempfile

HOST = "root@72.61.173.32"
REMOTE_DIR = "/root/CODE3B- TV BACKTEST ENGINE"
LOCAL_DIR = os.path.dirname(os.path.abspath(__file__))

# Dynamically locate SSH key
KEY = os.path.expanduser("~/.ssh/khazana_ed25519")
if not os.path.exists(KEY):
    # Fallback to absolute paths if home directory expansion fails
    alternative_keys = [
        r"C:\Users\91933\.ssh\khazana_ed25519",
        r"C:\Users\arsal\.ssh\khazana_ed25519"
    ]
    for alt in alternative_keys:
        if os.path.exists(alt):
            KEY = alt
            break

print(f"Using SSH Key: {KEY}")
SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no"]
SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no"]

# Files and directories to sync
SYNC_ITEMS = [
    "nifty_config.json",
    "data/config.json",
    "data/trades.db",
    "data/trades.db-shm",
    "data/trades.db-wal",
    "data/rsi_v1_watch.json",
    "data/downloader_alert.json",
    "data/downloader_log.json",
    "data/health_report.json",
    "data/sec_id_cache.json",
    "data/shared_ltp_cache.json",
    "data/trade_log.json",
    "data/trade_ohlc",
    "_TRADING_DATA",
    "logs"
]

def run_cmd(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and check:
        print(f"ERROR executing command: {' '.join(cmd)}")
        print(f"STDOUT: {r.stdout}")
        print(f"STDERR: {r.stderr}")
        sys.exit(1)
    return r

def main():
    print(f"\n[1/4] Checking remote files on VPS ({HOST})...")
    # Verify we can connect and the folder exists
    run_cmd(SSH + [HOST, f"ls -d '{REMOTE_DIR}'"])
    
    print("\n[2/4] Packaging data files on VPS into tarball...")
    # Build the list of files that actually exist remote to avoid tar errors
    remote_exists_cmd = (
        f"cd '{REMOTE_DIR}' && "
        "python3 -c \"import os; items = [" + ", ".join(f"'{x}'" for x in SYNC_ITEMS) + "]; "
        "print(' '.join([x for x in items if os.path.exists(x)]))\""
    )
    r = run_cmd(SSH + [HOST, remote_exists_cmd])
    existing_items = r.stdout.strip()
    if not existing_items:
        print("ERROR: No files found to sync on the VPS.")
        sys.exit(1)
        
    print(f"Found files on remote: {existing_items}")
    
    # Create the tarball remotely
    tar_cmd = f"cd '{REMOTE_DIR}' && tar -czf /tmp/vps_sync_data.tgz {existing_items}"
    print("Running remote tar packaging...")
    run_cmd(SSH + [HOST, tar_cmd])
    
    print("\n[3/4] Downloading tarball from VPS to local...")
    local_tgz = os.path.join(tempfile.gettempdir(), "vps_sync_data.tgz")
    if os.path.exists(local_tgz):
        os.unlink(local_tgz)
        
    run_cmd(SCP + [f"{HOST}:/tmp/vps_sync_data.tgz", local_tgz])
    print(f"Downloaded tarball to local: {local_tgz} ({os.path.getsize(local_tgz) // 1024} KB)")
    
    print("\n[4/4] Extracting files locally...")
    # Extract using python tarfile to handle Windows paths cleanly and safely
    with tarfile.open(local_tgz, "r:gz") as tar:
        # Extract all files overwriting existing ones
        tar.extractall(path=LOCAL_DIR)
        
    print("Files successfully extracted and populated locally!")
    
    # Cleanup remote and local tar files
    print("\nCleaning up temporary files...")
    run_cmd(SSH + [HOST, "rm -f /tmp/vps_sync_data.tgz"])
    if os.path.exists(local_tgz):
        os.unlink(local_tgz)
        
    print("\nSync completed successfully! Local host is now populated.")

if __name__ == "__main__":
    main()
