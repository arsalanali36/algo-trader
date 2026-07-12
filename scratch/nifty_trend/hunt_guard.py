"""Multi-session hunt guard (user request 2026-07-12) — 2-3 hunts / Claude sessions
running the SAME pipeline at the same time must not corrupt shared files or kill
each other's builds (it happened 2026-07-10: two sessions edited the same files and
killed each other's processes).

Three primitives:
  flock(name)        cross-process lock (atomic mkdir; stale-broken if owner pid dead)
  register(slug)     one ACTIVE hunt per slug — a second session starting the same
                     slug gets a clear refusal instead of silently clobbering runs/<slug>/
  active()           registry of live hunts {slug,pid,script,started} — check BEFORE
                     killing any python process; kill ONLY a pid listed here, never blanket.
Shared-file writes (runs/index.json, runs/compare.json) go through flock("runs_index")/
flock("compare") — wired inside run_hunt.py so every build_*.py inherits it for free.

CLI:  python hunt_guard.py status   # who is running what (+ last log lines)
      python hunt_guard.py clean    # prune dead entries / stale locks
"""
import json
import os
import sys
import time
from contextlib import contextmanager

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
LOCKS = os.path.join(RUNS, "_locks")
REG = os.path.join(RUNS, "_active_hunts.json")
LOGS = os.path.join(RUNS, "_logs")
STALE_S = 180.0          # a lock older than this whose owner is dead gets broken


def _pid_alive(pid):
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        pass
    if os.name == "nt":                      # never os.kill() on Windows (it TERMINATES)
        try:
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        except Exception:
            return True                      # unknown -> assume alive (safe side)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _clear_lock(path, pidf):
    """Windows-safe lock removal: another process may briefly hold the pid file
    open (reading it) -> os.remove raises PermissionError. Retry, never leak —
    a silently-leaked lock self-deadlocks its own owner on the next acquire."""
    for _ in range(40):
        try:
            if os.path.exists(pidf):
                os.remove(pidf)
            os.rmdir(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.05)
    return False


@contextmanager
def flock(name, timeout=120.0):
    """Cross-process lock via atomic mkdir. Owner pid recorded; a stale lock
    (owner dead, or leaked by THIS pid) is broken automatically."""
    os.makedirs(LOCKS, exist_ok=True)
    path = os.path.join(LOCKS, name + ".lck")
    pidf = os.path.join(path, "pid")
    t0 = time.time()
    while True:
        try:
            os.mkdir(path)
            with open(pidf, "w") as f:
                f.write(str(os.getpid()))
            break
        except FileExistsError:
            try:
                owner = int(open(pidf).read().strip())
            except Exception:
                owner = None
            try:
                age = time.time() - os.path.getmtime(path)
            except OSError:
                continue                       # lock vanished between checks — retry
            if owner == os.getpid() or \
               (owner is not None and not _pid_alive(owner)) or \
               (owner is None and age > STALE_S):
                _clear_lock(path, pidf)        # dead owner / own leaked lock
                continue
            if time.time() - t0 > timeout:
                raise TimeoutError(f"lock '{name}' held by pid {owner} for {age:.0f}s")
            time.sleep(0.25)
    try:
        yield
    finally:
        _clear_lock(path, pidf)


def _load_reg():
    try:
        return json.load(open(REG))
    except Exception:
        return []


def _save_reg(rows):
    os.makedirs(RUNS, exist_ok=True)
    json.dump(rows, open(REG, "w"), indent=2)


def active(prune=True):
    """live hunts; dead-pid rows pruned (and persisted) by default."""
    with flock("registry"):
        rows = _load_reg()
        live = [r for r in rows if _pid_alive(r.get("pid", -1))]
        if prune and len(live) != len(rows):
            _save_reg(live)
    return live


def register(slug, script=""):
    """claim a slug for THIS process. Refuses (SystemExit) if another LIVE process
    already runs the same slug — that is a second session's build; do not clobber it."""
    with flock("registry"):
        rows = [r for r in _load_reg() if _pid_alive(r.get("pid", -1))]
        for r in rows:
            if r["slug"] == slug and r["pid"] != os.getpid():
                raise SystemExit(
                    f"[hunt_guard] REFUSED: slug '{slug}' is already being built by "
                    f"pid {r['pid']} ({r.get('script','?')}, started {r.get('started','?')}). "
                    f"Another Claude session / terminal owns it. Pick a different --name, "
                    f"or check: python hunt_guard.py status")
        rows = [r for r in rows if r["slug"] != slug]
        rows.append(dict(slug=slug, pid=os.getpid(), script=script or sys.argv[0],
                         started=time.strftime("%Y-%m-%d %H:%M:%S"),
                         log=os.environ.get("HUNT_LOG", "")))
        _save_reg(rows)


def deregister(slug):
    with flock("registry"):
        rows = [r for r in _load_reg()
                if not (r["slug"] == slug and r["pid"] == os.getpid())]
        _save_reg(rows)


def _cli():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "clean":
        live = active(prune=True)
        # break stale locks too
        if os.path.isdir(LOCKS):
            for n in os.listdir(LOCKS):
                p = os.path.join(LOCKS, n)
                try:
                    owner = int(open(os.path.join(p, "pid")).read().strip())
                except Exception:
                    owner = None
                if owner is None or not _pid_alive(owner):
                    try:
                        pf = os.path.join(p, "pid")
                        if os.path.exists(pf):
                            os.remove(pf)
                        os.rmdir(p)
                        print("broke stale lock:", n)
                    except OSError:
                        pass
        print(f"registry pruned -> {len(live)} live hunt(s)")
        return
    live = active(prune=False)
    if not live:
        print("no active hunts")
        return
    print(f"{len(live)} active hunt(s):")
    for r in live:
        alive = _pid_alive(r.get("pid", -1))
        print(f"  slug={r['slug']:24s} pid={r['pid']:<8} {'LIVE' if alive else 'DEAD'} "
              f"script={r.get('script','?')} started={r.get('started','?')}")
        logp = r.get("log") or os.path.join(LOGS, r["slug"] + ".log")
        if os.path.exists(logp):
            try:
                lines = open(logp, encoding="utf-8", errors="replace").read().splitlines()
                for ln in lines[-3:]:
                    print("      |", ln[:110])
            except Exception:
                pass


if __name__ == "__main__":
    _cli()
