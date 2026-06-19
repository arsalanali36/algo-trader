#!/usr/bin/env python3
"""Quick test: NIFTY ATM CE/PE live LTP from Dhan"""
import json, requests, socket, time, csv

_orig = socket.getaddrinfo
def _v4(h,p,f=0,t=0,pr=0,fl=0): return _orig(h,p,socket.AF_INET,t,pr,fl)
socket.getaddrinfo = _v4

cfg = json.loads(open('data/config.json').read())
token, cid = cfg['jwt_token'], cfg['client_id']
hdrs = {'access-token': token, 'client-id': cid, 'Content-Type': 'application/json'}

r_idx = requests.post('https://api.dhan.co/v2/marketfeed/ltp',
    json={'IDX_I': [13]}, headers=hdrs, timeout=5)
idx = float(r_idx.json()['data']['IDX_I']['13']['last_price'])
atm = round(idx / 50) * 50
print(f'NIFTY Index (Dhan IDX_I): {idx:.2f}  ATM Strike: {atm}')

ce_sec = pe_sec = None
with open('data/api-scrip-master.csv') as f:
    for row in csv.DictReader(f):
        ts = row.get('SEM_TRADING_SYMBOL', '')
        if 'NIFTY' in ts and str(atm) in ts and 'BANK' not in ts:
            if ts.endswith('-CE') and not ce_sec:
                ce_sec = (row['SEM_SMST_SECURITY_ID'], ts)
            if ts.endswith('-PE') and not pe_sec:
                pe_sec = (row['SEM_SMST_SECURITY_ID'], ts)
        if ce_sec and pe_sec:
            break

print(f'CE: {ce_sec}')
print(f'PE: {pe_sec}')
print('--- Live LTP (5 readings, 2s apart) ---')

for i in range(5):
    ids = [int(ce_sec[0]), int(pe_sec[0])]
    body = {'NSE_FNO': ids}
    # Try marketfeed/ltp endpoint
    r = requests.post('https://api.dhan.co/v2/marketfeed/ltp', json=body, headers=hdrs, timeout=5)
    print(f'  marketfeed/ltp  HTTP {r.status_code}  body={r.text[:300]}')
    time.sleep(2)
