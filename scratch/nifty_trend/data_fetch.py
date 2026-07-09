"""Step 1 — NIFTY 1-min data, data-lake backed & incremental.

Reads the canonical per-day store first. Only the trading days that are MISSING
get downloaded from Dhan (<=90-day chunks over gaps), written back as per-day
CSVs, so the second run downloads nothing. Then rebuilds the consolidated
nifty_1min.csv + nifty_1h.csv the backtester reads.

    python data_fetch.py                 # fill store up to today, rebuild frames
    python data_fetch.py 2022-01-01      # ensure history back to this date
    python data_fetch.py --seed          # one-time: import existing nifty_1min.csv into store
"""
import json, os, sys, time, datetime
import requests
import pandas as pd
import datalake as dl

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
cfg  = json.load(open(os.path.join(BASE, "data", "config.json")))
HDRS = {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"],
        "Content-Type": "application/json"}
NIFTY = {"securityId": "13", "exchangeSegment": "IDX_I",
         "instrument": "INDEX", "expiryCode": 0}
RAW_CSV = os.path.join(HERE, "nifty_1min.csv")
H1_CSV  = os.path.join(HERE, "nifty_1h.csv")
SESS_START, SESS_END = datetime.time(9, 15), datetime.time(15, 29)


def fetch_chunk(fr, to):
    body = dict(NIFTY, fromDate=str(fr), toDate=str(to))
    r = requests.post("https://api.dhan.co/v2/charts/intraday", headers=HDRS, json=body, timeout=40)
    r.raise_for_status()
    d = r.json()
    if not (isinstance(d, dict) and d.get("open")):
        return pd.DataFrame(columns=dl.COLS)
    df = pd.DataFrame({
        "Datetime": [datetime.datetime.fromtimestamp(t) for t in d["timestamp"]],
        "Open": d["open"], "High": d["high"], "Low": d["low"],
        "Close": d["close"], "Volume": d.get("volume", [0] * len(d["open"])),
    })
    t = df.Datetime.dt.time
    return df[(df.Datetime.dt.weekday < 5) & (t >= SESS_START) & (t <= SESS_END)]


def _chunks(days, size=85):
    """Group missing ISO-date strings into contiguous <=`size`-day download windows."""
    if not days:
        return []
    days = sorted(days)
    out, s = [], days[0]
    prev = datetime.date.fromisoformat(s)
    for d in days[1:]:
        dd = datetime.date.fromisoformat(d)
        if (dd - datetime.date.fromisoformat(s)).days >= size:
            out.append((s, prev.isoformat())); s = d
        prev = dd
    out.append((s, days[-1]))
    return out


def rebuild_frames(start=None):
    raw = dl.load_all(start=start)
    if raw.empty:
        print("store empty — nothing to rebuild"); return
    raw.to_csv(RAW_CSV, index=False)
    s = raw.set_index("Datetime")
    h1 = (s.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                "Close": "last", "Volume": "sum"}).dropna())
    h1 = h1[h1.Open > 0].reset_index()
    h1 = h1[(h1.Datetime.dt.time >= datetime.time(9, 0)) & (h1.Datetime.dt.time <= datetime.time(15, 30))]
    h1.to_csv(H1_CSV, index=False)
    print(f"rebuilt: {len(raw):,} 1-min bars, {len(h1):,} 1H bars  "
          f"({raw.Datetime.iloc[0].date()} .. {raw.Datetime.iloc[-1].date()})")


def seed_from_local():
    if not os.path.isfile(RAW_CSV):
        print("no local nifty_1min.csv to seed from"); return
    df = pd.read_csv(RAW_CSV)
    n = dl.write_days(df)
    print(f"seeded {n} per-day files into store: {dl.STORE}")


def _forward_windows(start, end):
    """Only the contiguous tail after the latest present day needs downloading.
    Interior gaps in history are market holidays (permanently empty) — skip them,
    else every run re-pulls ranges around them. Backfill uses --backfill instead."""
    present = dl.present_days()
    have_in_range = sorted(d for d in present if start <= d <= end)
    if not have_in_range:
        return [(start, end)]                     # empty store -> full pull
    last = have_in_range[-1]
    nxt = (datetime.date.fromisoformat(last) + datetime.timedelta(days=1)).isoformat()
    if nxt > end:
        return []                                 # already up to date
    return _chunks(dl.missing_weekdays(nxt, end))


def main(start="2022-01-01", backfill=False):
    end = datetime.date.today().isoformat()
    if backfill:
        windows = _chunks(dl.missing_weekdays(start, end))
        print(f"BACKFILL {start}..{end}  missing weekdays: "
              f"{len(dl.missing_weekdays(start, end))}")
    else:
        windows = _forward_windows(start, end)
    if not windows:
        print(f"store: {dl.STORE}\nalready up to date ({start}..{end}) — 0 downloads")
        rebuild_frames(start=start); return
    print(f"store: {dl.STORE}\ndownloading {len(windows)} window(s)")
    for i, (fr, to) in enumerate(windows, 1):
        try:
            df = fetch_chunk(fr, to)
            wrote = dl.write_days(df) if len(df) else 0
            print(f"  [{i:2d}/{len(windows)}] {fr}..{to} -> {len(df):6d} bars, {wrote} days")
        except Exception as e:
            print(f"  [{i:2d}/{len(windows)}] {fr}..{to} -> ERR {e}")
        time.sleep(1.1)
    rebuild_frames(start=start)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--seed" in args:
        seed_from_local(); rebuild_frames()
    elif "--backfill" in args:
        start = next((a for a in args if a[0].isdigit()), "2022-01-01")
        main(start, backfill=True)
    else:
        main(args[0] if args and args[0][0].isdigit() else "2022-01-01")
