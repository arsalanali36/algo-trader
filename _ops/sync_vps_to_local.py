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
LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
# BatchMode=yes + ConnectTimeout: kabhi interactive prompt (host-key/passphrase) pe
# hang na ho — warna dashboard ka 180s route timeout ho jaata hai.
_SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12"]
SSH = ["ssh", "-i", KEY] + _SSH_OPTS
SCP = ["scp", "-i", KEY] + _SSH_OPTS

# FAST set (default, button ise pull karta) — "aaj ka data dikhne" ke liye jitna
# chahiye utna hi: trades DB + config + light JSONs. Seconds me aa jaata hai.
FAST_ITEMS = [
    "nifty_config.json",
    "data/config.json",
    "data/trades.db",   # -wal/-shm JAANBOOJH KE nahi: volatile SQLite files, local
                        # dashboard unhe open/lock rakhta (Windows overwrite fail).
                        # VPS pe pehle WAL checkpoint hota hai (neeche) → .db complete.
    "data/rsi_v1_watch.json",
    "data/downloader_alert.json",
    "data/downloader_log.json",
    "data/health_report.json",
    "data/sec_id_cache.json",
    "data/trade_log.json",
    "data/saved_backtests.json",
    "data/saved_optimizations.json",
]

# FULL set (--full flag) — sab kuch, incl. bhaari items (premium-chart OHLC,
# poora F&O data-lake, logs). Ye GBs ho sakta hai → kabhi-kabhi hi, button se nahi.
HEAVY_ITEMS = [
    "data/shared_ltp_cache.json",
    "data/trade_ohlc",
    "data/backtest.db",
    "_TRADING_DATA",
    "logs",
]
SYNC_ITEMS = FAST_ITEMS + HEAVY_ITEMS   # back-compat (full)

def run_cmd(cmd, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 and check:
        print(f"ERROR executing command: {' '.join(cmd)}")
        print(f"STDOUT: {r.stdout}")
        print(f"STDERR: {r.stderr}")
        sys.exit(1)
    return r

def main(items=None):
    items = items or FAST_ITEMS
    print(f"\n[1/4] Checking remote files on VPS ({HOST})...")
    # Verify we can connect and the folder exists
    run_cmd(SSH + [HOST, f"ls -d '{REMOTE_DIR}'"])

    # WAL checkpoint: recent committed trades -wal me hote hain; sirf trades.db
    # copy karne se woh chhut jaate. Checkpoint(TRUNCATE) -wal ko main .db me
    # merge kar deta (safe, non-destructive, live dashboard ko disturb nahi karta).
    if "data/trades.db" in items:
        ckpt = (f"cd '{REMOTE_DIR}' && python3 -c \""
                "import sqlite3; c=sqlite3.connect('data/trades.db'); "
                "c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.commit(); c.close(); "
                "print('wal checkpoint ok')\"")
        run_cmd(SSH + [HOST, ckpt], check=False)

    print(f"\n[2/4] Packaging {len(items)} data items on VPS into tarball...")
    # Build the list of files that actually exist remote to avoid tar errors
    remote_exists_cmd = (
        f"cd '{REMOTE_DIR}' && "
        "python3 -c \"import os; items = [" + ", ".join(f"'{x}'" for x in items) + "]; "
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
    # default = FAST (trades + config, seconds). --full = sab kuch (data-lake/logs, bhaari).
    _full = "--full" in sys.argv
    main(SYNC_ITEMS if _full else FAST_ITEMS)
