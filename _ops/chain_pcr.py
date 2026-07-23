#!/usr/bin/env python3
r"""
chain_pcr.py — NIFTY daily option-chain KPIs (PCR + max-pain) from NSE FO bhavcopy.

Kyun: live option-chain lake sirf ~10 din ka hai -> confluence backtest ke liye
chain history chahiye. NSE FO bhavcopy har contract ka OI deta hai -> NIFTY ka
daily PCR(OI) + max-pain reconstruct karke FII data ke saath confluence test.

Do formats (auto-detect by date):
  * <= 2024-07-05  OLD : archives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMMMYYYYbhav.csv.zip
        cols: INSTRUMENT(OPTIDX) SYMBOL(NIFTY) OPTION_TYP OPEN_INT STRIKE_PR CLOSE
  * >= 2024-07-08  UDiFF: nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip
        cols: FinInstrmTp(IDO) TckrSymb(NIFTY) OptnTp OpnIntrst StrkPric ClsPric

Output: ._TRADING DATA/FII_Flow/chain_pcr.csv  (tiny, 1 row/din)
  date, spot, pcr_oi_all, pcr_oi_near, ce_oi, pe_oi, max_pain_near, mp_dist_pct
  (mp_dist_pct = (spot - max_pain)/spot*100 ; +ve = spot above max-pain = downward pull)

Usage:
  python chain_pcr.py --from 2020-01-01
  python chain_pcr.py --from 2020-01-01 --to 2024-06-30
  python chain_pcr.py --daily
"""
import os, io, csv, sys, time, zipfile, argparse, threading
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

_TLS = threading.local()


def _tls_session():
    s = getattr(_TLS, "sess", None)
    if s is None:
        s = new_session()
        _TLS.sess = s
    return s

_TD = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA"
if not os.path.isdir(_TD):
    _TD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_TRADING_DATA")
OUT = os.path.join(_TD, "FII_Flow", "chain_pcr.csv")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MON = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"]
UDIFF_CUTOVER = dt.date(2024, 7, 8)
FIELDS = ["date","spot","pcr_oi_all","pcr_oi_near","ce_oi","pe_oi","max_pain_near","mp_dist_pct"]


def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*"})
    try: s.get("https://www.nseindia.com", timeout=15)
    except Exception: pass
    return s


def _url(d):
    if d >= UDIFF_CUTOVER:
        return ("https://nsearchives.nseindia.com/content/fo/"
                "BhavCopy_NSE_FO_0_0_0_%s_F_0000.csv.zip" % d.strftime("%Y%m%d"))
    return ("https://archives.nseindia.com/content/historical/DERIVATIVES/"
            "%d/%s/fo%s%sbhav.csv.zip" % (d.year, MON[d.month-1],
                                          d.strftime("%d"), MON[d.month-1] + str(d.year)))


def _download_csv(sess, d, retries=2):
    url = _url(d)
    for a in range(retries + 1):
        try:
            r = sess.get(url, timeout=35)
            if r.status_code == 200 and r.content[:2] == b"PK":
                z = zipfile.ZipFile(io.BytesIO(r.content))
                return z.read(z.namelist()[0]).decode("utf-8", "replace")
            if r.status_code == 404:
                return None
        except Exception:
            pass
        time.sleep(1.0 * (a + 1))
    return None


def _nifty_rows(text, d):
    """Yield (expiry_str, opt_type, strike float, oi int, spot-or-None) for NIFTY index options."""
    rows = csv.DictReader(text.splitlines())
    udiff = d >= UDIFF_CUTOVER
    for x in rows:
        if udiff:
            if x.get("FinInstrmTp") != "IDO" or x.get("TckrSymb") != "NIFTY":
                continue
            ot = x.get("OptnTp", "")
            if ot not in ("CE", "PE"): continue
            try:
                yield (x["XpryDt"], ot, float(x["StrkPric"]),
                       int(float(x["OpnIntrst"] or 0)),
                       float(x["UndrlygPric"]) if x.get("UndrlygPric") else None)
            except (ValueError, KeyError):
                continue
        else:
            if x.get("INSTRUMENT") != "OPTIDX" or x.get("SYMBOL") != "NIFTY":
                continue
            ot = x.get("OPTION_TYP", "")
            if ot not in ("CE", "PE"): continue
            try:
                yield (x["EXPIRY_DT"], ot, float(x["STRIKE_PR"]),
                       int(float(x["OPEN_INT"] or 0)), None)
            except (ValueError, KeyError):
                continue


def _parse_expiry(s):
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y"):
        try: return dt.datetime.strptime(s, fmt).date()
        except ValueError: pass
    return None


