"""v2: adds realistic per-leg SLIPPAGE (measured bucket spreads x crossing factor)."""
import delta_client as dc, datetime as dt, statistics as st, backtest_delta as bt

# measured median spreads (points) by |strike-spot| bucket (conservative: longer-dated mix)
def spread_pts(dist):
    a=abs(dist)
    return 14 if a<=500 else 22 if a<=2000 else 39 if a<=4000 else 74

def run_slip(d, H, strat, str_off, hedge_off, cross):
    """cross=0.5 limit / 1.0 market. Slippage on ENTRY 4 legs only (settlement=cash, no fill)."""
    b=bt.build(d,6)
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
        e=bt.prem(sym,ts,exp_ts,"entry"); s2=bt.prem(sym,ts,exp_ts,"settle")
        if e is None or s2 is None or e<=0: return None
        slip = cross * spread_pts(k-spot) / 2.0     # cross half the spread (per leg)
        tot += side*(e-s2) - bt.fee(e,spot) - bt.fee(s2,spot) - slip
    return tot

import random; random.seed(42)
def sp(p,it=3000):
    o=abs(sum(p)/len(p)); c=sum(1 for _ in range(it) if abs(sum(x*random.choice((1,-1)) for x in p)/len(p))>=o); return c/it
def spl(p): k=int(len(p)*0.65); return sum(p[:k])/max(k,1), sum(p[k:])/max(len(p)-k,1)

if __name__=="__main__":
    exps=sorted(bt.expiries(180))
    for cross,lab in [(0.0,"NO slip (ref)"),(0.5,"HALF-cross (limit)"),(1.0,"FULL-cross (market, pessimistic)")]:
        print(f"\n=== Iron-Fly (ATM+2000 wings) — {lab} ===")
        print(f"{'H':<5}{'N':<5}{'net':<8}{'mean':<7}{'win%':<6}{'sharpe':<8}{'p-val':<7}{'train':<7}{'oos':<7}{'DEPLOY'}")
        for H in [8,10,12,16,20,24]:
            p=[run_slip(d,H,"fly",0,2000,cross) for d in exps]; p=[x for x in p if x is not None]
            if len(p)<12: continue
            m=bt.metrics(p); pv=sp(p); tr,oo=spl(p)
            dep="YES" if (pv<0.05 and min(tr,oo)>0 and m['sharpe']>=1) else ""
            print(f"{H:<5}{m['n']:<5}{m['net']:<8.0f}{m['mean']:<7.1f}{m['wr']:<6.0f}{m['sharpe']:<8.2f}{pv:<7.3f}{tr:<7.1f}{oo:<7.1f}{dep}")
