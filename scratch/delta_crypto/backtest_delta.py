"""Phase-2 BTC short-vol backtester on Delta India real premium (zero-auth).
ATM fixed per expiry from a reference time; chain cached once; entry-time swept cheaply.
PnL in premium POINTS (per-BTC USD). Real $/contract = points x 0.001.
"""
import delta_client as dc, datetime as dt, time, os, json, statistics as st

CACHE="data/cache"; os.makedirs(CACHE, exist_ok=True)
FEE_RATE=0.0001; FEE_PREM_CAP=0.10
_spot_cache={}

def cc(sym, start, end, res="5m"):
    key=f"{CACHE}/{sym}_{res}.json"
    if os.path.exists(key):
        d=json.load(open(key))
        if d.get("s")<=start and d.get("e")>=end: return d["c"]
    c=dc.candles(sym,res,start,end)
    json.dump({"s":int(start),"e":int(end),"c":c}, open(key,"w")); time.sleep(0.02)
    return c

def spot_at(ts):
    k=round(ts/300)*300
    if k in _spot_cache: return _spot_cache[k]
    v=dc.spot_at("BTCUSD",ts); _spot_cache[k]=v; return v

def prem(sym, ts, exp_ts, which):
    c=cc(sym, exp_ts-30*3600, exp_ts+600)
    if not c: return None
    if which=="settle": return c[0]["close"]
    b=min(c,key=lambda x:abs(x["time"]-ts))
    return b["close"] if abs(b["time"]-ts)<=900 else None

def fee(p,spot): return min(FEE_RATE*spot, FEE_PREM_CAP*max(p,0.05))

def build(d, ref_h=6):
    """Fix ATM from spot ref_h before expiry; return (exp_ts, spot_ref, atm)."""
    exp_ts=int(dt.datetime(d.year,d.month,d.day,12,0,tzinfo=dt.UTC).timestamp())
    s=spot_at(exp_ts-int(ref_h*3600))
    if not s: return None
    return exp_ts, s, round(s/500)*500

def run(d, H, strat, str_off, hedge_off, ref_h=6):
    b=build(d, ref_h)
    if not b: return None
    exp_ts, spot, atm = b
    ts=exp_ts-int(H*3600)
    if strat=="fly":
        legs=[("C",atm,+1),("P",atm,+1),("C",atm+hedge_off,-1),("P",atm-hedge_off,-1)]
    else:
        cs,ps=atm+str_off,atm-str_off
        legs=[("C",cs,+1),("P",ps,+1),("C",cs+hedge_off,-1),("P",ps-hedge_off,-1)]
    tot=0.0
    for cp,k,side in legs:
        sym=dc.opt_symbol(cp,"BTC",k,d)
        e=prem(sym,ts,exp_ts,"entry"); s2=prem(sym,ts,exp_ts,"settle")
        if e is None or s2 is None or e<=0: return None
        tot += side*(e-s2) - fee(e,spot) - fee(s2,spot)
    return tot

def expiries(days_back):
    return [dt.date.today()-dt.timedelta(days=db)
            for db in range(2,days_back) if (dt.date.today()-dt.timedelta(days=db)).weekday()<5]

def metrics(p):
    n=len(p); s=sum(p); wr=sum(1 for x in p if x>0)/n*100 if n else 0
    mean=s/n if n else 0; sd=st.pstdev(p) if n>1 else 0
    sh=mean/sd*(n**0.5) if sd else 0
    cum=peak=mdd=0
    for x in p: cum+=x; peak=max(peak,cum); mdd=min(mdd,cum-peak)
    return dict(n=n,net=s,mean=mean,wr=wr,sharpe=sh,mdd=mdd)

if __name__=="__main__":
    import sys
    mode=sys.argv[1] if len(sys.argv)>1 else "sweep"
    exps=expiries(180)
    if mode=="warm":
        # pre-fetch chain for all expiries (fly wings 2000 + condor 1500 off + 2000 wing)
        for i,d in enumerate(exps):
            b=build(d,6)
            if not b: continue
            _,_,atm=b
            for k in [atm, atm+2000, atm-2000, atm+1500, atm-1500, atm+3500, atm-3500]:
                for cp in ("C","P"):
                    exp_ts=build(d,6)[0]
                    cc(dc.opt_symbol(cp,"BTC",k,d), exp_ts-30*3600, exp_ts+600)
            print(f"warmed {i+1}/{len(exps)} {d}", flush=True)
        print("WARM DONE", flush=True)
    else:
        print(f"expiries: {len(exps)} ({exps[-1]}->{exps[0]})\n")
        print("=== ENTRY-TIME SWEEP: Iron-Fly (ATM + 2000 wings) ===")
        print(f"{'H_bef':<7}{'N':<5}{'net':<9}{'mean':<8}{'win%':<7}{'sharpe':<8}{'maxDD'}")
        for H in [1,2,3,4,6,8,10,24]:
            p=[run(d,H,"fly",0,2000) for d in exps]; p=[x for x in p if x is not None]
            if p: m=metrics(p); print(f"{H:<7}{m['n']:<5}{m['net']:<9.0f}{m['mean']:<8.1f}{m['wr']:<7.0f}{m['sharpe']:<8.2f}{m['mdd']:.0f}")
