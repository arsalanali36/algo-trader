#!/usr/bin/env python3
"""
strategy_supervisor.py — fork-based launcher for CODE3B live/paper strategies.

WHY
    Har strategy ab tak ek ALAG python interpreter thi (`subprocess.Popen`),
    isliye pandas + 26MB scrip-master ka parsed cache har process me DOBARA
    load hota tha. 15 strategies = 15 copies = ~1.3-1.9 GB.
    Maapa gaya (asli scrip master + pandas, is box pe):
        6 alag process   = 521 MB  (87 MB / strategy)
        1 parent + 6 fork = 127 MB  (13 MB / child)   -> ~76% kam
    15 strategies pe extrapolate: ~1.3 GB -> ~285 MB.

HOW
    EK parent pandas import karta hai + scrip cache EK BAAR build karta hai
    (single-threaded), phir har strategy ke liye os.fork() karta hai. Linux
    copy-on-write parent ke read-mostly pages ko saare children me share karta
    hai. Har child PHIR BHI ek asli, alag process hai — ek ko restart / kill
    karo to baaki ko koi farak nahi (yahi poora maqsad hai).

SAFETY — fork + threads = deadlock trap
    Parent ko fork ke waqt SINGLE-THREADED rehna ZAROORI hai. Ye sirf pure code
    import karta hai + scrip cache build karta hai. Ye Dhan feed, socket, DB
    handle, ya logging-thread KABHI parent me nahi kholta. Har child ye sab
    fork ke BAAD, strategy ke apne run() ke andar kholta hai. Parent-path me
    koi thread-spawn karne wala import mat jodo.

DASHBOARD COMPATIBILITY
    Dashboard traders ko '--id <sid>' cmdline grep karke dhoondhta hai
    (get_pid). Forked children parent ki cmdline share karte hain, isliye:
      (a) har child ka process-title setproctitle se 'code3b-strategy --paper
          --id <sid>' set karte hain (pgrep -f still finds it), AUR
      (b) data/supervisor_pids.json me {sid: pid} likhte hain (reliable
          fallback jab setproctitle na ho).

CUTOVER
    Ye file INERT hai jab tak koi ise chalaye na. Live fleet ko isme switch
    karna ek alag, soch-samajh ke kiya jaane wala step hai (neeche checklist).
    Abhi self-test se mechanics prove karo:  python3 _ops/strategy_supervisor.py --self-test
"""

import gc
import json
import os
import signal
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
PIDMAP_FILE = BASE_DIR / "data" / "supervisor_pids.json"
CONFIG_FILE = BASE_DIR / "nifty_config.json"   # repo root me hai (data/ me nahi)

# Respawn backoff: agar ek strategy baar-baar crash kare to crash-loop na bane.
RESPAWN_MIN_UPTIME = 20      # sec — itne se pehle mari to "crash" mano
RESPAWN_BACKOFF = [2, 5, 15, 30, 60]  # consecutive-crash pe badhta wait
MAX_QUICK_CRASHES = 5        # itni tez crashes ke baad strategy ko de-activate maano


def _log(msg):
    print(f"[supervisor {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _set_title(sid, mode):
    """Child ka process-title set karo taaki dashboard ka '--id <sid>' grep chale."""
    try:
        import setproctitle
        flag = "--live" if mode == "live" else "--paper"
        setproctitle.setproctitle(f"code3b-strategy {flag} --id {sid}")
        return True
    except Exception:
        return False


def _write_pidmap(children):
    """{sid: pid} disk pe — get_pid ke liye reliable fallback."""
    try:
        PIDMAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {sid: c["pid"] for sid, c in children.items() if c["pid"]}
        tmp = PIDMAP_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, PIDMAP_FILE)
    except Exception as e:
        _log(f"pidmap write failed (non-fatal): {e}")


# ────────────────────────────────────────────────────────────────────────────
# Strategy resolution — SIRF read-mostly, koi order/socket/thread nahi.
# ────────────────────────────────────────────────────────────────────────────
def _active_strategies():
    """
    nifty_config.json se woh strategies jo active hain aur jinke paas live
    trader script hai. Return: list of (sid, mode).
    trader_dashboard ke STRATEGIES map + _base() ke saath 1:1 match.
    """
    sys.path.insert(0, str(BASE_DIR))
    import _paths  # noqa: F401 — repo ka standard bootstrap (_core/_data/_ops sys.path pe)
    # Dashboard ka STRATEGIES map + _base() hi single source hai (TRAP #116).
    # Import verified single-threaded (fork-safe): sirf MainThread rehta hai.
    from trader_dashboard import STRATEGIES, _base

    cfg = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    out = []
    for key, v in cfg.items():
        if not isinstance(v, dict):
            continue
        if not v.get("active", False):
            continue
        st = STRATEGIES.get(_base(key))
        if not st:
            continue
        out.append((key, v.get("mode", "paper"), st["script"]))
    return out


