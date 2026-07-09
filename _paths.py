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

# "" = project root. Order: root first, then flat-module source dirs. Both the
# legacy (_TRADERS) and the post-refactor (strategies/live) trader homes are
# listed so this works before AND after Phase 4 — non-existent dirs are skipped.
_DIRS = ("", "_core", "_data", "_ops", "_TRADERS", "strategies/live", "_TOOLS")


def setup():
    for sub in _DIRS:
        p = os.path.join(ROOT, sub) if sub else ROOT
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)


setup()  # run on import so a bare `import _paths` is enough
