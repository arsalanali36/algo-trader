"""exit_time_config() returns (H, M) TUPLES — every live loop must parse it via
risk_gate.hm_tuple(), never `str(x).split(":")` (2026-09-02: that pattern threw
ValueError inside `except: pass` in 3 live loops → expiry squareoff + no-entry gate
silently dead; 02.17 state never closed → next cycle's entry skipped).

Run:  python -c "import _paths, runpy; runpy.run_path('_DEV/tests/test_exit_time_hm.py', run_name='__main__')"
"""
import os, re, sys, glob
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
import _paths  # noqa
import risk_gate as rg

fails = 0
def check(name, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails += 1

# 1. helper accepts both shapes
check("hm_tuple((15,10)) -> (15,10)", rg.hm_tuple((15, 10)) == (15, 10))
check("hm_tuple([9,20]) -> (9,20)", rg.hm_tuple([9, 20]) == (9, 20))
check("hm_tuple('15:10') -> (15,10)", rg.hm_tuple("15:10") == (15, 10))
sq, ne = rg.exit_time_config()
check("exit_time_config returns tuples", isinstance(sq, tuple) and isinstance(ne, tuple))
check("hm_tuple(exit_time_config()[0]) is a valid (H,M)", 0 <= rg.hm_tuple(sq)[0] <= 23)

# 2. the three live loops' gates actually compute (not swallowed)
import importlib
for mod, fn in (("weekly_ironfly_live", "_no_entry_now"),
                ("strangle_live", "_no_entry_now"),
                ("m_pattern_ironfly_live", "_no_entry_now"),
                ("m_pattern_ironfly_live", "_sq_time_reached")):
    try:
        m = importlib.import_module(mod)
    except Exception as e:
        check(f"{mod} import ({e})", False); continue
    orig = m.rg.exit_time_config
    try:
        m.rg.exit_time_config = lambda: ((0, 0), (0, 0))      # cutoff at midnight → always past
        past = getattr(m, fn)()
        m.rg.exit_time_config = lambda: ((23, 59), (23, 59))  # cutoff end-of-day → never past
        future = getattr(m, fn)()
    finally:
        m.rg.exit_time_config = orig
    check(f"{mod}.{fn}: past-cutoff -> True", past is True)
    check(f"{mod}.{fn}: future-cutoff -> False", future is False)

# 3. static guard: the broken pattern must not come back anywhere in live code
bad = re.compile(r'str\(\s*(sqh|no_entry|_sq|squareoff|sq)\s*\)\.split\(\s*":"')
hits = []
for pat in ("_ops/*.py", "strategies/live/*.py", "_core/*.py", "trader_dashboard.py", "monitor_daemon.py"):
    for f in glob.glob(os.path.join(ROOT, pat)):
        try:
            src = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if bad.search(line):
                hits.append(f"{os.path.relpath(f, ROOT)}:{i}")
check("no `str(<exit-time>).split(':')` left in live code" + (f" — {hits}" if hits else ""), not hits)

print("\nRESULT:", "ALL PASS" if fails == 0 else f"{fails} FAIL")
sys.exit(1 if fails else 0)
