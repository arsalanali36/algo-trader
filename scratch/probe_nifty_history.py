"""One-off probe: how far back does Dhan serve NIFTY 1-min intraday candles?
Read-only, no orders, no cost. Tries a few past dates and reports bar counts."""
import json, os, sys, datetime, time
import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg  = json.load(open(os.path.join(BASE, "data", "config.json")))
HDRS = {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"],
        "Content-Type": "application/json"}

NIFTY = {"securityId": "13", "exchangeSegment": "IDX_I",
         "instrument": "INDEX", "expiryCode": 0}

def bars_on(date_str):
    body = dict(NIFTY, fromDate=date_str, toDate=date_str)
    try:
        r = requests.post("https://api.dhan.co/v2/charts/intraday",
                          headers=HDRS, json=body, timeout=20)
    except Exception as e:
        return f"ERR {e}"
    if r.status_code != 200:
        return f"HTTP {r.status_code}: {r.text[:120]}"
    d = r.json()
    if isinstance(d, dict) and d.get("open"):
        n = len(d["open"])
        ts0 = datetime.datetime.fromtimestamp(d["timestamp"][0]).strftime("%Y-%m-%d %H:%M")
        return f"{n} bars (first {ts0})"
    return f"no data ({str(d)[:100]})"

# a weekday roughly N years / months back (skip weekends crudely)
def past(days):
    dt = datetime.date.today() - datetime.timedelta(days=days)
    while dt.weekday() >= 5:  # Sat/Sun -> step back
        dt -= datetime.timedelta(days=1)
    return dt.strftime("%Y-%m-%d")

targets = [
    ("~1 month",  30),  ("~6 months", 182), ("~1 year",  365),
    ("~1.5 year", 548), ("~2 years",  730), ("~2.5 year",913),
    ("~3 years", 1095), ("~3.5 year",1278), ("~4 years", 1460),
    ("~4.5 year",1643), ("~5 years", 1825),
]
print("Probing Dhan NIFTY 1-min intraday history (today =", datetime.date.today(), ")\n")
for label, days in targets:
    ds = past(days)
    res = bars_on(ds)
    print(f"  {label:11s} {ds}  ->  {res}")
    time.sleep(0.8)  # be gentle on rate limit
print("\nDone.")
