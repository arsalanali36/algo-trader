"""Download BANKNIFTY 1-min spot history (Dhan secId 25, IDX_I) → bnf_1min.csv.
Same 90-day-chunk approach as data_fetch.py. Free API, read-only."""
import json, os, sys, time, datetime
import requests
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
cfg = json.load(open(os.path.join(BASE, "data", "config.json")))
HDRS = {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"], "Content-Type": "application/json"}
BNF = {"securityId": "25", "exchangeSegment": "IDX_I", "instrument": "INDEX", "expiryCode": 0}
OUT = os.path.join(HERE, "bnf_1min.csv")
S0, S1 = datetime.time(9, 15), datetime.time(15, 29)


def chunk(fr, to):
    r = requests.post("https://api.dhan.co/v2/charts/intraday", headers=HDRS,
                      json=dict(BNF, fromDate=str(fr), toDate=str(to)), timeout=40)
    r.raise_for_status()
    d = r.json()
    if not (isinstance(d, dict) and d.get("open")):
        return pd.DataFrame()
    df = pd.DataFrame({"Datetime": [datetime.datetime.fromtimestamp(t) for t in d["timestamp"]],
                       "Open": d["open"], "High": d["high"], "Low": d["low"],
                       "Close": d["close"], "Volume": d.get("volume", [0]*len(d["open"]))})
    t = df.Datetime.dt.time
    return df[(df.Datetime.dt.weekday < 5) & (t >= S0) & (t <= S1)]


def main(start="2022-01-01"):
    cur = datetime.date.fromisoformat(start); end = datetime.date.today()
    frames = []; i = 0
    while cur <= end:
        to = min(cur + datetime.timedelta(days=89), end)
        try:
            df = chunk(cur, to); i += 1
            print(f"  [{i:2d}] {cur}..{to} -> {len(df)} bars", flush=True)
            if len(df): frames.append(df)
        except Exception as e:
            print(f"  [{i:2d}] {cur}..{to} ERR {e}", flush=True)
        cur = to + datetime.timedelta(days=1); time.sleep(1.1)
    raw = pd.concat(frames, ignore_index=True).drop_duplicates("Datetime").sort_values("Datetime").reset_index(drop=True)
    raw.to_csv(OUT, index=False)
    print(f"BNF 1-min: {len(raw):,} bars {raw.Datetime.iloc[0].date()}..{raw.Datetime.iloc[-1].date()} -> {OUT}")


if __name__ == "__main__":
    main()
