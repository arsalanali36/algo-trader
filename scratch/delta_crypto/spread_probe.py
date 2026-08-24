import requests, statistics as st
from collections import defaultdict
BASE="https://api.india.delta.exchange"; S=requests.Session()
def get(p,params=None): return S.get(BASE+p,params=params,timeout=25).json()
j=get("/v2/tickers",{"contract_types":"call_options,put_options"})
res=j.get("result",[])
rows=[]
for t in res:
    sym=t.get("symbol","")
    if not sym.startswith(("C-BTC","P-BTC")): continue
    q=t.get("quotes") or {}; bb=q.get("best_bid"); ba=q.get("best_ask")
    sp=t.get("spot_price")
    if not(bb and ba and sp): continue
    bb=float(bb); ba=float(ba); sp=float(sp); mid=(bb+ba)/2
    if mid<=0: continue
    try: k=int(sym.split("-")[2])
    except: continue
    rows.append((k-sp, mid, ba-bb, (ba-bb)/mid*100))
print(f"BTC option quotes: {len(rows)}, spot~{res and [float(t['spot_price']) for t in res if t.get('symbol','').startswith('C-BTC') and t.get('spot_price')][0]:.0f}\n")
def bkt(d):
    a=abs(d)
    return "ATM(0-500)" if a<=500 else "near(500-2000)" if a<=2000 else "wing(2000-4000)" if a<=4000 else "far(>4000)"
buk=defaultdict(list)
for d,mid,spr,sprp in rows: buk[bkt(d)].append((mid,spr,sprp))
print(f"{'moneyness':<18}{'n':<5}{'med_mid':<9}{'med_spr_pts':<13}{'med_spr%'}")
for b in ["ATM(0-500)","near(500-2000)","wing(2000-4000)","far(>4000)"]:
    v=buk.get(b,[])
    if v: print(f"{b:<18}{len(v):<5}{st.median([m for m,_,_ in v]):<9.1f}{st.median([s for _,s,_ in v]):<13.1f}{st.median([p for _,_,p in v]):.1f}")
