#!/usr/bin/env python3
r"""
fii_flow.py — FII/DII "big player" positioning data-lake (NSE, free, EOD).

Kyun: Sensibull ka FII/DII participant + cash data actually NSE ka free public
report hai. Isko roz apne lake me le aao -> apni thesis (big-player direction) ko
option-chain data ke saath confluence me daal ke next-day bias banaya ja sake.

Sources (dono free, koi login/whitelist nahi):
  * Participant-wise OI : archives.nseindia.com/content/nsccl/fao_participant_oi_DDMMYYYY.csv
      -> FII/DII/Pro/Client ke Future Index + Option Index Call/Put Long/Short (contracts)
      -> 2015 tak historical available (~10 saal). Har file ~1KB.
  * FII/DII Cash        : nseindia.com/api/fiidiiTradeReact  (sirf CURRENT day)
      -> cash buy/sell/net (Rs Cr). Deep history nahi milti -> roz aage capture.

NSE anti-bot: pehle homepage GET karke cookie warm karo, phir archives hit karo.

Output lake (._TRADING DATA/FII_Flow/):
  raw_oi/participant_oi_YYYY-MM-DD.csv   <- raw NSE file (idempotent, skip-if-exists)
  cash/fii_dii_cash.csv                  <- append-only daily cash (forward)
  fii_flow.csv                           <- NORMALIZED master, 1 row/din, computed KPIs

Usage:
  python fii_flow.py --daily                       # aaj (ya last trading day) ka OI + cash
  python fii_flow.py --backfill --from 2015-01-01  # deep history OI (cash current-only)
  python fii_flow.py --backfill --from 2024-01-01 --to 2026-07-23
  python fii_flow.py --rebuild-master              # raw_oi/* -> fii_flow.csv dobara
  python fii_flow.py --dry-run --backfill --from 2026-07-01   # plan only

Display/data-only. Koi order/risk/live path nahi.
"""

import os
import sys
import csv
import json
import time
import argparse
import datetime as dt

import requests

# ---------------- paths ----------------
_WIN_LAKE = r"D:\KHAZANA\KHAZANA\PYTHON\._TRADING DATA\FII_Flow"
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# os.name guard REQUIRED on Linux — else a literal "D:\..." dir gets created and
# self-perpetuates the wrong lake path (see chain_pcr.py note; live 2026-07-30).
if os.name == "nt" and os.path.isdir(os.path.dirname(_WIN_LAKE)):
    LAKE = _WIN_LAKE
else:
    LAKE = os.path.join(_HERE, "_TRADING_DATA", "FII_Flow")

RAW_OI_DIR = os.path.join(LAKE, "raw_oi")
CASH_DIR   = os.path.join(LAKE, "cash")
CASH_CSV   = os.path.join(CASH_DIR, "fii_dii_cash.csv")
MASTER_CSV = os.path.join(LAKE, "fii_flow.csv")
for d in (RAW_OI_DIR, CASH_DIR):
    os.makedirs(d, exist_ok=True)

OI_URL   = "https://archives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv"
CASH_URL = "https://www.nseindia.com/api/fiidiiTradeReact"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

PARTICIPANTS = ("Client", "DII", "FII", "Pro")

# raw NSE column order (after "Client Type")
_OI_COLS = [
    "fut_idx_long", "fut_idx_short", "fut_stk_long", "fut_stk_short",
    "opt_idx_ce_long", "opt_idx_pe_long", "opt_idx_ce_short", "opt_idx_pe_short",
    "opt_stk_ce_long", "opt_stk_pe_long", "opt_stk_ce_short", "opt_stk_pe_short",
    "total_long", "total_short",
]


# ---------------- session ----------------
def new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*",
                      "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get("https://www.nseindia.com", timeout=15)
    except Exception:
        pass
    return s


# ---------------- download ----------------
def _iso(d):  # date -> "YYYY-MM-DD"
    return d.strftime("%Y-%m-%d")


def raw_oi_path(d):
    return os.path.join(RAW_OI_DIR, "participant_oi_%s.csv" % _iso(d))


def download_oi_day(sess, d, retries=2):
    """Return raw CSV text for date d, or None if no data (holiday/weekend/missing)."""
    url = OI_URL.format(ddmmyyyy=d.strftime("%d%m%Y"))
    for attempt in range(retries + 1):
        try:
            r = sess.get(url, timeout=25)
            if r.status_code == 200 and "Participant wise Open Interest" in r.text:
                return r.text
            if r.status_code == 404:
                return None  # holiday / no report that day
        except Exception:
            pass
        time.sleep(1.2 * (attempt + 1))
        if attempt == 0:  # re-warm cookies once
            try:
                sess.get("https://www.nseindia.com", timeout=15)
            except Exception:
                pass
    return None


