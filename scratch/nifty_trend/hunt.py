"""Parallel-hunt launcher (user request 2026-07-12) — start 2-3 long builds side by
side, from ANY Claude session/terminal, without stepping on each other.

  python hunt.py build_pivot.py                 # launch detached, log to runs/_logs/
  python hunt.py build_bnf.py --trials 300      # extra args pass through
  python hunt.py status                         # = hunt_guard status (who runs what)

The child build registers its own slug via hunt_guard (inside run_hunt.main), so a
duplicate-slug launch refuses cleanly. Kill a hunt ONLY by the pid shown in status —
never `pkill python` (that killed a sibling session's build on 2026-07-10).
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "runs", "_logs")


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("status", "clean"):
        raise SystemExit(subprocess.call(
            [sys.executable, "-X", "utf8", os.path.join(HERE, "hunt_guard.py")] + sys.argv[1:2]))
    script = sys.argv[1]
    path = script if os.path.isabs(script) else os.path.join(HERE, script)
    if not os.path.exists(path):
        raise SystemExit(f"no such build script: {path}")
    os.makedirs(LOGS, exist_ok=True)
    tag = os.path.splitext(os.path.basename(script))[0] + time.strftime("_%m%d_%H%M%S")
    logp = os.path.join(LOGS, tag + ".log")
    logf = open(logp, "w", encoding="utf-8")
    env = dict(os.environ, HUNT_LOG=logp, PYTHONUTF8="1")
    kw = {"env": env}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED + NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([sys.executable, "-X", "utf8", "-u", path] + sys.argv[2:],
                         cwd=HERE, stdout=logf, stderr=subprocess.STDOUT, **kw)
    print(f"launched pid={p.pid}  script={script}")
    print(f"log: {logp}")
    print("status anytime:  python hunt.py status")


if __name__ == "__main__":
    main()
