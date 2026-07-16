"""Step 1 — NIFTY 1-min data, data-lake backed & incremental.

Reads the canonical per-day store first. Only the trading days that are MISSING
get downloaded from Dhan (<=90-day chunks over gaps), written back as per-day
CSVs, so the second run downloads nothing. Then rebuilds the consolidated
nifty_1min.csv + nifty_1h.csv the backtester reads.

    python data_fetch.py                 # fill store up to today, rebuild frames
    python data_fetch.py 2018-01-01      # ensure history back to this date (DOWNLOAD depth only)
    python data_fetch.py --seed          # nifty_1min.csv ke MISSING din store me daalo
    python data_fetch.py --seed-overwrite  # ...aur maujood din bhi replace karo (soch ke)
    python data_fetch.py --force         # rebuild kar do chahe CSV chhoti ho jaaye

nifty_1min.csv / nifty_1h.csv **DERIVED** hain — per-day store hi source hai. Rebuild
hamesha POORA store likhta hai; date argument sirf ye batata hai ki downloader ko kitna
peeche jaana hai. Agar rebuild se CSV chhoti hoti hai to wo ruk jaata hai (--force se hi
aage badhega) — 2026-07-16 ko ek chup rebuild ne 788,410 rows ko 416,673 kar diya tha aur
4 saal ka NIFTY data sirf git ke committed blob me bacha tha.
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


SHRINK_TOLERANCE = 0.98   # naya frame purane ka 98% se kam = kuch gadbad hai


def _existing_rows(path):
    """Kitni data-rows abhi us CSV me hain (header chhod ke). Missing = 0."""
    if not os.path.isfile(path):
        return 0
    with open(path, "rb") as f:
        return max(0, sum(1 for _ in f) - 1)


def rebuild_frames(start=None, force=False):
    raw = dl.load_all(start=start)
    if raw.empty:
        print("store empty — nothing to rebuild"); return

    # ── SHRINK GUARD (2026-07-16) ───────────────────────────────────────────
    # rebuild_frames() nifty_1min.csv ko store se DERIVE karke overwrite karta
    # hai. Agar store adhoora ho, ye chupchaap saal ke saal kaat deta hai aur
    # kisi ko pata nahi chalta — har baad wala backtest chhote period pe chalta
    # hai, alag numbers deta hai, koi warning nahi.
    #
    # Exactly ye hua 2026-07-16 ko: local store 2022 se shuru hota hai, jabki
    # commit 2c57074 ("extend NIFTY data to 8.5yr (2018)") ne CSV ko 2018 tak
    # bhara tha — par 2018-2021 ke per-day files local store me kabhi aaye hi
    # nahi (wo VPS pe bane the). Ek local run ne 788,410 rows ko 416,673 pe
    # laakar 4 saal uda diye; wo data sirf git ke committed blob me bacha.
    #
    # Ab: chhota hona = ruko aur bolo. Jaan-boojh ke chhota karna ho to --force.
    old = _existing_rows(RAW_CSV)
    new = len(raw)
    if old and new < old * SHRINK_TOLERANCE and not force:
        print(f"\n  REFUSING to rebuild {os.path.basename(RAW_CSV)} — it would SHRINK.")
        print(f"    on disk now : {old:,} rows")
        print(f"    store gives : {new:,} rows   ({new/old:.0%})")
        print(f"    store range : {raw.Datetime.min()}  ->  {raw.Datetime.max()}")
        print(f"    store dir   : {dl.STORE}")
        print("\n  Matlab store me wo din hain hi nahi jo CSV me hain — CSV store se")
        print("  DERIVE hoti hai, source nahi. Aage badhne se pehle:")
        print("    * store ko bharo:  python data_fetch.py --seed   (CSV se missing din wapas)")
        print("    * ya poore din download karo:  python data_fetch.py 2018-01-01")
        print("    * sach me chhota chahiye to:   python data_fetch.py --force")
        print("  (nifty_1h.csv bhi nahi likhi — dono ek saath hi badalne chahiye)\n")
        return
    raw.to_csv(RAW_CSV, index=False)
    s = raw.set_index("Datetime")
    h1 = (s.resample("1h").agg({"Open": "first", "High": "max", "Low": "min",
                                "Close": "last", "Volume": "sum"}).dropna())
    h1 = h1[h1.Open > 0].reset_index()
    h1 = h1[(h1.Datetime.dt.time >= datetime.time(9, 0)) & (h1.Datetime.dt.time <= datetime.time(15, 30))]
    h1.to_csv(H1_CSV, index=False)
    print(f"rebuilt: {len(raw):,} 1-min bars, {len(h1):,} 1H bars  "
          f"({raw.Datetime.iloc[0].date()} .. {raw.Datetime.iloc[-1].date()})")


def seed_from_local(overwrite=False):
    """nifty_1min.csv ke din store me daalo — DEFAULT: sirf wo din jo store me nahi hain.

    Pehle ye dl.write_days(df) tha, jo CSV ke HAR din ko overwrite kar deta tha.
    Wo ulta chal sakta hai: CSV store se derive hoti hai aur purani ho sakti hai
    (2026-07-16: committed CSV ka aakhri bar 2026-07-09 ka tha, jabki store me
    07-16 tak ke din the), to blunt seed taaza din ko purane se replace kar deta.
    Ab default = sirf missing din bharo (yahi ka yahi use-case hai: git ki CSV se
    2018-2021 wapas laana, jo per-day store me kabhi aaye hi nahi).
    """
    if not os.path.isfile(RAW_CSV):
        print("no local nifty_1min.csv to seed from"); return
    df = pd.read_csv(RAW_CSV)
    df["Datetime"] = pd.to_datetime(df["Datetime"])
    days = sorted(df["Datetime"].dt.date.unique())
    todo = days if overwrite else [d for d in days if not dl.has_day(str(d))]
    skipped = len(days) - len(todo)
    if not todo:
        print(f"store already has all {len(days)} days from the CSV — nothing to seed")
        return
    n = dl.write_days(df[df["Datetime"].dt.date.isin(set(todo))])
    print(f"seeded {n} per-day files into store: {dl.STORE}")
    print(f"  range   : {min(todo)}  ->  {max(todo)}")
    if skipped:
        print(f"  skipped : {skipped} day(s) already present (use --seed-overwrite to replace)")


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
    """start = kahan tak PEECHE ka data DOWNLOAD karna hai — CSV me kya likhna hai
    wo NAHI. Pehle dono ek hi the: main() `rebuild_frames(start=start)` bulata tha,
    aur load_all(start=) us date se pehle ka sab kaat deta hai — to bina argument ke
    ek `python data_fetch.py` (default 2022-01-01) CSV se 2018-2021 uda deta tha,
    chahe store me wo din maujood hon. Ab rebuild hamesha POORA store likhta hai;
    start sirf downloader ke liye hai."""
    end = datetime.date.today().isoformat()
    if backfill:
        windows = _chunks(dl.missing_weekdays(start, end))
        print(f"BACKFILL {start}..{end}  missing weekdays: "
              f"{len(dl.missing_weekdays(start, end))}")
    else:
        windows = _forward_windows(start, end)
    if not windows:
        print(f"store: {dl.STORE}\nalready up to date ({start}..{end}) — 0 downloads")
        rebuild_frames(); return
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
    if "--seed" in args or "--seed-overwrite" in args:
        seed_from_local(overwrite="--seed-overwrite" in args)
        rebuild_frames(force="--force" in args)
    elif "--force" in args:
        rebuild_frames(force=True)
    elif "--backfill" in args:
        start = next((a for a in args if a[0].isdigit()), "2022-01-01")
        main(start, backfill=True)
    else:
        main(args[0] if args and args[0][0].isdigit() else "2022-01-01")
