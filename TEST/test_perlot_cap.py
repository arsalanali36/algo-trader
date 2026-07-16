"""max_loss_per_lot_rs — opt-in per-strategy daily loss cap that scales with lots.

The whole point of the test: prove NOTHING changes for any strategy that doesn't
set the key, including the live %-of-balance branch.
"""
import os
import sys

os.chdir('/root/ARSALAN/CODE3B- TV BACKTEST ENGINE')
sys.path[:0] = ['_core', '_data', '.']
import risk_gate as rg

fails = []


def check(name, got, want):
    ok = (got == want)
    print(('  PASS  ' if ok else '  FAIL  ') + name)
    print('          got=%r want=%r' % (got, want))
    if not ok:
        fails.append(name)


print('')
print('=== 1. _configured_lots resolves from the RIGHT config shape ===')
check('arschain_MAIN (webhooks map) -> 2', rg._configured_lots('arschain_MAIN'), 2)
check('unknown strategy -> None (never guess)', rg._configured_lots('nope_xyz'), None)
check('None -> None', rg._configured_lots(None), None)

print('')
print('=== 2. per-lot cap wins over BOTH the flat Rs and the live %% branch ===')
RC = {'global': {'max_loss_rs': 5500, 'max_loss_pct': 1},
      'per_strategy': {'arschain_MAIN': {'max_loss_per_lot_rs': 2000}}}
check('paper: 2000/lot x 2 lots -> 4000',
      rg.effective_daily_loss_cap('arschain_MAIN', rc=RC), 4000.0)
check('LIVE: per-lot beats 1%%-of-balance (11,789) -> 4000',
      rg.effective_daily_loss_cap('arschain_MAIN', rc=RC, mode='live', broker='kite'),
      4000.0)

print('')
print('=== 3. OPT-IN: koi aur strategy nahi badalti ===')
RC2 = {'global': {'max_loss_rs': 5500, 'max_loss_pct': 1},
       'per_strategy': {'arschain_MAIN': {'max_loss_per_lot_rs': 2000},
                        'range_v1': {}}}
check('range_v1 (no key) -> flat 5500 as before',
      rg.effective_daily_loss_cap('range_v1', rc=RC2), 5500.0)
check('ARS_CHAIN_V1 (no key) -> flat 5500 as before',
      rg.effective_daily_loss_cap('ARS_CHAIN_V1', rc=RC2), 5500.0)
check('no strategy at all -> flat 5500',
      rg.effective_daily_loss_cap(None, rc=RC2), 5500.0)

print('')
print('=== 4. garbage values fall through, never fail-open ===')
for bad in (0, -100, 'abc', '', None):
    RC3 = {'global': {'max_loss_rs': 5500},
           'per_strategy': {'arschain_MAIN': {'max_loss_per_lot_rs': bad}}}
    check('per_lot=%r -> falls back to 5500' % (bad,),
          rg.effective_daily_loss_cap('arschain_MAIN', rc=RC3), 5500.0)

print('')
print('=== 5. lots unresolvable -> flat cap, NOT unlimited ===')
RC4 = {'global': {'max_loss_rs': 5500},
       'per_strategy': {'ghost_strat': {'max_loss_per_lot_rs': 2000}}}
check('unknown strategy + per-lot set -> 5500 (loud warn above)',
      rg.effective_daily_loss_cap('ghost_strat', rc=RC4), 5500.0)

print('')
print('=== 6. cap scales with lots (the whole point) ===')
for lots, want in ((1, 2000.0), (2, 4000.0), (4, 8000.0)):
    _real = rg._configured_lots
    rg._configured_lots = lambda s, _l=lots: _l
    try:
        check('%d lot -> Rs %.0f' % (lots, want),
              rg.effective_daily_loss_cap('arschain_MAIN', rc=RC), want)
    finally:
        rg._configured_lots = _real

print('')
if fails:
    print('RESULT: %d FAILED -> %s' % (len(fails), fails))
    sys.exit(1)
print('RESULT: all passed')
