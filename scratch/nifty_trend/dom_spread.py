"""
dom_spread.py — REAL bid/ask spread + walk-the-book slippage measurement
from brother's 20-level order-book (DOM) data.

WHY: every option backtest here prices fills with a FLAT slippage knob
(0.5% in real_struct2 / BS charge model). We have never measured the real
number. This tool reads the DOM zips (ATM CE / ATM PE / near FUT, ~8 snaps/sec,
full session) and computes, per instrument, per time-of-day bucket, per premium
bucket:
  - quoted spread  (ask_l1 - bid_l1)  in Rs and as % of mid
  - half-spread %  (mid -> ask, i.e. the one-way cost of crossing at L1)
  - effective slippage for a real order SIZE by walking levels l1..l20
    (buy eats asks, sell eats bids) -> avg fill vs mid

OUTPUT: prints a summary table + writes dom_spread_calib.json (the calibrated
slippage curve) so a backtest can swap the flat knob for a state-dependent one.

DATA IS READ-ONLY. Source zips are never modified (brother's DOM = do-not-touch).

Usage:
  python dom_spread.py                     # all days, NIFTY, all instruments
  python dom_spread.py --symbol ETERNAL    # the stock-option file instead
  python dom_spread.py --days 3            # first 3 days only (quick look)
"""
import os, sys, csv, json, zipfile, argparse, random
from collections import defaultdict
import numpy as np

random.seed(42)
RES_CAP = 120000  # per-bucket reservoir cap for percentiles (mean uses ALL rows)

class Reservoir:
    """running sum/count for an exact mean + a capped reservoir sample for percentiles."""
    __slots__ = ("s", "n", "buf", "seen")
    def __init__(self):
        self.s = 0.0; self.n = 0; self.buf = []; self.seen = 0
    def add(self, v):
        self.s += v; self.n += 1; self.seen += 1
        if len(self.buf) < RES_CAP:
            self.buf.append(v)
        else:
            j = random.randint(0, self.seen - 1)
            if j < RES_CAP:
                self.buf[j] = v
    def mean(self): return (self.s / self.n) if self.n else None
    def pctl(self, q): return float(np.percentile(self.buf, q)) if self.buf else None
    def __len__(self): return self.n

DOM_DIR = r"C:\_SABHAI DATA - Copy"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dom_spread_calib.json")

# 20 bid levels then 20 ask levels, each (price, qty), after 10 base columns.
BASE = 10  # time,timestamp,symbol,instrument,label,expiry,strike,atm_strike,spot_ltp,ltp
BID0 = BASE            # bid_l1_price index
ASK0 = BASE + 40       # ask_l1_price index (20 bid levels * 2)

# order sizes to walk the book for, in raw units (NIFTY lot ~75; these span ~1..20 lots)
ORDER_SIZES = [75, 375, 750, 1500]

def tod_bucket(hhmmss):
    """time-of-day bucket from 'HH:MM:SS.mmm'"""
    hm = hhmmss[:5]
    h = int(hhmmss[:2]); m = int(hhmmss[3:5])
    t = h * 60 + m
    if t < 9*60+20:  return "1_open_0915_0920"
    if t < 10*60:    return "2_0920_1000"
    if t < 11*60:    return "3_1000_1100"
    if t < 14*60:    return "4_midday_1100_1400"
    if t < 15*60:    return "5_1400_1500"
    return "6_close_1500_1530"

def prem_bucket(mid):
    if mid < 50:   return "p_lt50"
    if mid < 100:  return "p_50_100"
    if mid < 200:  return "p_100_200"
    if mid < 400:  return "p_200_400"
    return "p_gt400"

def fnum(x):
    if x == "" or x is None:
        return None
    try:
        v = float(x)
        return v if v > 0 else None
    except ValueError:
        return None

def walk(levels, target):
    """levels = list of (price, qty) one side, best-first. Return avg fill price
    for `target` units, or None if the visible book (<=20 levels) can't fill it."""
    got = 0.0; cost = 0.0
    for px, qt in levels:
        if px is None or qt is None:
            break
        take = min(qt, target - got)
        cost += take * px
        got += take
        if got >= target:
            return cost / got
    return None  # not enough visible depth

def side_levels(cells, start):
    out = []
    for i in range(20):
        px = fnum(cells[start + 2*i]) if start + 2*i < len(cells) else None
        qt = fnum(cells[start + 2*i + 1]) if start + 2*i + 1 < len(cells) else None
        out.append((px, qt))
    return out

TOD_ORDER = ["1_open_0915_0920","2_0920_1000","3_1000_1100","4_midday_1100_1400","5_1400_1500","6_close_1500_1530"]
PREM_ORDER = ["p_lt50","p_50_100","p_100_200","p_200_400","p_gt400"]

class Acc:
    """accumulator for one instrument (memory-bounded via Reservoir)"""
    def __init__(self):
        self.n = 0
        self.dropped = 0
        self.half = defaultdict(Reservoir)        # tod -> half_spread_pct
        self.half_prem = defaultdict(Reservoir)   # prem -> half_spread_pct
        self.eff = {sz: Reservoir() for sz in ORDER_SIZES}   # sz -> slip% (all tod pooled)
        self.eff_fillfail = {sz: 0 for sz in ORDER_SIZES}

    def add(self, cells):
        t = cells[0]
        bids = side_levels(cells, BID0)
        asks = side_levels(cells, ASK0)
        b1p = bids[0][0]; a1p = asks[0][0]
        # clean: need both L1 sides, non-crossed
        if b1p is None or a1p is None or a1p < b1p:
            self.dropped += 1
            return
        mid = 0.5 * (b1p + a1p)
        if mid <= 0:
            self.dropped += 1
            return
        self.n += 1
        tb = tod_bucket(t); pb = prem_bucket(mid)
        half_pct = 100.0 * (a1p - mid) / mid   # one-way cost of crossing at L1
        self.half[tb].add(half_pct)
        self.half_prem[pb].add(half_pct)
        for sz in ORDER_SIZES:
            buy = walk(asks, sz)   # buy eats asks
            if buy is None:
                self.eff_fillfail[sz] += 1
                continue
            self.eff[sz].add(100.0 * (buy - mid) / mid)

