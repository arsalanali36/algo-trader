import delta_client as dc, requests, datetime as dt, time
BASE="https://api.india.delta.exchange"; S=requests.Session()
prods=[]; after=None
while True:
    p={"page_size":1000}
    if after:p["after"]=after
    j=S.get(BASE+"/v2/products",params=p,timeout=25).json()
    prods+=j["result"]; after=j.get("meta",{}).get("after")
    if not after or len(prods)>5000: break
btc=[p for p in prods if p.get("contract_type")in("call_options","put_options")
     and p.get("underlying_asset",{}).get("symbol")=="BTC"]
strikes=sorted(set(int(p["strike_price"]) for p in btc))
diffs=sorted(set(strikes[i+1]-strikes[i] for i in range(len(strikes)-1)))
print("BTC strike range:",strikes[0],"->",strikes[-1])
print("strike step(s) seen:",diffs[:8])

# validate expired-chain reconstruction over last ~20 days (weekday dailies)
print("\nExpired daily-chain reconstruction test (BTC ATM, hold-to-expiry):")
now=int(time.time())
ok=0; tried=0
for dback in range(2,22):
    d=dt.date.today()-dt.timedelta(days=dback)
    if d.weekday()>=5: continue  # skip weekend (no daily expiry Sat/Sun)
    exp_ts=int(dt.datetime(d.year,d.month,d.day,12,0,tzinfo=dt.UTC).timestamp())
    entry_ts=exp_ts-6*3600  # 6h before expiry
    spot=dc.spot_at("BTCUSD",entry_ts)
    if not spot: continue
    atm=round(spot/1000)*1000
    ce=dc.opt_symbol("C","BTC",atm,d); pe=dc.opt_symbol("P","BTC",atm,d)
    cc=dc.candles(ce,"5m",entry_ts-1800,exp_ts+300)
    pc=dc.candles(pe,"5m",entry_ts-1800,exp_ts+300)
    tried+=1
    if cc and pc:
        ok+=1
        cprem=min(cc,key=lambda x:abs(x["time"]-entry_ts))["close"]
        pprem=min(pc,key=lambda x:abs(x["time"]-entry_ts))["close"]
        # settlement premium = last bar
        cset=cc[0]["close"]; pset=pc[0]["close"]
        credit=cprem+pprem; settle=cset+pset
        print(f"  {d} ATM {atm}: spot {spot:.0f} credit={credit:.1f} settle={settle:.1f} straddle_pnl={credit-settle:+.1f}")
    time.sleep(0.03)
print(f"\nreconstruction success: {ok}/{tried} expiries")