def parse_oi(text):
    """Raw participant-OI CSV text -> {participant: {col: int}}. Robust to spaces."""
    out = {}
    for row in csv.reader(text.splitlines()):
        if not row:
            continue
        key = row[0].strip()
        if key in PARTICIPANTS and len(row) >= 15:
            vals = {}
            for i, col in enumerate(_OI_COLS):
                cell = (row[i + 1] or "").strip().replace(",", "")
                try:
                    vals[col] = int(float(cell)) if cell else 0
                except ValueError:
                    vals[col] = 0
            out[key] = vals
    return out if len(out) == 4 else None


def fetch_cash(sess):
    """Current-day FII/DII cash. Returns {'date','fii_net','dii_net',...} or None."""
    try:
        r = sess.get(CASH_URL, timeout=20,
                     headers={"Referer": "https://www.nseindia.com/reports/fii-dii"})
        if r.status_code != 200:
            return None
        rows = r.json()
    except Exception:
        return None
    rec = {}
    for it in rows:
        cat = it.get("category", "")
        net = float(it.get("netValue", 0) or 0)
        rec["date"] = it.get("date")
        if cat.startswith("FII"):
            rec["fii_cash_net"] = net
        elif cat.startswith("DII"):
            rec["dii_cash_net"] = net
    return rec or None


# ---------------- KPIs ----------------
def _lsr(long_, short_):
    tot = long_ + short_
    return round(100.0 * long_ / tot, 2) if tot else None


def compute_kpis(oi, cash=None):
    """Per-participant contracts -> one flat dict of the KPIs we care about."""
    k = {}
    for p in PARTICIPANTS:
        v = oi[p]
        pl = p.lower()
        # index futures
        k["%s_fut_idx_long" % pl]  = v["fut_idx_long"]
        k["%s_fut_idx_short" % pl] = v["fut_idx_short"]
        k["%s_fut_idx_net" % pl]   = v["fut_idx_long"] - v["fut_idx_short"]
        k["%s_fut_idx_lsr" % pl]   = _lsr(v["fut_idx_long"], v["fut_idx_short"])
        # index options net (long - short) for CE and PE
        k["%s_opt_idx_ce_net" % pl] = v["opt_idx_ce_long"] - v["opt_idx_ce_short"]
        k["%s_opt_idx_pe_net" % pl] = v["opt_idx_pe_long"] - v["opt_idx_pe_short"]
    # cash (may be absent for historical days)
    if cash:
        k["fii_cash_net"] = cash.get("fii_cash_net")
        k["dii_cash_net"] = cash.get("dii_cash_net")
    return k


MASTER_FIELDS = ["date"] + [
    "%s_%s" % (p.lower(), c)
    for p in PARTICIPANTS
    for c in ("fut_idx_long", "fut_idx_short", "fut_idx_net", "fut_idx_lsr",
              "opt_idx_ce_net", "opt_idx_pe_net")
] + ["fii_cash_net", "dii_cash_net"]


# ---------------- master build ----------------
def _read_master():
    if not os.path.exists(MASTER_CSV):
        return {}
    with open(MASTER_CSV, newline="") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def _write_master(rows_by_date):
    with open(MASTER_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_FIELDS)
        w.writeheader()
        for date in sorted(rows_by_date):
            row = rows_by_date[date]
            w.writerow({fld: row.get(fld, "") for fld in MASTER_FIELDS})


def upsert_master_from_raw(date_iso, cash=None):
    """Read one raw_oi file, compute KPIs, upsert into master. Returns True if written."""
    path = os.path.join(RAW_OI_DIR, "participant_oi_%s.csv" % date_iso)
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8", errors="replace") as f:
        oi = parse_oi(f.read())
    if not oi:
        return False
    row = {"date": date_iso}
    row.update(compute_kpis(oi, cash))
    master = _read_master()
    if date_iso in master and cash is None:
        # keep existing cash if re-building from raw without fresh cash
        for c in ("fii_cash_net", "dii_cash_net"):
            if master[date_iso].get(c):
                row.setdefault(c, master[date_iso][c])
    master[date_iso] = row
    _write_master(master)
    return True


