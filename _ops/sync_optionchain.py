"""Sync the live option-chain collector CSVs from the VPS down to THIS machine, so the
What-If / whatif2 Strategy Builder (and /curves, /gex) can serve RECENT dates locally.

Why: the per-minute collector (`algo-optionchain`) only runs on the VPS, so the recent
window (last ~few weeks, real IV) lives ONLY there. The historical lake (OptChainLake_1m)
IS on this machine, so old dates already work offline — this just fills the recent gap.

Files land at  _TRADING_DATA/OptionChain/<U>/<U>_<date>.csv  — exactly where the reader
(`option_curves._load_rows`) looks. Incremental: a file is pulled only if it's missing
locally or the VPS copy is a different size (today's file keeps growing, so it re-pulls).

Usage:
    python _ops/sync_optionchain.py                    # all underlyings, all collector days
    python _ops/sync_optionchain.py --days 25          # only the last 25 days
    python _ops/sync_optionchain.py --underlyings NIFTY,BANKNIFTY
    python _ops/sync_optionchain.py --key C:/Users/arsal/.ssh/khazana_ed25519

Read-only on the VPS (never writes/deletes there). No extra deps beyond paramiko.
"""
import os
import sys
import argparse
import datetime as _dt

HOST = "72.61.173.32"
USER = "root"
REMOTE_BASE = "/root/ARSALAN/CODE3B- TV BACKTEST ENGINE/_TRADING_DATA/OptionChain"
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_BASE = os.path.join(PROJECT, "_TRADING_DATA", "OptionChain")

_KEY_CANDIDATES = [
    r"C:\Users\arsal\.ssh\khazana_ed25519",
    os.path.expanduser("~/.ssh/khazana_ed25519"),
]


def _find_key(explicit):
    for k in ([explicit] if explicit else []) + _KEY_CANDIDATES:
        if k and os.path.exists(k):
            return k
    return None


def _date_from_name(name):
    """'NIFTY_2026-08-05.csv' -> '2026-08-05' (or None)."""
    base = name.rsplit(".", 1)[0]
    tail = base.split("_")[-1]
    try:
        _dt.datetime.strptime(tail, "%Y-%m-%d")
        return tail
    except Exception:
        return None


def _fmt(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f%s" % (n, unit)
        n /= 1024.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="only files newer than today-N days (0 = all)")
    ap.add_argument("--underlyings", default="", help="comma list, e.g. NIFTY,BANKNIFTY (default: all on the VPS)")
    ap.add_argument("--key", default="", help="path to the ed25519 private key")
    ap.add_argument("--host", default=HOST)
    args = ap.parse_args()

    try:
        import paramiko
    except Exception:
        print("paramiko missing -> pip install paramiko")
        sys.exit(1)

    key = _find_key(args.key)
    if not key:
        print("SSH key not found. Pass --key <path> (tried: %s)" % ", ".join(_KEY_CANDIDATES))
        sys.exit(1)

    want_u = {s.strip().upper() for s in args.underlyings.split(",") if s.strip()}
    cutoff = None
    if args.days > 0:
        cutoff = (_dt.date.today() - _dt.timedelta(days=args.days)).strftime("%Y-%m-%d")

    print("Sync option-chain  VPS %s  ->  %s" % (args.host, LOCAL_BASE))
    print("key: %s" % key)
    if want_u:
        print("underlyings: %s" % ", ".join(sorted(want_u)))
    if cutoff:
        print("only dates >= %s" % cutoff)

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(args.host, username=USER, key_filename=key, look_for_keys=False, allow_agent=False, timeout=30)
    sftp = cli.open_sftp()

    try:
        udirs = sorted(sftp.listdir(REMOTE_BASE))
    except IOError as e:
        print("cannot list remote %s: %s" % (REMOTE_BASE, e))
        cli.close()
        sys.exit(1)

    n_pull = n_skip = 0
    bytes_pull = 0
    for u in udirs:
        if want_u and u.upper() not in want_u:
            continue
        rdir = REMOTE_BASE + "/" + u
        try:
            entries = sftp.listdir_attr(rdir)
        except IOError:
            continue
        ldir = os.path.join(LOCAL_BASE, u)
        os.makedirs(ldir, exist_ok=True)
        pulled_here = []
        for at in entries:
            name = at.filename
            if not name.endswith(".csv"):
                continue
            d = _date_from_name(name)
            if cutoff and d and d < cutoff:
                continue
            lpath = os.path.join(ldir, name)
            # incremental: pull only if missing or a different size (today's file grows)
            if os.path.exists(lpath) and os.path.getsize(lpath) == at.st_size:
                n_skip += 1
                continue
            tmp = lpath + ".part"
            sftp.get(rdir + "/" + name, tmp)
            os.replace(tmp, lpath)
            n_pull += 1
            bytes_pull += at.st_size
            pulled_here.append(name.rsplit("_", 1)[-1].replace(".csv", ""))
        if pulled_here:
            print("  %-10s +%d  (%s)" % (u, len(pulled_here), ", ".join(pulled_here[-6:])
                                         + (" …" if len(pulled_here) > 6 else "")))

    sftp.close()
    cli.close()
    print("\nDone: pulled %d file(s) %s, up-to-date %d." % (n_pull, _fmt(bytes_pull), n_skip))
    if n_pull:
        print("Ab in dates ki chain/payoff localhost pe bhi chalegi (pehli baar us din ka parse, phir cached).")


if __name__ == "__main__":
    main()
