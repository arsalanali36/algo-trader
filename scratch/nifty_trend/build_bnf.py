"""#07 (take-3) — BANKNIFTY hunt: run the FULL validated pipeline on bnf_1min.csv.

Same machinery as every NIFTY hunt (screen all designs incl. chain_zone -> optimize
min(train,OOS) -> rotation significance -> 3-pass BS dashboard), with BANKNIFTY-correct
market spec patched in:
  - data           : bnf_1min.csv (2022-01 -> today, 4.5yr)
  - strike step    : 100 (NIFTY=50)
  - lot size       : from Dhan scrip master ("BANKNIFTY"), fallback 30 (labelled)
  - expiry T (BS)  : weekly Thu -> Wed (NSE cir 119/2023, eff 04-Sep-2023) until the
                     last weekly 13-Nov-2024; MONTHLY (last Thu / last Tue from Sep-2025)
                     after — via expiry_calendar.banknifty_next_expiry (official circulars)
  - expiry-day skip: exact is_banknifty_expiry_day (post-Nov-24 only the monthly day)

Rationale: proven edge family here is directional spot-signals -> ATM option BUY (#01-05,
all NIFTY). A different UNDERLYING is real diversification; BNF options are the most
liquid after NIFTY. Usage: python build_bnf.py [--tf 15m,5m] [--trials 250]
"""
import sys
import datetime as dt
import pandas as pd

import expiry_calendar as xcal
import bs_option as bs
import intraday_engine as ie
import run_hunt as rh

BNF_CSV = "bnf_1min.csv"
BNF_STEP = 100


# ---- patches (BANKNIFTY market spec) ----
_orig_load = ie.load_1m
def _load_bnf(csv=BNF_CSV):
    return _orig_load(BNF_CSV)
ie.load_1m = _load_bnf

bs.STRIKE_STEP = BNF_STEP

def _bnf_lot(default=30):
    try:
        import os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from _data import dhan_master
        res = dhan_master.get_option_contract("BANKNIFTY", 52000, "CE")
        if res and res[2]:
            return int(res[2])
    except Exception as e:
        print(f"[bnf] lot fallback = {default} ({e})", flush=True)
    return default
bs.get_nifty_lot = _bnf_lot

def _bnf_tte(ts):
    """time-to-expiry (years) to the nearest BANKNIFTY expiry (weekly era Thu/Wed;
    monthly-only after 2024-11-20), expiring 15:30 IST."""
    ts = pd.Timestamp(ts)
    ed = xcal.banknifty_next_expiry(ts.date())
    exp = pd.Timestamp(dt.datetime.combine(ed, dt.time(15, 30)))
    if ts > exp:
        ed = xcal.banknifty_next_expiry(ts.date() + dt.timedelta(days=1))
        exp = pd.Timestamp(dt.datetime.combine(ed, dt.time(15, 30)))
    return max((exp - ts).total_seconds(), 0.0) / (365.0 * 86400.0)
bs.tte_years = _bnf_tte

# expiry-day skip flag (intraday_engine compares weekday == weekly_expiry_weekday(d))
xcal.weekly_expiry_weekday = lambda d: (d.weekday() if xcal.is_banknifty_expiry_day(d) else -1)

# intraday candles / overlays read the NIFTY csv by filename — repoint to BNF
try:
    import add_intraday
    _orig_bi = add_intraday.build_intraday
    add_intraday.build_intraday = lambda csv, dates: _orig_bi(BNF_CSV, dates)
except Exception:
    pass
try:
    import add_overlays
    _orig_bo = add_overlays.build_overlays
    add_overlays.build_overlays = lambda design, dna, tf, trades, csv: _orig_bo(design, dna, tf, trades, BNF_CSV)
except Exception:
    pass

# titles: mark the underlying
for k in list(rh.DESIGN_TITLE):
    rh.DESIGN_TITLE[k] = rh.DESIGN_TITLE[k] + " [BANKNIFTY]"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="15m,5m")
    ap.add_argument("--trials", type=int, default=250)
    ap.add_argument("--name", default="banknifty_hunt")
    args = ap.parse_args()
    sys.argv = ["run_hunt.py", "--tf", args.tf, "--trials", str(args.trials), "--name", args.name]
    rh.main()
    # post-fix cosmetic 'NIFTY' -> 'BANKNIFTY' in the written meta/design strings
    import os, json
    folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs", args.name)
    for fn in ("meta.json", "results.js"):
        p = os.path.join(folder, fn)
        if not os.path.exists(p):
            continue
        txt = open(p, encoding="utf-8").read()
        txt = txt.replace("— NIFTY ", "— BANKNIFTY ").replace('"instrument": "NIFTY 50"', '"instrument": "BANKNIFTY"')
        open(p, "w", encoding="utf-8").write(txt)
    idxp = os.path.join(os.path.dirname(folder), "index.json")
    idx = json.load(open(idxp))
    for row in idx:
        if row.get("slug") == args.name:
            row["instrument"] = "BANKNIFTY"
    json.dump(idx, open(idxp, "w"), indent=2)
    print("[bnf] labels fixed", flush=True)


if __name__ == "__main__":
    main()
