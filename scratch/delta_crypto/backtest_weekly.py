"""Weekly BTC short-vol: enter D days before Friday 12:00 UTC expiry, hold to settlement.
Weeklies have more strikes -> condor/strangle viable. Slippage-aware."""
import delta_client as dc, datetime as dt, statistics as st, backtest_delta as bt, os, json, time
CACHE="data/cache_wk"; os.makedirs(CACHE,exist_ok=True)

def ccw(sym,start,end,res="15m"):   # 15m res: 6 days = 576 bars
    key=f"{CACHE}/{sym}_{res}.json"
    if os.path.exists(key):
        d=json.load(open(key))
        if d["s"]<=start and d["e"]>=end: return d["c"]
    c=dc.candles(sym,res,start,end); json.dump({"s":int(start),"e":int(end),"c":c},open(key,"w")); time.sleep(0.02)
    return c

def spread_pts(dist):
    a=abs(dist); return 14 if a<=500 else 22 if a<=2000 else 39 if a<=4000 else 74

def fridays(weeks_back=26):
    out=[]; d=dt.date.today()
    while d.weekday()!=4: d-=dt.timedelta(days=1)  # last Friday
    d-=dt.timedelta(days=7)  # skip current (may be live)
    for _ in range(weeks_back):
        out.append(d); d-=dt.timedelta(days=7)
    return out

def prem_wk(sym,ts,exp_ts,which):
    c=ccw(sym,exp_ts-6*86400,exp_ts+900)
    if not c: return None
    if which=="settle": return c[0]["close"]
    b=min(c,key=lambda x:abs(x["time"]-ts))
    return b["close"] if abs(b["time"]-ts)<=2700 else None

def run_wk(fri, days_before, strat, str_off, hedge_off, cross):
    exp_ts=int(dt.datetime(fri.year,fri.month,fri.day,12,0,tzinfo=dt.UTC).timestamp())
    ts=exp_ts-int(days_before*86400)
    spot=dc.spot_at("BTCUSD",ts,res="15m",window=7200)
    if not spot: return None
    atm=round(spot/500)*500
    if strat=="fly": legs=[("C",atm,+1),("P",atm,+1),("C",atm+hedge_off,-1),("P",atm-hedge_off,-1)]
    else:
        cs,ps=atm+str_off,atm-str_off
        legs=[("C",cs,+1),("P",ps,+1),("C",cs+hedge_off,-1),("P",ps-hedge_off,-1)]
    tot=0.0
    for cp,k,side in legs:
        sym=dc.opt_symbol(cp,"BTC",k,fri)
        e=prem_wk(sym,ts,exp_ts,"entry"); s2=prem_wk(sym,ts,exp_ts,"settle")
        if e is None or s2 is None or e<=0: return None
        slip=cross*spread_pts(k-spot)/2.0
        tot += side*(e-s2) - bt.fee(e,spot) - bt.fee(s2,spot) - slip
    return tot

if __name__=="__main__":
    import sys
    fr=fridays(26)
    if sys.argv[1:]==["warm"]:
        for i,fri in enumerate(fr):
            exp_ts=int(dt.datetime(fri.year,fri.month,fri.day,12,0,tzinfo=dt.UTC).timestamp())
            s=dc.spot_at("BTCUSD",exp_ts-3*86400,res="15m",window=7200)
            if not s: print(f"skip {fri} no spot",flush=True); continue
            atm=round(s/500)*500
            for k in [atm,atm+2000,atm-2000,atm+3000,atm-3000,atm+5000,atm-5000,atm+1500,atm-1500,atm+2500,atm-2500,atm+4500,atm-4500]:
                for cp in ("C","P"): ccw(dc.opt_symbol(cp,"BTC",k,fri),exp_ts-6*86400,exp_ts+900)
            print(f"warmed {i+1}/{len(fr)} {fri}",flush=True)
        print("WK WARM DONE",flush=True)