def compute_day(text, d, spot_fallback=None):
    """Return summary dict for date d, or None."""
    ce_all = pe_all = 0
    spot = None
    by_exp = {}  # expiry_date -> {'CE':{strike:oi}, 'PE':{strike:oi}}
    for exp_s, ot, strike, oi, sp in _nifty_rows(text, d):
        if ot == "CE": ce_all += oi
        else: pe_all += oi
        if sp: spot = sp
        ed = _parse_expiry(exp_s)
        if ed is None: continue
        rec = by_exp.setdefault(ed, {"CE": {}, "PE": {}})
        rec[ot][strike] = rec[ot].get(strike, 0) + oi
    if ce_all == 0 and pe_all == 0:
        return None
    if spot is None:
        spot = spot_fallback
    # nearest expiry >= d (else the closest)
    future = sorted(e for e in by_exp if e >= d)
    near = future[0] if future else (sorted(by_exp)[-1] if by_exp else None)
    pcr_near = None; max_pain = None; mp_dist = None
    if near:
        ce = by_exp[near]["CE"]; pe = by_exp[near]["PE"]
        ce_n = sum(ce.values()); pe_n = sum(pe.values())
        pcr_near = round(pe_n / ce_n, 4) if ce_n else None
        # max-pain: strike minimizing total writer payout (sum over both sides)
        strikes = sorted(set(ce) | set(pe))
        best = None
        for K in strikes:
            pain = 0.0
            for k2, oi in ce.items():
                if k2 < K: pain += (K - k2) * oi       # ITM calls
            for k2, oi in pe.items():
                if k2 > K: pain += (k2 - K) * oi       # ITM puts
            if best is None or pain < best[1]:
                best = (K, pain)
        if best: max_pain = best[0]
        if max_pain and spot:
            mp_dist = round(100.0 * (spot - max_pain) / spot, 3)
    return {
        "date": d.strftime("%Y-%m-%d"),
        "spot": round(spot, 2) if spot else "",
        "pcr_oi_all": round(pe_all / ce_all, 4) if ce_all else "",
        "pcr_oi_near": pcr_near if pcr_near is not None else "",
        "ce_oi": ce_all, "pe_oi": pe_all,
        "max_pain_near": max_pain if max_pain else "",
        "mp_dist_pct": mp_dist if mp_dist is not None else "",
    }


def _load():
    if not os.path.exists(OUT): return {}
    return {r["date"]: r for r in csv.DictReader(open(OUT))}


def _save(d):
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader()
        for k in sorted(d): w.writerow({fld: d[k].get(fld, "") for fld in FIELDS})


def _load_nifty_spot():
    """date -> close from Index/NIFTY per-day files, as spot fallback for old bhavcopy."""
    import glob
    out = {}
    nd = os.path.join(_TD, "Index", "NIFTY")
    for p in glob.glob(os.path.join(nd, "NIFTY_*.csv")):
        di = os.path.basename(p)[len("NIFTY_"):-4]
        try:
            last = None
            for row in csv.reader(open(p)):
                if len(row) >= 5 and row[4] and row[4] != "Close": last = row[4]
            if last: out[di] = float(last)
        except Exception: pass
    return out


def backfill(start, end):
    sess = new_session()
    spots = _load_nifty_spot()
    data = _load()
    d = start; n = got = miss = 0
    while d <= end:
        if d.weekday() < 5:
            di = d.strftime("%Y-%m-%d")
            if di in data and data[di].get("ce_oi"):
                pass
            else:
                text = _download_csv(sess, d)
                if text:
                    summ = compute_day(text, d, spots.get(di))
                    if summ:
                        data[di] = summ; got += 1
                        if got % 25 == 0: _save(data)
                    else:
                        miss += 1
                else:
                    miss += 1
                n += 1
                time.sleep(0.4)
                if n % 150 == 0:
                    _save(data); sess = new_session()
                    print("  ...%d req, %d saved (at %s)" % (n, got, di))
        d += dt.timedelta(days=1)
    _save(data)
    print("chain backfill done: %d saved, %d miss/holiday. -> %s" % (got, miss, OUT))


def _fetch_one(d, spot_fallback):
    """Thread worker: download+parse one day. Returns (iso, summary or None)."""
    sess = _tls_session()
    text = _download_csv(sess, d)
    if not text:
        return (d.strftime("%Y-%m-%d"), None)
    return (d.strftime("%Y-%m-%d"), compute_day(text, d, spot_fallback))


def backfill_parallel(start, end, workers=6):
    spots = _load_nifty_spot()
    data = _load()
    todo = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            di = d.strftime("%Y-%m-%d")
            if not (di in data and data[di].get("ce_oi")):
                todo.append(d)
        d += dt.timedelta(days=1)
    print("parallel: %d days to fetch, %d workers" % (len(todo), workers))
    got = miss = done = 0
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_fetch_one, dd, spots.get(dd.strftime("%Y-%m-%d"))): dd
                for dd in todo}
        for fut in as_completed(futs):
            di, summ = fut.result()
            done += 1
            with lock:
                if summ:
                    data[di] = summ; got += 1
                else:
                    miss += 1
                if got and got % 50 == 0:
                    _save(data)
            if done % 100 == 0:
                print("  ...%d/%d done, %d saved" % (done, len(todo), got))
    _save(data)
    print("chain parallel backfill done: %d saved, %d miss/holiday -> %s" % (got, miss, OUT))


def daily():
    sess = new_session(); spots = _load_nifty_spot(); data = _load()
    today = dt.date.today()
    for back in range(0, 6):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5: continue
        text = _download_csv(sess, d)
        if not text: continue
        summ = compute_day(text, d, spots.get(d.strftime("%Y-%m-%d")))
        if summ:
            data[summ["date"]] = summ; _save(data)
            print("chain daily OK:", summ["date"], "PCR", summ["pcr_oi_near"], "MP-dist", summ["mp_dist_pct"])
            return
    print("chain daily: no trading day found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2020-01-01")
    ap.add_argument("--to", dest="to", default=None)
    ap.add_argument("--daily", action="store_true")
    ap.add_argument("--workers", type=int, default=6, help="parallel downloads (1 = serial)")
    a = ap.parse_args()
    if a.daily:
        daily(); return
    start = dt.datetime.strptime(a.frm, "%Y-%m-%d").date()
    end = dt.datetime.strptime(a.to, "%Y-%m-%d").date() if a.to else dt.date.today()
    print("Chain PCR backfill %s -> %s  (workers=%d)" % (start, end, a.workers))
    if a.workers > 1:
        backfill_parallel(start, end, workers=a.workers)
    else:
        backfill(start, end)


if __name__ == "__main__":
    main()
