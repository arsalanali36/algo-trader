#!/usr/bin/env python3
"""
strategy_supervisor.py — fork-based launcher for CODE3B live/paper strategies.

WHY
    Har strategy ab tak ek ALAG python interpreter thi (`subprocess.Popen`),
    isliye pandas + 26MB scrip-master ka parsed cache har process me DOBARA
    load hota tha. 15 strategies = 15 copies = ~1.9 GB.
    Maapa gaya (asli scrip master + pandas, is box pe):
        6 alag process   = 521 MB  (87 MB / strategy)
        1 parent + 6 fork = 127 MB  (13 MB / child)   -> ~76% kam

HOW
    EK parent pandas import karta hai + scrip cache EK BAAR build karta hai
    (single-threaded), phir har strategy ke liye os.fork() karta hai. Linux
    copy-on-write parent ke read-mostly pages ko saare children me share karta
    hai. Har child PHIR BHI ek asli, alag process hai — ek ko restart / kill
    karo to baaki ko koi farak nahi (yahi poora maqsad hai).

ARCHITECTURE (cutover 2026-07-21)
    daemon (systemd `algo-supervisor`) <—— data/supervisor_desired.json ——
    trader_dashboard api_start/api_stop (jab data/supervisor_mode.flag maujood ho).

    * Dashboard hi single source hai: api_start desired-entry me sid+mode+script
      likhta hai (script woh apne STRATEGIES map se resolve karta hai). Daemon
      trader_dashboard KABHI import nahi karta — parent minimal + single-threaded
      rehta hai (fork + threads = deadlock trap).
    * PARITY RULE: crash/exit pe auto-respawn NAHI (legacy Popen bhi nahi karta
      tha). Cutover SIRF spawn-mechanism badalta hai, behavior nahi.
      (RESPAWN_ON_CRASH flag rakha hai — future me deliberate enable ho sakta.)
    * FAIL-SAFE: agar daemon zinda nahi (pidfile check) to api_start LEGACY
      Popen pe gir jaata hai + loud log — "supervisor mara to kal subah
      strategies start hi nahi hui" wala failure mode exist nahi karta.
    * DAILY RE-WARM: naye IST din ki pehli fork se pehle scrip master
      re-download + cache rebuild — warna hafte-purana COW cache children ko
      STALE expiries deta (money-path bug, pehle hi block kiya).

SAFETY — fork + threads = deadlock trap
    Parent me sirf: stdlib + pandas + dhan_master. Koi feed/socket/DB/thread
    nahi. Har child apna sab kuch fork ke BAAD kholta hai (runpy se script ka
    apna __main__). Parent-path me thread-spawn karne wala import mat jodo.

DASHBOARD COMPATIBILITY
    Children ka process-title: 'code3b-strategy --paper --id <sid>'
    (setproctitle) -> dashboard get_pid() ka pgrep/psutil dono match karte hain
    (psutil ke liye _proc_cmdline me single-token split fix bhi gaya hai).
    data/supervisor_pids.json = {sid: pid} reliable fallback.

USAGE
    python3 _ops/strategy_supervisor.py --daemon      # systemd service (real)
    python3 _ops/strategy_supervisor.py --self-test   # dummy children, 0 risk
    python3 _ops/strategy_supervisor.py --dry-run     # kya fork hoga, list
    python3 _ops/strategy_supervisor.py --only a,b    # manual staged run
"""

import gc
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
PIDMAP_FILE = DATA_DIR / "supervisor_pids.json"
DESIRED_FILE = DATA_DIR / "supervisor_desired.json"
DAEMON_PIDFILE = DATA_DIR / "supervisor_daemon.pid"
CONFIG_FILE = BASE_DIR / "nifty_config.json"   # repo root me hai (data/ me nahi)

# PARITY: legacy Popen crash pe respawn nahi karta tha — hum bhi nahi.
# (Deliberate future enable ke liye flag; tab RESPAWN_BACKOFF/MAX_QUICK_CRASHES
#  wapas kaam aayenge.)
RESPAWN_ON_CRASH = False
KILL_GRACE_SECS = 10          # desired=stopped -> SIGTERM, itne baad SIGKILL
POLL_SECS = 2                 # desired-file mtime poll


def ist_now():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)


