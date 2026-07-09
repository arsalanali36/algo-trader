#!/usr/bin/env python3
r"""
sync_data.py — local <-> VPS trading-data union sync (bidirectional)

Goal: jahan bhi data download karo (local ya VPS), dono jagah aa jaye — baar
baar download na karna pade.

Kaise: per-day CSVs immutable snapshots hain, to ye safe union-merge hai —
  * POPULATED file (real data, > 100 bytes) hamesha EMPTY/missing ko HARAATA hai.
  * Dono populated  -> skip (same day = same data, churn avoid).
  * Dono empty/missing -> skip.
Isse purani empty (header-only) files automatically real data se replace ho jaati
hain jis bhi side pe asli data hai.

Direction:
  python sync_data.py            # dono taraf (pull + push)
  python sync_data.py --pull     # sirf VPS -> local
  python sync_data.py --push     # sirf local -> VPS
  python sync_data.py --dry-run  # plan dikhao, transfer mat karo

Transfer = tar-batched (ek tarball, hazaaron files ke liye fast; per-file scp nahi).
"""

import os
import sys
import subprocess
import tempfile

HOST        = "root@72.61.173.32"
KEY         = os.path.expanduser("~/.ssh/khazana_ed25519")
if not os.path.exists(KEY):
    for alt in [r"C:\Users\arsal\.ssh\khazana_ed25519", r"C:\Users\91933\.ssh\khazana_ed25519"]:
        if os.path.exists(alt):
            KEY = alt
            break
LOCAL_ROOT  = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA"
REMOTE_ROOT = "/root/CODE3B- TV BACKTEST ENGINE/_TRADING_DATA"
POP_BYTES   = 100

SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no"]
SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no"]


def local_index():
    """rel(posix) -> size for every file under LOCAL_ROOT."""
    idx = {}
    for root, _, files in os.walk(LOCAL_ROOT):
        for f in files:
            p = os.path.join(root, f)
            rel = os.path.relpath(p, LOCAL_ROOT).replace("\\", "/")
            try:
                idx[rel] = os.path.getsize(p)
            except OSError:
                pass
    return idx


def remote_index():
    """rel(posix) -> size for every file under REMOTE_ROOT (one ssh call)."""
    cmd = SSH + [HOST, f"cd '{REMOTE_ROOT}' && find . -type f -printf '%s %p\\n'"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("ERROR reading remote index:", r.stderr.strip()); sys.exit(1)
    idx = {}
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        size, _, path = line.partition(" ")
        rel = path[2:] if path.startswith("./") else path
        try:
            idx[rel] = int(size)
        except ValueError:
            pass
    return idx


def plan(li, ri):
    """Return (to_pull, to_push) rel lists using 'populated beats empty'."""
    to_pull, to_push = [], []
    for rel in set(li) | set(ri):
        ls = li.get(rel, -1)
        rs = ri.get(rel, -1)
        lpop = ls > POP_BYTES
        rpop = rs > POP_BYTES
        if rpop and not lpop:
            to_pull.append(rel)          # VPS has real data, local doesn't
        elif lpop and not rpop:
            to_push.append(rel)          # local has real data, VPS doesn't
        # both populated / both empty -> skip
    return sorted(to_pull), sorted(to_push)


def _writelist(rels):
    tf = tempfile.NamedTemporaryFile("w", suffix=".lst", delete=False, newline="\n")
    tf.write("\n".join(rels) + "\n")
    tf.close()
    return tf.name


def do_push(rels, dry):
    if not rels:
        print("  push: nothing"); return
    print(f"  push: {len(rels)} file(s) local -> VPS")
    if dry:
        return
    lst = _writelist(rels)
    tgz = os.path.join(tempfile.gettempdir(), "sync_push.tgz")
    subprocess.run(["tar", "-C", LOCAL_ROOT, "-czf", tgz, "-T", lst], check=True)
    subprocess.run(SCP + [tgz, f"{HOST}:/tmp/sync_push.tgz"], check=True)
    r = subprocess.run(SSH + [HOST, f"tar xzf /tmp/sync_push.tgz -C '{REMOTE_ROOT}/' && echo OK"],
                       capture_output=True, text=True)
    print("   ", r.stdout.strip() or r.stderr.strip())
    os.unlink(lst)


def do_pull(rels, dry):
    if not rels:
        print("  pull: nothing"); return
    print(f"  pull: {len(rels)} file(s) VPS -> local")
    if dry:
        return
    lst = _writelist(rels)
    subprocess.run(SCP + [lst, f"{HOST}:/tmp/sync_pull.lst"], check=True)
    r = subprocess.run(SSH + [HOST, f"cd '{REMOTE_ROOT}' && tar -czf /tmp/sync_pull.tgz -T /tmp/sync_pull.lst && echo OK"],
                       capture_output=True, text=True)
    if "OK" not in r.stdout:
        print("   remote tar failed:", r.stderr.strip()); os.unlink(lst); return
    tgz = os.path.join(tempfile.gettempdir(), "sync_pull.tgz")
    subprocess.run(SCP + [f"{HOST}:/tmp/sync_pull.tgz", tgz], check=True)
    os.makedirs(LOCAL_ROOT, exist_ok=True)
    subprocess.run(["tar", "xzf", tgz, "-C", LOCAL_ROOT], check=True)
    print("    OK")
    os.unlink(lst)


def main():
    dry  = "--dry-run" in sys.argv
    only_pull = "--pull" in sys.argv
    only_push = "--push" in sys.argv
    do_p = not only_push          # pull unless --push only
    do_u = not only_pull          # push unless --pull only

    print(f"Sync trading data  local <-> {HOST}")
    print(f"  local : {LOCAL_ROOT}")
    print(f"  remote: {REMOTE_ROOT}\n")
    li, ri = local_index(), remote_index()
    print(f"  local files: {len(li)} | remote files: {len(ri)}")
    to_pull, to_push = plan(li, ri)
    print(f"  -> pull (VPS->local): {len(to_pull)} | push (local->VPS): {len(to_push)}"
          f"{'   [DRY-RUN]' if dry else ''}\n")
    if do_p:
        do_pull(to_pull, dry)
    if do_u:
        do_push(to_push, dry)
    print("\nDone." if not dry else "\n[dry-run] kuch transfer nahi hua.")


if __name__ == "__main__":
    main()
