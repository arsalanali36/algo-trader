import backtest_delta as bt, datetime as dt
exps=sorted(bt.expiries(180))
for H in [10,12]:
    rows=[(d,bt.run(d,H,"fly",0,2000)) for d in exps]
    p=[(d,r) for d,r in rows if r is not None]
    vals=[r for _,r in p]
    vals_s=sorted(vals)
    print(f"\n=== Iron-Fly H={H}h  N={len(vals)} ===")
    print(f"  net={sum(vals):.0f}  mean={sum(vals)/len(vals):.1f}  win%={sum(1 for v in vals if v>0)/len(vals)*100:.0f}")
    print(f"  BEST 3 : {[f'{v:.0f}' for v in vals_s[-3:]]}")
    print(f"  WORST 5: {[(d.isoformat(),round(v)) for d,v in sorted(p,key=lambda x:x[1])[:5]]}")
    # cumulative equity + worst drawdown day
    cum=peak=mdd=0; mdd_day=None
    eq=[]
    for d,v in p:
        cum+=v; eq.append((d,cum)); peak=max(peak,cum)
        if cum-peak<mdd: mdd=cum-peak; mdd_day=d
    print(f"  final equity={cum:.0f}  maxDD={mdd:.0f} (on {mdd_day})")
    # how much does removing best/worst trade change net?
    print(f"  net w/o best trade: {sum(vals)-vals_s[-1]:.0f}   net w/o worst 3: {sum(vals)-sum(vals_s[:3]):.0f}")
    # spot move on worst days -> confirm crash exposure
    print("  worst-day spot moves:")
    for d,v in sorted(p,key=lambda x:x[1])[:3]:
        exp_ts=int(dt.datetime(d.year,d.month,d.day,12,0,tzinfo=dt.UTC).timestamp())
        s_entry=bt.spot_at(exp_ts-H*3600); s_exp=bt.spot_at(exp_ts)
        mv=(s_exp-s_entry)/s_entry*100 if s_entry else 0
        print(f"    {d}: pnl={v:.0f}  spot {s_entry:.0f}->{s_exp:.0f} ({mv:+.1f}%)")