def summarize(acc, name):
    print(f"\n{'='*78}\n{name}  |  clean snapshots: {acc.n:,}  dropped(empty/crossed): {acc.dropped:,}", flush=True)
    print(f"{'-'*78}")
    print("HALF-SPREAD %% (one-way cost to cross at L1) by time-of-day:")
    print(f"  {'bucket':<22} {'n':>10} {'median':>8} {'mean':>8} {'p90':>8}")
    for tb in TOD_ORDER:
        r = acc.half.get(tb)
        if not r or len(r) == 0: continue
        print(f"  {tb:<22} {len(r):>10,} {r.pctl(50):>8.3f} {r.mean():>8.3f} {r.pctl(90):>8.3f}")
    print("\nHALF-SPREAD %% by premium level:")
    for pb in PREM_ORDER:
        r = acc.half_prem.get(pb)
        if not r or len(r) == 0: continue
        print(f"  {pb:<22} {len(r):>10,} {r.pctl(50):>8.3f} {r.mean():>8.3f} {r.pctl(90):>8.3f}")
    print("\nEFFECTIVE one-way slippage %% by ORDER SIZE (walk-the-book, all-day pooled):")
    print(f"  {'size(units)':<12} {'n_filled':>10} {'fill_fail':>10} {'median%':>9} {'mean%':>9} {'p90%':>9}")
    for sz in ORDER_SIZES:
        r = acc.eff[sz]
        if len(r) == 0: continue
        print(f"  {sz:<12} {len(r):>10,} {acc.eff_fillfail[sz]:>10,} "
              f"{r.pctl(50):>9.3f} {r.mean():>9.3f} {r.pctl(90):>9.3f}")

def calib_dict(acc):
    """the machine-readable calibration a backtest can load"""
    d = {"n_clean": acc.n, "half_spread_pct_by_tod": {}, "half_spread_pct_by_prem": {},
         "eff_slip_pct_by_size": {}}
    for tb in TOD_ORDER:
        r = acc.half.get(tb)
        if r and len(r): d["half_spread_pct_by_tod"][tb] = {"median": r.pctl(50), "mean": r.mean(), "p90": r.pctl(90), "n": len(r)}
    for pb in PREM_ORDER:
        r = acc.half_prem.get(pb)
        if r and len(r): d["half_spread_pct_by_prem"][pb] = {"median": r.pctl(50), "mean": r.mean(), "p90": r.pctl(90), "n": len(r)}
    for sz in ORDER_SIZES:
        r = acc.eff[sz]
        if len(r): d["eff_slip_pct_by_size"][str(sz)] = {"median": r.pctl(50), "mean": r.mean(),
                                                         "p90": r.pctl(90), "n": len(r), "fill_fail": acc.eff_fillfail[sz]}
    return d

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--days", type=int, default=0, help="limit to first N days (0=all)")
    ap.add_argument("--instrument", default="ALL", help="CE_NEAR|PE_NEAR|FUT_NEAR|ALL")
    args = ap.parse_args()

    zips = sorted(f for f in os.listdir(DOM_DIR) if f.startswith("orderbook_") and f.endswith(".zip"))
    if args.days: zips = zips[:args.days]
    print(f"DOM dir: {DOM_DIR}\nsymbol: {args.symbol}  days: {len(zips)}  order sizes: {ORDER_SIZES}")

    want = {"CE_NEAR", "PE_NEAR", "FUT_NEAR"} if args.instrument == "ALL" else {args.instrument}
    accs = {inst: Acc() for inst in want}

    for zf in zips:
        path = os.path.join(DOM_DIR, zf)
        try:
            with zipfile.ZipFile(path) as z:
                entry = next((e for e in z.namelist() if e.startswith(args.symbol)), None)
                if not entry:
                    print(f"  [skip] {zf}: no {args.symbol} entry"); continue
                with z.open(entry) as fh:
                    rdr = csv.reader((line.decode("utf-8", "replace") for line in fh))
                    next(rdr, None)  # header
                    day_n = {i: 0 for i in want}
                    for cells in rdr:
                        if len(cells) < ASK0 + 2:
                            continue
                        inst = cells[3]
                        if inst in accs:
                            accs[inst].add(cells)
                            day_n[inst] += 1
            print(f"  {zf}: " + "  ".join(f"{i.split('_')[0]}={day_n[i]:,}" for i in want), flush=True)
        except zipfile.BadZipFile:
            print(f"  [skip] {zf}: bad zip")

    out = {}
    for inst in want:
        summarize(accs[inst], f"{args.symbol} {inst}")
        out[inst] = calib_dict(accs[inst])

    with open(OUT, "w") as f:
        json.dump({"symbol": args.symbol, "days": len(zips), "order_sizes": ORDER_SIZES,
                   "note": "half_spread_pct = one-way L1 cross cost (mid->ask) as %% of mid; "
                           "eff_slip_pct = walk-the-book avg fill vs mid for the given size",
                   "instruments": out}, f, indent=2)
    print(f"\nwrote {OUT}")

if __name__ == "__main__":
    main()
