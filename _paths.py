"""_paths.py — central sys.path bootstrap for CODE3B.

Every DIRECTLY-RUN entrypoint (dashboard, traders, ops scripts) imports this
early so the project's flat-import source folders are on sys.path. Modules that
are only ever *imported* (risk_gate, order_store, dhan_master, …) don't need to
call this themselves — whoever imports them has already run it.

Design: modules keep their flat names (`import risk_gate`, `import dhan_master`,
`import range_trader`, `import backtest_engine`). This file just adds every
folder those names can live in, so NO import statements change when files move
between _core/_data/_ops/_TRADERS/strategies/live during the refactor.

Usage (root entrypoint):        import _paths            # sets paths on import
Usage (script in a subfolder):  import os, sys
                                sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                                import _paths
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# "" = project root. Order: root first, then flat-module source dirs.
# _TRADERS (the pre-2026-07-09 trader home) dropped 2026-07-16: Phase 4 finished
# long ago and it holds zero .py files now — only pre-refactor logs. Keeping it
# listed implied traders might still live there, which is how "which RSI file is
# real" (TRAP #84) stayed confusing for so long.
_DIRS = ("", "_core", "_data", "_ops", "strategies/live", "_TOOLS")


def setup():
    for sub in _DIRS:
        p = os.path.join(ROOT, sub) if sub else ROOT
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


setup()  # run on import so a bare `import _paths` is enough
