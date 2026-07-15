#!/usr/bin/env python3
"""lake_pull_to_pc.py — build the 1-min expired-option lake ON THE VPS (fresh token +
coordinated rate-limiter = safe during live market hours) and MIRROR it to this PC,
one underlying at a time, freeing VPS disk as we go.

WHY: the user wants the full 1-min lake (NIFTY + BANKNIFTY + ~54 liquid stocks) on the
PC as a durable backup (the VPS is being replaced). The VPS only has ~15 GB free (a full
1-min lake ≈ 28 GB), and the PC's local Dhan token is stale — so the download must run on
the VPS but can't all live there at once. This driver stages it: download 1 underlying →
scp to PC → delete from VPS → next.

RESUMABLE + reboot-safe:
  • Per-underlying done-marker on the PC (`<local_lake>/<SYM>/_pulled.ok`) — a re-run skips
    what's already here. Safe to Ctrl-C / PC-reboot / re-run any time.
  • If the VPS download process dies mid-underlying (e.g. VPS reboot), it is relaunched;
    optchain_dl's own manifest resumes that underlying where it left off.

RUN (locally, in the background):
  nohup python _ops/lake_pull_to_pc.py > _TRADING_DATA/lake_pull.log 2>&1 &
Progress:  tail -f _TRADING_DATA/lake_pull.log
"""
import os
import sys
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# --- config -------------------------------------------------------------------
KEY = r"C:\Users\arsal\.ssh\khazana_ed25519"
VPS = "root@72.61.173.32"
VPS_DIR = "/root/ARSALAN/CODE3B- TV BACKTEST ENGINE"
VPS_LAKE = VPS_DIR + "/_TRADING_DATA/OptChainLake_1m"
LOCAL_LAKE = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake_1m")
INTERVAL = "1"
POLL_SECS = 60
LAUNCH_SETTLE = 8

INDEX = ["NIFTY", "BANKNIFTY"]                       # OPTIDX, WEEK+MONTH, ±10, 5yr
STOCKS = [                                           # OPTSTK, MONTH, ±5, 3yr
    "ADANIENT", "ADANIGREEN", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL", "BPCL", "BRITANNIA",
    "CHOLAFIN", "CIPLA", "COALINDIA", "DLF", "DRREDDY", "EICHERMOT", "GRASIM", "HAL",
    "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
    "INDUSINDBK", "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI",
    "NESTLEIND", "NTPC", "ONGC", "PFC", "POWERGRID", "RECLTD", "RELIANCE", "SBILIFE",
    "SBIN", "SHRIRAMFIN", "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN",
    "ULTRACEMCO", "WIPRO",
]

SSH = ["ssh", "-i", KEY, "-o", "StrictHostKeyChecking=no",
       "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=8"]
SCP = ["scp", "-i", KEY, "-o", "StrictHostKeyChecking=no", "-r"]


def log(msg):
    print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)


def _run(cmd, timeout=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def ssh(remote_cmd, timeout=120):
    return _run(SSH + [VPS, remote_cmd], timeout=timeout)


def dl_cmd(sym):
    # quote the symbol — names like M&M would otherwise trip the remote shell (&)
    sel = ("--underlying '%s'" % sym) if sym in INDEX else ("--stocks '%s'" % sym)
    log_f = "/tmp/lake_%s.log" % sym.replace("&", "_")
    return ("cd '%s' && nohup venv/bin/python scratch/nifty_trend/optchain_dl.py "
            "%s --interval %s > %s 2>&1 </dev/null & disown"
            % (VPS_DIR, sel, INTERVAL, log_f)), log_f


def proc_alive(sym):
    pat = "--underlying %s" % sym if sym in INDEX else "--stocks %s" % sym
    rc, out = ssh("pgrep -af optchain_dl | grep -F -- '%s' | grep -v grep | head -1" % pat)
    return bool(out.strip())


def is_done(sym, log_f):
    rc, out = ssh("grep -c -- 'DONE' '%s' 2>/dev/null || echo 0" % log_f)
    try:
        return int((out.strip().splitlines() or ["0"])[-1]) > 0
    except Exception:
        return False


def download_one(sym):
    """Launch + supervise the VPS download of one underlying until DONE."""
    cmd, log_f = dl_cmd(sym)
    launches = 0
    while True:
        if is_done(sym, log_f):
            return True
        if not proc_alive(sym):
            if launches >= 6:
                log("  %s: gave up after %d relaunches" % (sym, launches))
                return is_done(sym, log_f)
            launches += 1
            log("  %s: launching VPS download (attempt %d)" % (sym, launches))
            ssh(cmd, timeout=60)
            time.sleep(LAUNCH_SETTLE)
        time.sleep(POLL_SECS)


def pull_one(sym):
    """scp the underlying folder to the PC, verify, then delete from the VPS."""
    local_dir = os.path.join(LOCAL_LAKE, sym)
    os.makedirs(LOCAL_LAKE, exist_ok=True)
    rc, out = _run(SCP + ["%s:'%s/%s'" % (VPS, VPS_LAKE, sym), LOCAL_LAKE + os.sep],
                   timeout=1800)
    if rc != 0:
        log("  %s: scp FAILED rc=%s %s" % (sym, rc, out[-200:]))
        return False
    # verify: at least one .csv landed
    got = 0
    for _dp, _dn, fns in os.walk(local_dir):
        got += sum(1 for f in fns if f.endswith(".csv"))
    if got == 0:
        log("  %s: pulled but 0 csv files — leaving VPS copy, will retry" % sym)
        return False
    # free VPS space
    ssh("rm -rf '%s/%s'" % (VPS_LAKE, sym))
    open(os.path.join(local_dir, "_pulled.ok"), "w").write(time.strftime("%Y-%m-%d %H:%M:%S"))
    log("  %s: pulled %d csv files -> PC, cleared from VPS" % (sym, got))
    return True


def main():
    order = INDEX + STOCKS
    log("lake_pull_to_pc: %d underlyings (interval=%smin) -> %s" % (len(order), INTERVAL, LOCAL_LAKE))
    done = skipped = failed = 0
    for i, sym in enumerate(order):
        marker = os.path.join(LOCAL_LAKE, sym, "_pulled.ok")
        if os.path.exists(marker):
            skipped += 1
            continue
        log("[%d/%d] %s — downloading on VPS..." % (i + 1, len(order), sym))
        if not download_one(sym):
            log("[%d/%d] %s — download incomplete, skipping for now" % (i + 1, len(order), sym))
            failed += 1
            continue
        if pull_one(sym):
            done += 1
        else:
            failed += 1
    log("ALL DONE. pulled=%d already-had=%d failed=%d" % (done, skipped, failed))


if __name__ == "__main__":
    main()
