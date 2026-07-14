"""Portfolio view — combined result of ALL deployed strategies (auto-discovered from the
registry: status paper/live with a backtest run). Writes portfolio.json for portfolio.html:
combined equity curve, headline metrics, correlation matrix, per-strategy breakdown.
Run: python -X utf8 build_portfolio.py"""
import json, os, numpy as np, pandas as pd
HERE=os.path.dirname(os.path.abspath(__file__)); RUNS=os.path.join(HERE,"runs")
REG=os.path.join(HERE,"..","..","strategy_registry.json")

def deployed():
    reg=json.load(open(REG,encoding="utf-8"))["strategies"]
    out=[]
    for sid,m in sorted(reg.items()):
        if m.get("status") in ("paper","live") and m.get("slug"):
            p=os.path.join(RUNS,m["slug"],"results.js")
            if os.path.exists(p): out.append((sid,m["name"],m["slug"],m["status"]))
    return out

def daily(slug):
    raw=open(os.path.join(RUNS,slug,"results.js"),encoding="utf-8").read()
    o,_=json.JSONDecoder().raw_decode(raw[raw.index("{"):])
    dd={}
    for t in o["combos"].get("bs|full",{}).get("all_trades",[]):
        d=str(pd.to_datetime(t["exit_dt"]).date()); dd[d]=dd.get(d,0.0)+float(t["pnl"])
    return o["combos"].get("bs|full",{}).get("metrics",{}), dd

def shp(x): return float(x.mean()/x.std(ddof=1)*np.sqrt(252)) if len(x)>2 and x.std(ddof=1)>0 else 0.0
def maxdd(cum): return float((cum-np.maximum.accumulate(cum)).min())

def main():
    strats=deployed(); series={}; meta_by={}
    for sid,name,slug,status in strats:
        m,dd=daily(slug); series[sid]=pd.Series(dd); meta_by[sid]=dict(name=name,slug=slug,status=status,metrics=m)
    df=pd.DataFrame(series).sort_index().fillna(0.0)
    df.index=pd.to_datetime(df.index)
    ids=list(df.columns)
    # per-strategy
    per=[]
    for sid in ids:
        x=df[sid]; per.append(dict(id=sid, name=meta_by[sid]["name"], slug=meta_by[sid]["slug"],
            status=meta_by[sid]["status"], sharpe=round(shp(x),2), net=round(float(x.sum())),
            trades=meta_by[sid]["metrics"].get("trades"), maxdd=round(maxdd(x.cumsum()))))
    # correlation
    corr=df.corr().round(2)
    # combined books
    eq=df.sum(axis=1); eq_cum=eq.cumsum()
    w=1.0/df.std(ddof=1); w=(w/w.sum())
    rp=(df*w).sum(axis=1)
    # equity curve (equal-lot cumulative) downsampled
    cum=eq_cum.values; idx=df.index
    N=300; step=max(1,len(cum)//N)
    curve=[[str(idx[i].date()), round(1_000_000+float(cum[i]))] for i in range(0,len(cum),step)]
    if curve and curve[-1][0]!=str(idx[-1].date()): curve.append([str(idx[-1].date()),round(1_000_000+float(cum[-1]))])
    out=dict(
        generated=str(pd.Timestamp("now"))[:16] if False else None,
        window=[str(idx.min().date()),str(idx.max().date())], days=len(df),
        strategies=per,
        combined=dict(
            equal_lot=dict(sharpe=round(shp(eq),2), net=round(float(eq.sum())), maxdd=round(maxdd(eq_cum.values)),
                           net_pct=round(float(eq.sum())/1_000_000*100,1)),
            risk_parity=dict(sharpe=round(shp(rp),2)),
            best_single=round(max(p["sharpe"] for p in per),2),
            avg_single=round(float(np.mean([p["sharpe"] for p in per])),2)),
        corr=dict(ids=ids, names={sid:meta_by[sid]["name"] for sid in ids},
                  matrix=[[float(corr.loc[a,b]) for b in ids] for a in ids]),
        curve=curve)
    json.dump(out, open(os.path.join(HERE,"portfolio.json"),"w"), indent=1, default=float)
    print(f"portfolio.json: {len(per)} strategies, {len(df)} days")
    print(f"  combined equal-lot Sharpe {out['combined']['equal_lot']['sharpe']} net Rs{out['combined']['equal_lot']['net']:,} DD Rs{out['combined']['equal_lot']['maxdd']:,}")
    print(f"  risk-parity Sharpe {out['combined']['risk_parity']['sharpe']} vs best-single {out['combined']['best_single']} avg-single {out['combined']['avg_single']}")

if __name__=="__main__": main()