# ---------------- commands ----------------
def cmd_daily(dry=False):
    sess = new_session()
    today = dt.date.today()
    cash = fetch_cash(sess)
    # walk back up to 5 days to hit the last trading day
    for back in range(0, 6):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        path = raw_oi_path(d)
        if not os.path.exists(path):
            text = download_oi_day(sess, d)
            if not text:
                continue
            if dry:
                print("[dry] would save", path)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
        # match cash date to this OI day (cash is current-day only)
        cd = None
        if cash and cash.get("date"):
            try:
                cash_iso = _iso(dt.datetime.strptime(cash["date"], "%d-%b-%Y").date())
                cd = cash if cash_iso == _iso(d) else None
            except Exception:
                cd = None
        if not dry:
            if cd:  # append raw cash log
                _append_cash(cd, _iso(d))
            upsert_master_from_raw(_iso(d), cd)
        print("daily OK:", _iso(d), "cash" if cd else "no-cash-match")
        return
    print("daily: no trading day OI found in last 6 days")


def _append_cash(cash, date_iso):
    exists = os.path.exists(CASH_CSV)
    seen = set()
    if exists:
        with open(CASH_CSV, newline="") as f:
            seen = {r["date"] for r in csv.DictReader(f)}
    if date_iso in seen:
        return
    with open(CASH_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "fii_cash_net", "dii_cash_net"])
        if not exists:
            w.writeheader()
        w.writerow({"date": date_iso,
                    "fii_cash_net": cash.get("fii_cash_net", ""),
                    "dii_cash_net": cash.get("dii_cash_net", "")})


def cmd_backfill(start, end, dry=False):
    sess = new_session()
    d = start
    got = skipped = holiday = 0
    n = 0
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri only
            path = raw_oi_path(d)
            if os.path.exists(path):
                skipped += 1
            else:
                if dry:
                    print("[dry] would fetch", _iso(d))
                    got += 1
                else:
                    text = download_oi_day(sess, d)
                    if text:
                        with open(path, "w", encoding="utf-8") as f:
                            f.write(text)
                        upsert_master_from_raw(_iso(d))
                        got += 1
                    else:
                        holiday += 1
                    n += 1
                    time.sleep(0.35)
                    if n % 200 == 0:
                        print("  ...%d requests, %d saved (at %s)" % (n, got, _iso(d)))
                        sess = new_session()  # refresh cookies periodically
        d += dt.timedelta(days=1)
    print("backfill done: %d saved, %d already-had, %d holiday/missing" %
          (got, skipped, holiday))


def cmd_rebuild_master():
    """Recompute master in ONE pass (O(n), not O(n^2))."""
    master = {}
    for fn in sorted(os.listdir(RAW_OI_DIR)):
        if not (fn.startswith("participant_oi_") and fn.endswith(".csv")):
            continue
        date_iso = fn[len("participant_oi_"):-4]
        with open(os.path.join(RAW_OI_DIR, fn), encoding="utf-8", errors="replace") as f:
            oi = parse_oi(f.read())
        if not oi:
            continue
        row = {"date": date_iso}
        row.update(compute_kpis(oi))
        master[date_iso] = row
    # preserve any cash already captured in existing master
    for date_iso, old in _read_master().items():
        if date_iso in master:
            for c in ("fii_cash_net", "dii_cash_net"):
                if old.get(c):
                    master[date_iso][c] = old[c]
    _write_master(master)
    print("master rebuilt from %d raw files -> %s" % (len(master), MASTER_CSV))


def _parse_date(s):
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def main():
    ap = argparse.ArgumentParser(description="FII/DII participant + cash data-lake (NSE)")
    ap.add_argument("--daily", action="store_true", help="fetch last trading day")
    ap.add_argument("--backfill", action="store_true", help="fetch a date range of OI")
    ap.add_argument("--rebuild-master", action="store_true",
                    help="recompute fii_flow.csv from raw_oi/*")
    ap.add_argument("--from", dest="frm", default="2015-01-01")
    ap.add_argument("--to", dest="to", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.rebuild_master:
        cmd_rebuild_master()
    elif a.backfill:
        start = _parse_date(a.frm)
        end = _parse_date(a.to) if a.to else dt.date.today()
        print("Backfill OI %s -> %s into %s" % (start, end, RAW_OI_DIR))
        cmd_backfill(start, end, dry=a.dry_run)
    elif a.daily:
        cmd_daily(dry=a.dry_run)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
