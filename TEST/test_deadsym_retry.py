"""Transient fetch failure -> retry. Permanently-dead symbol -> say once, stay quiet.

The retry fix (09:37) treated both the same, so TATAMOTORS — which Dhan has no
scrip-master entry for at all — retried every loop and rang the bell every loop.
"""
import sys
fails = []


def check(name, got, want):
    ok = got == want
    print(('  PASS  ' if ok else '  FAIL  ') + name)
    if not ok:
        print('          got=%r want=%r' % (got, want))
        fails.append(name)


DHAN = {"NIFTY": ("13", "IDX_I", "INDEX")}      # TATAMOTORS jaan-boojh kar nahi hai


def loop(state, symbol, fetch_ok, variant):
    """One iteration. Returns (fetched?, alerted?)."""
    lv, dead, said = state['lv'], state['dead'], state['said']
    if variant == 'v2' and symbol in dead:
        return (False, False)
    if lv.get(symbol):
        return (False, False)
    levels = ['P', 'R1'] if fetch_ok else []
    lv[symbol] = levels
    if levels:
        return (True, False)
    if variant == 'v1':                       # my 09:37 fix — no permanence check
        lv.pop(symbol, None)
        return (True, True)                   # retries AND alerts every loop
    permanent = DHAN.get(symbol) is None      # v2 — the correction
    lv.pop(symbol, None)
    if permanent:
        dead.add(symbol)
    key = ('deadsym' if permanent else 'levels0') + ':' + symbol
    alerted = key not in said
    said.add(key)
    return (True, alerted)


def fresh():
    return {'lv': {}, 'dead': set(), 'said': set()}


print('')
print('=== TATAMOTORS (Dhan me hai hi nahi) — meri 09:37 wali fix ===')
s = fresh()
r = [loop(s, 'TATAMOTORS', False, 'v1') for _ in range(4)]
check('v1: har loop fetch karta hai', [x[0] for x in r], [True] * 4)
check('v1: har loop bell bajata hai', [x[1] for x in r], [True] * 4)
print('  -> yahi spam aapko dikha')

print('')
print('=== wahi symbol — correction ===')
s = fresh()
r = [loop(s, 'TATAMOTORS', False, 'v2') for _ in range(4)]
check('v2: sirf pehli baar fetch', [x[0] for x in r], [True, False, False, False])
check('v2: sirf pehli baar bell', [x[1] for x in r], [True, False, False, False])
check('v2: dead_syms me chala gaya', 'TATAMOTORS' in s['dead'], True)

print('')
print('=== NIFTY transient fail (401 burst) — retry ZINDA rehna chahiye ===')
s = fresh()
a = loop(s, 'NIFTY', False, 'v2')     # 09:15 DH-902
b = loop(s, 'NIFTY', True, 'v2')      # 09:20 healthy
c = loop(s, 'NIFTY', True, 'v2')      # 09:25
check('transient: pehla fail', a, (True, True))
check('transient: agle loop me RETRY hua', b[0], True)
check('transient: levels aa gaye', s['lv']['NIFTY'], ['P', 'R1'])
check('transient: dead_syms me NAHI dala', 'NIFTY' in s['dead'], False)
check('success ke baad dobara fetch nahi', c[0], False)
print('  -> permanent aur transient ab alag — dono sahi')

print('')
if fails:
    print('RESULT: %d FAILED -> %s' % (len(fails), fails)); sys.exit(1)
print('RESULT: all passed')
