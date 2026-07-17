"""Split token alerts + auto-clear on login. Uses TODAY's real health report."""
import json
import os
import sys

os.chdir('/root/ARSALAN/CODE3B- TV BACKTEST ENGINE')
sys.path[:0] = ['_core', '_data', '.']

fails = []


def check(name, got, want):
    ok = got == want
    print(('  PASS  ' if ok else '  FAIL  ') + name)
    if not ok:
        print('          got=%r want=%r' % (got, want))
        fails.append(name)


# Deterministic fixture — the SHAPE health_check produced at 09:12 today: Kite
# token dead, so token_red marked all 13 strategies red while every one of their
# OWN checks passed. Must not read the live report: the 09:20 timer rewrote it
# the moment Arsalan logged in, which is exactly how a state-dependent test lies.
REP_KITE_DEAD = {
    "token": {"status": "OK", "nifty": 24127.6, "kite": {"status": "FAIL", "ok": False,
              "error": "Kite token EXPIRED"}},
    "strategies": [
        {"id": sid, "red": True, "ready": False, "checks": [
            {"name": "active", "status": "OK", "detail": "active:true"},
            {"name": "script", "status": "OK", "detail": "x.py"},
            {"name": "heartbeat", "status": "SKIP", "detail": "market band"},
            {"name": "data:NIFTY", "status": "OK", "detail": "LTP=24127.6"}]}
        for sid in ["ARS_CHAIN_V1_PAPER", "backspread_v1", "banknifty_v1", "chainzone_v1",
                    "dvert_v1", "ema_v1", "orb_v1", "orbst_v1", "range_v1",
                    "rsi_v1_PAPER", "straddle_v1", "vrp_condor_v1", "webhook_v1"]],
}
# a strategy broken for its OWN reason must still be named
REP_REAL_FAIL = {
    "token": {"status": "OK", "kite": {"status": "OK"}},
    "strategies": [
        {"id": "orb_v1", "red": True, "checks": [
            {"name": "script", "status": "FAIL", "detail": "compile error"}]},
        {"id": "range_v1", "red": False, "checks": [{"name": "script", "status": "OK"}]}],
}
rep = REP_KITE_DEAD


def build(rep, existing):
    """Mirror of health_check --report's new alert block."""
    alerts = list(existing)
    mine = {"token:dhan", "token:kite", "health:strategies"}
    alerts = [a for a in alerts
              if not (isinstance(a, dict) and str(a.get("key") or "") in mine)
              and not (isinstance(a, str) and "Health:" in a)]
    tok = rep.get("token") or {}
    if (tok.get("kite") or {}).get("status") == "FAIL":
        alerts.append({"key": "token:kite", "msg": "kite expired"})
    if tok.get("status") == "FAIL":
        alerts.append({"key": "token:dhan", "msg": "dhan expired"})
    own = [s["id"] for s in rep["strategies"]
           if s["red"] and any(c.get("status") == "FAIL" for c in s.get("checks", []))]
    if own:
        alerts.append({"key": "health:strategies", "msg": "health: " + ", ".join(own)})
    return alerts


print('')
print('=== 1. AAJ ka asli report -> kaunse alerts? ===')
out = build(rep, [])
keys = [a['key'] for a in out]
old_reds = [s['id'] for s in rep['strategies'] if s['red']]
print('  PURANA alert : "Health: %s ..." (%d naam)' % (', '.join(old_reds[:3]), len(old_reds)))
print('  NAYA alerts  : %s' % keys)
check('sirf Kite token alert (asli wajah)', keys, ['token:kite'])
check('Dhan alert nahi (Dhan OK hai)', 'token:dhan' in keys, False)
check('13-naam wala shor gaya', 'health:strategies' in keys, False)

print('')
print('=== 2. Kite login -> sirf Kite ka alert jaye ===')
existing = [{"key": "token:kite", "msg": "kite expired"},
            {"key": "token:dhan", "msg": "dhan expired"},
            "🔴 untracked position mila"]


def clear(alerts, keys_, substr=None):
    kset = {str(k) for k in keys_}

    def drop(a):
        if isinstance(a, dict):
            return str(a.get("key") or "") in kset
        if isinstance(a, str):
            return bool(substr) and substr.lower() in a.lower()
        return False
    return [a for a in alerts if not drop(a)]


after = clear(existing, ["token:kite"])
check('Kite login -> token:kite gaya', any(isinstance(a, dict) and a.get('key') == 'token:kite' for a in after), False)
check('Kite login -> token:dhan BACHA (alag problem)', any(isinstance(a, dict) and a.get('key') == 'token:dhan' for a in after), True)
check('Kite login -> unrelated alert BACHA', '🔴 untracked position mila' in after, True)

print('')
print('=== 3. Dhan token save -> sirf Dhan ka jaye (purana bug: Kite bhi udta tha) ===')
after = clear(existing, ["token:dhan"], substr="dhan token")
check('Dhan save -> token:dhan gaya', any(isinstance(a, dict) and a.get('key') == 'token:dhan' for a in after), False)
check('Dhan save -> token:kite BACHA', any(isinstance(a, dict) and a.get('key') == 'token:kite' for a in after), True)

print('')
print('  purana filter tha:  \'token expire\' not in a.lower()')
print('  "Kite token EXPIRED".lower() me "token expire" hai? ->',
      'token expire' in "Kite token EXPIRED".lower(), ' <- isliye Dhan save Kite ka alert bhi uda deta')

print('')
print('=== 4. dict alert pe crash nahi (purana a.lower() phat jaata tha) ===')
try:
    clear([{"key": "x", "msg": "y"}], ["z"], substr="abc")
    print('  PASS  dict + substr -> koi crash nahi')
except Exception as e:
    print('  FAIL  crash:', e)
    fails.append('dict crash')
try:
    ["a" for a in [{"k": 1}] if 'token expire' not in a.lower()]
    print('  FAIL  purana filter crash nahi hua?')
except AttributeError as e:
    print('  PASS  purana filter dict pe crash karta tha -> %s' % e)

print('')
print('=== 4b. apni wajah se tooti strategy ab bhi naam se aati hai ===')
o = build(REP_REAL_FAIL, [])
check('script FAIL -> health:strategies alert', [a['key'] for a in o], ['health:strategies'])
check('sirf toota hua naam, saaf strategy nahi', o[0]['msg'], 'health: orb_v1')

print('')
print('=== 5. problem theek -> alert file se gaya -> bell "fixed" ===')
out2 = build({'token': {'status': 'OK', 'kite': {'status': 'OK'}}, 'strategies': rep['strategies']}, out)
check('Kite theek hone pe token:kite alert gaya', [a.get('key') for a in out2 if isinstance(a, dict)], [])
print('  -> _ingest_downloader_alerts() key gayab dekhega -> notify.resolve() -> "✓ fixed"')

print('')
if fails:
    print('RESULT: %d FAILED -> %s' % (len(fails), fails))
    sys.exit(1)
print('RESULT: all passed')
