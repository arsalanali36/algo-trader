"""A failed daily fetch must not poison the cache for the rest of the day.

2026-07-17: a transient 401/DH-902 burst at 09:15:36 (every strategy waking at
once) made range_v1 log "NIFTY levels: 0 key levels loaded". The endpoint was
healthy seconds later, but 0 stayed cached until midnight — no zones, no signals,
and it looked perfectly healthy the entire time.
"""
import sys

fails = []


def check(name, got, want):
    ok = got == want
    print(('  PASS  ' if ok else '  FAIL  ') + name)
    if not ok:
        print('          got=%r want=%r' % (got, want))
        fails.append(name)


def loop(daily_levels, symbol, fetch_ok, old_code):
    """One iteration of range_trader's level-build block."""
    if old_code:
        gate = symbol not in daily_levels           # KEY presence — the bug
    else:
        gate = not daily_levels.get(symbol)         # missing OR empty — the fix
    if not gate:
        return False                                 # skipped, no fetch
    levels = ['P', 'R1', 'S1'] if fetch_ok else []   # build_key_levels([]) -> []
    daily_levels[symbol] = levels
    if not levels and not old_code:
        daily_levels.pop(symbol, None)               # don't cache the failure
    return True                                      # fetched


print('')
print('=== PURANA code — aaj ka asli scenario ===')
d = {}
loop(d, 'NIFTY', fetch_ok=False, old_code=True)      # 09:15:36 — DH-902 burst
print('  09:15  fetch fail -> daily_levels =', d)
f2 = loop(d, 'NIFTY', fetch_ok=True, old_code=True)  # 09:20 — endpoint healthy
f3 = loop(d, 'NIFTY', fetch_ok=True, old_code=True)  # 09:25
print('  09:20  dobara fetch kiya? ->', f2)
print('  09:25  dobara fetch kiya? ->', f3)
check('purana: 0 levels poore din cache', d['NIFTY'], [])
check('purana: retry kabhi nahi hua', (f2, f3), (False, False))
print('  -> "NIFTY" key MAUJOOD hai (value khaali) -> `not in` kabhi True nahi -> din khatam')

print('')
print('=== NAYA code — wahi scenario ===')
d = {}
loop(d, 'NIFTY', fetch_ok=False, old_code=False)     # 09:15:36 — same failure
print('  09:15  fetch fail -> daily_levels =', d)
check('naya: failure cache NAHI hui', 'NIFTY' in d, False)
f2 = loop(d, 'NIFTY', fetch_ok=True, old_code=False)  # 09:20 — retry
print('  09:20  dobara fetch kiya? ->', f2, '| levels =', d.get('NIFTY'))
check('naya: agle loop me retry hua', f2, True)
check('naya: levels aa gaye', d['NIFTY'], ['P', 'R1', 'S1'])

print('')
print('=== success ke baad dobara fetch nahi (rate limit safety) ===')
f3 = loop(d, 'NIFTY', fetch_ok=True, old_code=False)
check('levels mil gaye -> ab har loop pe fetch nahi', f3, False)

print('')
print('=== har symbol apne aap independent ===')
d = {}
loop(d, 'NIFTY', fetch_ok=True, old_code=False)
loop(d, 'BANKNIFTY', fetch_ok=False, old_code=False)
check('NIFTY OK', d.get('NIFTY'), ['P', 'R1', 'S1'])
check('BANKNIFTY fail -> cache nahi, retry hoga', 'BANKNIFTY' in d, False)
check('ek symbol ka fail dusre ko nahi maarta', loop(d, 'NIFTY', True, False), False)

print('')
if fails:
    print('RESULT: %d FAILED -> %s' % (len(fails), fails))
    sys.exit(1)
print('RESULT: all passed')
