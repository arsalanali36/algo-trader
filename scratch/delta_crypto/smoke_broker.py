from delta_broker import DeltaBroker
b = DeltaBroker()   # no creds
print("name:", b.name(), "| has_creds:", b.has_creds())

# public: live BTC perpetual quote
q = b.quote("BTCUSD")
print("\nquote BTCUSD:", {k:q[k] for k in ('ltp','bid','ask','spot')})

# public: live BTC option quote (find one)
prods = b._products()
live_opt = [s for s,p in prods.items() if s.startswith("C-BTC-") and p.get("state")=="live"]
print("live BTC call options:", len(live_opt))
if live_opt:
    s = sorted(live_opt)[len(live_opt)//2]
    qo = b.quote(s)
    print(f"quote {s}: ltp={qo['ltp']} bid={qo['bid']} ask={qo['ask']} iv={qo['iv']} greeks={'yes' if qo['greeks'] else 'no'}")
    print("product_id:", b.product_id(s))

# public: candles as DataFrame
df = b.intraday_candles("BTCUSD", days=1, interval=5)
print("\ncandles BTCUSD 5m/1d: rows=", len(df))
print(df.tail(2).to_string(index=False) if hasattr(df,'tail') else df[:2])

# private without creds -> safe empty
print("\nfunds() no-creds:", b.funds())
print("positions() no-creds:", b.positions())
po = b.place_order("BUY", "BTCUSD", qty=1)
print("place_order() no-creds:", po['status'], "|", po['reason'])