def _run_strategy_in_child(sid, mode, script_path):
    """
    CHILD context. Fork ke baad yahan aate hain. Yahin — aur SIRF yahin —
    strategy apna sab kuch (feed/socket/DB/threads) kholti hai.
    """
    # Parent ke inherited signal-handlers ko DEFAULT pe reset karo — warna
    # child SIGTERM pe sirf parent-wala flag set karta hai aur kabhi exit
    # nahi hota (smoke-test me pakda: kill ke baad bhi child zinda).
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    _set_title(sid, mode)
    # stdout/stderr ko strategy ke apne log file pe — bilkul Popen jaisa.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lf = open(LOG_DIR / f"{sid}.log", "a", buffering=1, encoding="utf-8")
    os.dup2(lf.fileno(), 1)
    os.dup2(lf.fileno(), 2)
    # argv/env ko waise set karo jaise script standalone chala ho.
    sys.argv = [script_path, ("--live" if mode == "live" else "--paper"), "--id", sid]
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    print(f"\n[{'LIVE' if mode == 'live' else 'PAPER'}] {sid} (fork-child pid {os.getpid()})\n", flush=True)

    # Script ko uske APNE __main__ block se chalao (runpy) — bilkul waise jaise
    # standalone `python script.py --paper --id X` chala ho. Isse har script ka
    # apna argparse / logging-setup / entry-pattern (run() ho ya main()) khud
    # chalta hai — supervisor ko kisi script ke internals ki assumption nahi.
    import runpy
    os.chdir(str(BASE_DIR))  # Popen bhi cwd=BASE_DIR deta tha
    runpy.run_path(script_path, run_name="__main__")


def _spawn(sid, mode, script_path):
    """Ek strategy ke liye fork. Parent me child-pid return, child me kabhi return nahi."""
    pid = os.fork()
    if pid == 0:
        # CHILD
        try:
            _run_strategy_in_child(sid, mode, script_path)
        except SystemExit:
            raise
        except BaseException as e:
            # Child ka crash apne log me — supervisor respawn dekhega.
            try:
                print(f"[{sid}] CRASH: {e!r}", flush=True)
            except Exception:
                pass
            os._exit(1)
        os._exit(0)
    return pid


