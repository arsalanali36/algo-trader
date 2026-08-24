"""Phase-2 analysis: fly + condor entry-time sweep with significance + train/OOS."""
import backtest_delta as bt, datetime as dt, random, statistics as st
random.seed(42)

def signflip_p(pnls, iters=5000):
    """Null: signs are random noise. p = P(|perm mean| >= |obs mean|)."""
    obs=abs(sum(pnls)/len(pnls)); cnt=0
    for _ in range(iters):
        m=sum(x*random.choice((1,-1)) for x in pnls)/len(pnls)
        if abs(m)>=obs: cnt+=1
    return cnt/iters

def split(pnls):
    k=int(len(pnls)*0.65)
    tr=pnls[:k]; oo=pnls[k:]
    return (sum(tr)/len(tr) if tr else 0), (sum(oo)/len(oo) if oo else 0)

def evalcfg(exps, H, strat, str_off, hedge_off):
    # chronological order (oldest first)
    rows=[(d, bt.run(d,H,strat,str_off,hedge_off)) for d in sorted(exps)]
    p=[r for _,r in rows if r is not None]
    if len(p)<12: return None
    m=bt.metrics(p); trm,oom=split(p); pv=signflip_p(p)
    m.update(p_value=pv, train=trm, oos=oom,
             deploy=(pv<0.05 and min(trm,oom)>0 and m['sharpe']>=1))
    return m

if __name__=="__main__":
    exps=bt.expiries(180)
    print(f"expiries window: {len(exps)}  {min(exps)} -> {max(exps)}\n")
    grids=[
        ("fly (ATM + 2000 wings)", "fly", 0, 2000),
        ("condor (str1500 + 2000 wings)", "condor", 1500, 2000),
    ]
    for label,strat,so,ho in grids:
        print(f"=== {label} ===")
        print(f"{'H':<5}{'N':<5}{'net':<8}{'mean':<7}{'win%':<6}{'sharpe':<8}{'p-val':<7}{'train':<7}{'oos':<7}{'DEPLOY'}")
        for H in [4,6,8,10,12,16,20,24,36,48]:
            m=evalcfg(exps,H,strat,so,ho)
            if m:
                flag="YES" if m['deploy'] else ""
                print(f"{H:<5}{m['n']:<5}{m['net']:<8.0f}{m['mean']:<7.1f}{m['wr']:<6.0f}{m['sharpe']:<8.2f}{m['p_value']:<7.3f}{m['train']:<7.1f}{m['oos']:<7.1f}{flag}")
        print()