def _log(msg):
    print(f"[supervisor {ist_now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _set_title(sid, mode):
    """Child ka process-title set karo taaki dashboard ka '--id <sid>' grep chale."""
    try:
        import setproctitle
        flag = "--live" if mode == "live" else "--paper"
        setproctitle.setproctitle(f"code3b-strategy {flag} --id {sid}")
        return True
    except Exception:
        return False


def _atomic_write(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    os.replace(tmp, path)


def _write_pidmap(children):
    """{sid: pid} disk pe — get_pid ke liye reliable fallback."""
    try:
        PIDMAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(PIDMAP_FILE, {sid: c["pid"] for sid, c in children.items() if c["pid"]})
    except Exception as e:
        _log(f"pidmap write failed (non-fatal): {e}")


def _read_desired():
    try:
        if DESIRED_FILE.exists():
            d = json.loads(DESIRED_FILE.read_text())
            return d if isinstance(d, dict) else {}
    except Exception as e:
        _log(f"desired read failed: {e}")
    return {}


def _legacy_pid(sid, own_children_pids):
    """Kya is sid ka koi PURANA (legacy Popen / doosra) process chal raha hai?
    Duplicate fork rokne ke liye. Apne children exclude."""
    try:
        import subprocess as sp
        out = sp.check_output(["pgrep", "-a", "-f", "--", f"--id {sid}"], text=True).strip()
        for line in out.splitlines():
            parts = line.split(maxsplit=1)
            pid = int(parts[0])
            cl = parts[1] if len(parts) > 1 else ""
            if pid in own_children_pids or pid == os.getpid():
                continue
            # exact-token check: 'rsi_v1' pattern 'rsi_v1_PAPER' ko bhi substring-match
            # karta hai — toks se confirm karo.
            toks = cl.split()
            for i, t in enumerate(toks):
                if t == "--id" and i + 1 < len(toks) and toks[i + 1] == sid:
                    return pid
    except Exception:
        pass
    return None


# ────────────────────────────────────────────────────────────────────────────
# Parent warm (COW payload) + daily re-warm
# ────────────────────────────────────────────────────────────────────────────
def _warm_parent():
    """pandas + scrip cache parent me load — children COW se share karenge.
    SIRF yahi 2 heavy imports; aur kuch nahi (fork-safety)."""
    import pandas  # noqa: F401
    sys.path.insert(0, str(BASE_DIR / "_data"))
    try:
        import dhan_master
        try:
            dhan_master.download_master_if_needed()
        except Exception as e:
            _log(f"master download skip (cache purana chalega): {e}")
        dhan_master.build_cache()
        _log("scrip cache built in parent (children isko share karenge)")
    except Exception as e:
        _log(f"scrip cache warm skip (non-fatal — children khud build kar lenge): {e}")
    gc.collect()
    try:
        gc.freeze()  # loaded objects GC young-gen se bahar -> kam page-dirtying
    except Exception:
        pass


def _rewarm_if_new_day(state):
    """Naye IST din ki pehli fork se PEHLE cache rebuild — STALE expiries children
    me COW ho jaane wala money-path bug isi se block hota hai."""
    today = ist_now().date().isoformat()
    if state.get("cache_day") == today:
        return
    _log(f"naya din {today} — scrip master re-warm...")
    try:
        import dhan_master
        try:
            dhan_master.download_master_if_needed()
        except Exception as e:
            _log(f"master re-download fail (kal ka use hoga): {e}")
        dhan_master.build_cache()
        gc.collect()
        try:
            gc.freeze()
        except Exception:
            pass
        _log("re-warm done")
    except Exception as e:
        _log(f"re-warm FAIL (children khud build karenge — RAM saving us din kam): {e}")
    state["cache_day"] = today


# ────────────────────────────────────────────────────────────────────────────
# Child
# ────────────────────────────────────────────────────────────────────────────
def _run_strategy_in_child(sid, mode, script_path):
    """
    CHILD context. Fork ke baad yahan aate hain. Yahin — aur SIRF yahin —
    strategy apna sab kuch (feed/socket/DB/threads) kholti hai.
    """
    # Parent ke inherited signal-handlers ko DEFAULT pe reset karo — warna
    # child SIGTERM pe sirf parent-wala flag set karta hai aur kabhi exit
    # nahi hota (smoke-test me pakda).
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
    """Ek strategy ke liye fork. Parent me child-pid return, child kabhi return nahi."""
    pid = os.fork()
    if pid == 0:
        try:
            _run_strategy_in_child(sid, mode, script_path)
        except SystemExit:
            raise
        except BaseException as e:
            try:
                print(f"[{sid}] CRASH: {e!r}", flush=True)
            except Exception:
                pass
            os._exit(1)
        os._exit(0)
    return pid


def _script_ok(script_path):
    """Sanity: script maujood ho aur strategies/live ke andar ho (desired-file
    tamper hone pe bhi daemon manmaani file execute nahi karega)."""
    try:
        p = Path(script_path).resolve()
        return p.is_file() and (BASE_DIR / "strategies" / "live") in p.parents
    except Exception:
        return False


# ────────────────────────────────────────────────────────────────────────────
# DAEMON — desired-state reconciler (systemd `algo-supervisor`)
# ────────────────────────────────────────────────────────────────────────────
def daemon_loop():
    _log(f"daemon start, parent pid = {os.getpid()}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_PIDFILE.write_text(str(os.getpid()))
    _warm_parent()
    state = {"cache_day": ist_now().date().isoformat()}

    children = {}       # sid -> {pid, mode, script, started}
    kill_pending = {}   # sid -> SIGKILL deadline (graceful stop escalation)
    stopping = {"flag": False}
    last_mtime = 0.0

    def _shutdown(signum, frame):
        stopping["flag"] = True
        _log(f"signal {signum} (parent {os.getpid()}) — saare children ko SIGTERM")
        for sid, c in children.items():
            if c["pid"]:
                try:
                    os.kill(c["pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while not stopping["flag"]:
        # 1) reap dead children (PARITY: no respawn — legacy bhi nahi karta tha)
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                break
            dead = next((s for s, c in children.items() if c["pid"] == pid), None)
            if dead:
                up = time.time() - children[dead]["started"]
                _log(f"{dead} exit (pid {pid}, uptime {up:.0f}s)"
                     + ("" if dead in kill_pending else " — respawn OFF (parity), "
                        "agla /api/start hi dobara chalayega"))
                children[dead]["pid"] = None
                kill_pending.pop(dead, None)
                _write_pidmap(children)

        # 2) graceful-stop escalation
        now = time.time()
        for sid, deadline in list(kill_pending.items()):
            c = children.get(sid)
            if not c or not c["pid"]:
                kill_pending.pop(sid, None)
                continue
            if now >= deadline:
                _log(f"{sid} ne SIGTERM ignore kiya {KILL_GRACE_SECS}s — SIGKILL")
                try:
                    os.kill(c["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
                kill_pending.pop(sid, None)

        # 3) desired-file change? -> reconcile
        try:
            mtime = DESIRED_FILE.stat().st_mtime if DESIRED_FILE.exists() else 0.0
        except Exception:
            mtime = 0.0
        if mtime != last_mtime:
            last_mtime = mtime
            desired = _read_desired()
            own_pids = {c["pid"] for c in children.values() if c["pid"]}

            for sid, want in desired.items():
                if not isinstance(want, dict):
                    continue
                d = want.get("desired")
                c = children.get(sid)
                running = bool(c and c["pid"])

                if d == "running" and not running:
                    script = want.get("script", "")
                    mode = want.get("mode", "paper")
                    if not _script_ok(script):
                        _log(f"⚠️  {sid}: script sanity FAIL ({script!r}) — fork refuse")
                        continue
                    lp = _legacy_pid(sid, own_pids)
                    if lp:
                        _log(f"{sid}: legacy process pid {lp} already chal raha — "
                             f"duplicate fork nahi (woh khud exit hoga to agla start yahan aayega)")
                        continue
                    _rewarm_if_new_day(state)
                    pid = _spawn(sid, mode, script)
                    children[sid] = {"pid": pid, "mode": mode, "script": script,
                                     "started": time.time()}
                    own_pids.add(pid)
                    _log(f"started {sid} (pid {pid}, {mode})")
                    _write_pidmap(children)

                elif d == "stopped" and running:
                    _log(f"{sid}: desired=stopped — SIGTERM (pid {c['pid']})")
                    try:
                        os.kill(c["pid"], signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    kill_pending[sid] = time.time() + KILL_GRACE_SECS

        time.sleep(POLL_SECS)

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
    try:
        DAEMON_PIDFILE.unlink()
    except Exception:
        pass
    _log("daemon exit")


def daemon_alive():
    """Kya daemon chal raha hai? (dashboard fail-safe isko use karta hai)"""
    try:
        pid = int(DAEMON_PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return pid
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────────────────
# Manual / inspection modes
# ────────────────────────────────────────────────────────────────────────────
def _active_strategies():
    """--dry-run/--only ke liye: active strategies enumerate (dashboard import
    ALAG throwaway subprocess me hota to aur saaf hota, par ye modes daemon
    nahi hain — inka process fork ke liye reuse nahi hota, isliye theek hai)."""
    sys.path.insert(0, str(BASE_DIR))
    import _paths  # noqa: F401 — repo ka standard bootstrap (_core/_data/_ops sys.path pe)
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


def supervise_manual(strategies):
    """--only ka simple runner (staged rollout / smoke tests). Respawn nahi."""
    _warm_parent()
    _log(f"parent pid = {os.getpid()}")
    children = {}
    stopping = {"flag": False}

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

    for sid, mode, script in strategies:
        pid = _spawn(sid, mode, script)
        children[sid] = {"pid": pid, "mode": mode, "script": script, "started": time.time()}
        _log(f"started {sid} (pid {pid}, {mode})")
    _write_pidmap(children)

    while not stopping["flag"] and any(c["pid"] for c in children.values()):
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid:
            dead = next((s for s, c in children.items() if c["pid"] == pid), None)
            if dead:
                children[dead]["pid"] = None
                _log(f"{dead} exit")
        time.sleep(1)
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
# SELF-TEST — zero risk. Dummy sleeper children se mechanics prove karta hai.
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
    _warm_parent()

    kids = []
    for i in range(N):
        pid = os.fork()
        if pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            _set_title(f"selftest_{i}", "paper")
            time.sleep(120)
            os._exit(0)
        kids.append(pid)
    time.sleep(4)
    fork_pss = pss_mb([os.getpid()] + kids)
    _log(f"[1] fork RAM: {N} children + parent = {fork_pss:.1f} MB total "
         f"({fork_pss / (N + 1):.1f} MB/proc via COW)")

    try:
        out = subprocess.run(["pgrep", "-af", "code3b-strategy"],
                             capture_output=True, text=True).stdout.strip()
        _log(f"[4] pgrep '--id' visibility: {'OK' if out else 'setproctitle absent (pidmap fallback zaroori)'}")
    except Exception:
        pass

    victim = kids[2]
    _log(f"[2] restart test: child {victim} ko SIGTERM (baaki {N - 1} ko chhue bina)")
    os.kill(victim, signal.SIGTERM)
    time.sleep(2)
    try:
        os.waitpid(victim, os.WNOHANG)
    except ChildProcessError:
        pass
    alive = [p for p in kids if p != victim and _alive(p)]
    victim_gone = not _alive(victim)
    ok = victim_gone and len(alive) == N - 1
    _log(f"[2] result: baaki {len(alive)}/{N - 1} zinda, victim gaya: {victim_gone} "
         f"-> isolation {'OK' if ok else 'FAIL'}")

    newpid = os.fork()
    if newpid == 0:
        time.sleep(120)
        os._exit(0)
    kids = [p for p in kids if p != victim] + [newpid]
    _log(f"[3] respawn: naya child pid {newpid} victim ki jagah")

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
    if "--daemon" in sys.argv:
        daemon_loop()
        sys.exit(0)
    # --only sid1,sid2 -> manual staged run (smoke tests)
    only = None
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = set(sys.argv[i + 1].split(","))
    strategies = _active_strategies()
    if only:
        strategies = [t for t in strategies if t[0] in only]
        missing = only - {t[0] for t in strategies}
        if missing:
            _log(f"⚠️  --only me ye active/known nahi: {sorted(missing)}")
    _log(f"{len(strategies)} strategies — supervising: {[t[0] for t in strategies]}")
    supervise_manual(strategies)
