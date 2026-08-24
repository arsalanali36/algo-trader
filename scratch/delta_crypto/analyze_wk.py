import backtest_weekly as w, backtest_delta as bt, random
random.seed(42)
def sp(p,it=3000):
    o=abs(sum(p)/len(p)); return sum(1 for _ in range(it) if abs(sum(x*random.choice((1,-1)) for x in p)/len(p))>=o)/it
def spl(p): k=int(len(p)*0.65); return sum(p[:k])/max(k,1), sum(p[k:])/max(len(p)-k,1)
fr=sorted(w.fridays(26))
cross=0.5  # realistic limit-order fills
print(f"weekly expiries: {len(fr)}  {fr[0]} -> {fr[-1]}  (slippage=half-cross)\n")
cfgs=[("fly ATM+2000w","fly",0,2000),
      ("fly ATM+3000w","fly",0,3000),
      ("condor s1500+2000w","condor",1500,2000),
      ("condor s2500+2000w","condor",2500,2000)]
for lab,strat,so,ho in cfgs:
    print(f"=== {lab} ===")
    print(f"{'Dbef':<6}{'N':<5}{'net':<8}{'mean':<7}{'win%':<6}{'sharpe':<8}{'p-val':<7}{'train':<7}{'oos':<7}{'DEPLOY'}")
    for D in [1,2,3,4]:
        p=[w.run_wk(f,D,strat,so,ho,cross) for f in fr]; p=[x for x in p if x is not None]
        if len(p)<10: 
            print(f"{D:<6}{len(p):<5}(insufficient)"); continue
        m=bt.metrics(p); pv=sp(p); tr,oo=spl(p)
        dep="YES" if (pv<0.05 and min(tr,oo)>0 and m['sharpe']>=1) else ""
        print(f"{D:<6}{m['n']:<5}{m['net']:<8.0f}{m['mean']:<7.1f}{m['wr']:<6.0f}{m['sharpe']:<8.2f}{pv:<7.3f}{tr:<7.1f}{oo:<7.1f}{dep}")
    print()