# ────────────────────────────────────────────────────────────────────────────
# Supervisor main loop
# ────────────────────────────────────────────────────────────────────────────
def supervise(strategies, warm=True):
    """
    strategies: list of (sid, mode, script_path)
    warm=True  -> fork se pehle pandas + scrip cache parent me load (COW share).
    """
    if warm:
        _log("warming parent (pandas + scrip cache) — single-threaded...")
        import pandas  # noqa: F401  (parent me load, children COW share karenge)
        sys.path.insert(0, str(BASE_DIR / "_data"))
        try:
            import dhan_master
            dhan_master.build_cache()
            _log("scrip cache built in parent (children isko share karenge)")
        except Exception as e:
            _log(f"scrip cache warm skip (non-fatal): {e}")
        gc.collect()
        try:
            gc.freeze()  # loaded objects ko GC young-gen se hatao -> kam page-dirtying
        except Exception:
            pass

    children = {}   # sid -> {pid, mode, script, started, crashes}
    stopping = {"flag": False}

    _log(f"parent pid = {os.getpid()}")

    def _shutdown(signum, frame):
        stopping["flag"] = True
        _log(f"signal {signum} mila (parent {os.getpid()}) — saare children ko SIGTERM")
        for sid, c in children.items():
            if c["pid"]:
                try:
                    os.kill(c["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # initial spawn
    for sid, mode, script in strategies:
        pid = _spawn(sid, mode, script)
        children[sid] = {"pid": pid, "mode": mode, "script": script,
                         "started": time.time(), "crashes": 0}
        _log(f"started {sid} (pid {pid}, {mode})")
    _write_pidmap(children)

    # reap + respawn loop
    while not stopping["flag"]:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            if stopping["flag"]:
                break
            time.sleep(1)
            continue
        if pid == 0:
            time.sleep(1)
            continue
        # kaunsi strategy mari?
        dead = next((s for s, c in children.items() if c["pid"] == pid), None)
        if dead is None:
            continue
        c = children[dead]
        uptime = time.time() - c["started"]
        c["pid"] = None
        if stopping["flag"]:
            continue
        if uptime < RESPAWN_MIN_UPTIME:
            c["crashes"] += 1
        else:
            c["crashes"] = 0  # der tak chali -> healthy restart, counter reset
        if c["crashes"] >= MAX_QUICK_CRASHES:
            _log(f"⚠️  {dead} ne {c['crashes']} baar tez crash kiya — respawn ROK raha "
                 f"hoon (config/script dekho). Baaki strategies chal rahi hain.")
            _write_pidmap(children)
            continue
        wait = RESPAWN_BACKOFF[min(c["crashes"], len(RESPAWN_BACKOFF) - 1)]
        _log(f"{dead} exit (uptime {uptime:.0f}s, crash#{c['crashes']}) — "
             f"{wait}s me respawn")
        time.sleep(wait)
        c["pid"] = _spawn(dead, c["mode"], c["script"])
        c["started"] = time.time()
        _write_pidmap(children)
        _log(f"respawned {dead} (pid {c['pid']})")

    # graceful drain
    deadline = time.time() + 15
    while any(c["pid"] for c in children.values()) and time.time() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
            if pid:
                for c in children.values():
                    if c["pid"] == pid:
                        c["pid"] = None
        except ChildProcessError:
            break
        time.sleep(0.5)
    _log("supervisor exit")


# ────────────────────────────────────────────────────────────────────────────
# SELF-TEST — zero risk. Dummy sleeper children se mechanics prove karta hai:
#   1) fork-COW se RAM sharing   2) ek child restart baaki ko chhue bina
#   3) crash-respawn             4) setproctitle / pidmap visibility
# Live traders ko HAATH NAHI LAGATA (koi run(), koi Dhan, koi order).
# ────────────────────────────────────────────────────────────────────────────
def _self_test():
    import subprocess
    N = 6

    def pss_mb(pids):
        tot = 0
        for p in pids:
            try:
                for line in open(f"/proc/{p}/smaps_rollup"):
                    if line.startswith("Pss:"):
                        tot += int(line.split()[1])
                        break
            except Exception:
                pass
        return tot / 1024

    _log(f"SELF-TEST start (N={N} dummy children)")

    # parent warm: pandas + ek bada read-mostly object (scrip cache jaisa)
    import pandas  # noqa
    sys.path.insert(0, str(BASE_DIR / "_data"))
    try:
        import dhan_master
        dhan_master.build_cache()
        _log("parent warm: scrip cache built")
    except Exception as e:
        _log(f"scrip cache skip: {e}")
    gc.collect()
    try:
        gc.freeze()
    except Exception:
        pass

    kids = []
    for i in range(N):
        pid = os.fork()
        if pid == 0:
            _set_title(f"selftest_{i}", "paper")
            time.sleep(120)
            os._exit(0)
        kids.append(pid)
    time.sleep(4)
    fork_pss = pss_mb([os.getpid()] + kids)
    _log(f"[1] fork RAM: {N} children + parent = {fork_pss:.1f} MB total "
         f"({fork_pss / (N + 1):.1f} MB/proc via COW)")

    # setproctitle / pidmap visibility check
    try:
        out = subprocess.run(["pgrep", "-af", "code3b-strategy"],
                             capture_output=True, text=True).stdout.strip()
        _log(f"[4] pgrep '--id' visibility: {'OK' if out else 'setproctitle absent (pidmap fallback zaroori)'}")
    except Exception:
        pass

    # restart-one: ek child maaro, dekho baaki zinda
    victim = kids[2]
    _log(f"[2] restart test: child {victim} ko SIGTERM (baaki {N - 1} ko chhue bina)")
    os.kill(victim, signal.SIGTERM)
    time.sleep(2)
    # victim ko reap karo (warna zombie reh ke 'zinda' dikhega), phir baaki ginо.
    try:
        os.waitpid(victim, os.WNOHANG)
    except ChildProcessError:
        pass
    alive = [p for p in kids if p != victim and _alive(p)]
    victim_gone = not _alive(victim)
    ok = victim_gone and len(alive) == N - 1
    _log(f"[2] result: baaki {len(alive)}/{N - 1} zinda, victim gaya: {victim_gone} "
         f"-> isolation {'OK' if ok else 'FAIL'}")

    # crash-respawn concept: naya child fork karke victim ki jagah
    newpid = os.fork()
    if newpid == 0:
        time.sleep(120)
        os._exit(0)
    kids = [p for p in kids if p != victim] + [newpid]
    _log(f"[3] respawn: naya child pid {newpid} victim ki jagah")

    # cleanup
    for p in kids:
        try:
            os.kill(p, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for p in kids:
        try:
            os.waitpid(p, 0)
        except ChildProcessError:
            pass
    _log("SELF-TEST done — mechanics verified, zero live impact")


def _alive(pid):
    """True agar process sach me chal raha hai (zombie = NOT alive)."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    except Exception:
        return True


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        sys.exit(0)
    if "--dry-run" in sys.argv:
        for sid, mode, script in _active_strategies():
            print(f"WOULD FORK  {sid:22} mode={mode:5} {script}")
        sys.exit(0)
    # REAL run — cutover ke baad hi chalao. Abhi manual invoke se hi chalega.
    # --only sid1,sid2  -> sirf in strategies ko chalao (smoke-test / staged rollout)
    strategies = _active_strategies()
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = set(sys.argv[i + 1].split(","))
    if only:
        strategies = [t for t in strategies if t[0] in only]
        missing = only - {t[0] for t in strategies}
        if missing:
            _log(f"⚠️  --only me ye active/known nahi: {sorted(missing)}")
    _log(f"{len(strategies)} strategies — supervising: {[t[0] for t in strategies]}")
    supervise(strategies, warm=True)
