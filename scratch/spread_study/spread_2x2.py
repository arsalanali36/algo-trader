"""Same structures, BOTH exit rules -> isolates rule vs instrument."""
import os, glob, datetime as dt
import pandas as pd

BASE="_TRADING_DATA/OptionChain"; COLS=["datetime","spot","expiry","strike","opt_type","ltp","bid","ask"]

def snap(p):
    try: d=pd.read_csv(p,usecols=COLS)
    except Exception: return None
    t=d["datetime"].astype(str).str[-8:-3]; d=d[(t>="09:20")&(t<="09:26")]
    if d.empty: return None
    d=d[d["datetime"]==d["datetime"].min()]
    d=d[(d.bid>0)&(d.ask>0)&(d.ask>=d.bid)]
    return d if len(d) else None

def leg(d,e,k,t):
    r=d[(d.expiry==e)&(d.strike==k)&(d.opt_type==t)]
    if r.empty: return None
    r=r.iloc[0]; b,a=float(r.bid),float(r.ask)
    return dict(mid=(a+b)/2,spread=a-b)

def collect(sym,step,dist,wing,mode):
    out=[]
    for p in sorted(glob.glob(f"{BASE}/{sym}/{sym}_*.csv")):
        day=os.path.basename(p).split("_")[1][:10]; d=snap(p)
        if d is None: continue
        spot=float(d.spot.iloc[0]); atm=round(spot/step)*step
        d0=dt.date.fromisoformat(day)
        c=[(e,(dt.date.fromisoformat(e)-d0).days) for e in sorted(d.expiry.astype(str).unique())]
        c=[(e,n) for e,n in c if n>=(3 if mode=="monthly" else 0)]
        if not c: continue
        e,dte=c[0]
        L=[leg(d,e,atm+dist,"CE"),leg(d,e,atm-dist,"PE"),
           leg(d,e,atm+dist+wing,"CE"),leg(d,e,atm-dist-wing,"PE")]
        if any(x is None for x in L): continue
        credit=L[0]["mid"]+L[1]["mid"]-L[2]["mid"]-L[3]["mid"]
        out.append(dict(day=day,dte=dte,credit=credit,spread=sum(x["spread"] for x in L)))
    return pd.DataFrame(out)

bnf=collect("BANKNIFTY",100,600,500,"monthly")
nif=collect("NIFTY",50,250,250,"weekly")

def pct(df,thr):
    v=(df.spread/thr*100).replace([float("inf")],float("nan")).dropna()
    return v

print("="*92)
print("SPREAD as % of the exit threshold  —  SAME structures, BOTH exit rules")
print("(clean days only: DTE>=1, and threshold > 2 pts)")
print("="*92)
print(f"{'':<26}{'FIXED 26.7 pt':>26}{'50% of CREDIT':>26}")
print(f"{'':<26}{'median':>10}{'worst':>8}{'n':>6}{'median':>12}{'worst':>8}{'n':>6}")
for name,df in (("02.10.01 BNF monthly",bnf),("02.15    NIFTY weekly",nif)):
    d=df[df.dte>=1].copy()
    a=pct(d,26.7)
    d2=d[d.credit*0.5>2]
    b=pct(d2,d2.credit*0.5)
    print(f"{name:<26}{a.median():>9.1f}%{a.max():>7.1f}%{len(a):>6}"
          f"{b.median():>11.1f}%{b.max():>7.1f}%{len(b):>6}")

print("\n" + "="*92)
print("BNF ko DTE ke hisaab se toda (fixed 26.7 pt exit)")
print("="*92)
b=bnf[bnf.dte>=1].copy()
b["bucket"]=pd.cut(b.dte,[0,7,14,21,40],labels=["1-7 (expiry ke paas)","8-14","15-21","22-40 (naya monthly)"])
g=b.groupby("bucket",observed=True).apply(
    lambda x: pd.Series({"days":len(x),"credit":x.credit.median(),"spread":x.spread.median(),
                         "vs 26.7pt":f"{(x.spread/26.7*100).median():.1f}%",
                         "vs 50%credit":f"{(x.spread/(x.credit*0.5)*100).median():.1f}%"}))
print(g.to_string())
