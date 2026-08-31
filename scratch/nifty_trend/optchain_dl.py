"""Dhan rollingoption downloader — REAL expired-option premium + IV + OI data-lake.

Endpoint: POST /v2/charts/rollingoption (paid "Expired Options Data" add-on).
Per call returns, for ONE relative strike (rolling ATM±n) + ONE side (CE/PE), a 5-min
series of open/high/low/close/volume/IV/OI/strike/spot. 30-day max per call.

MULTI-UNDERLYING (2026-07-15): builds a per-underlying lake under
_TRADING_DATA/OptChainLake/<SYM>/<WEEK|MONTH>/<CE|PE>_<off>.csv .
  • Index  (NIFTY/BANKNIFTY): OPTIDX, WEEK+MONTH, ATM±10, ~5yr.
  • Stocks (F&O liquid set):  OPTSTK, MONTH only (no weekly stock options), ATM±5, ~3yr.

The rollingoption call/parse lives in _data/opt_hist.py (single source, Rule 6B) —
this script only orchestrates task order, manifest/resume, append+dedup.

Priority order so USABLE data lands first: WEEK before MONTH, RECENT before old,
ATM outward (0,±1,±2…). Rate: lowest priority ("account") via dhan_rate_limiter so it
NEVER starves live orders; hard backoff on DH-904. Token re-read each call. Resumable
via a per-underlying manifest of done (series,chunk) keys.

USAGE (run on the VPS — token + subscription live there):
    venv/bin/python scratch/nifty_trend/optchain_dl.py                 # NIFTY (default, back-compat)
    venv/bin/python scratch/nifty_trend/optchain_dl.py --underlying NIFTY,BANKNIFTY
    venv/bin/python scratch/nifty_trend/optchain_dl.py --stocks        # ~liquid F&O stock set
    venv/bin/python scratch/nifty_trend/optchain_dl.py --stocks TCS,RELIANCE
"""
import os
import sys
import csv
import json
import time
import datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
import _paths  # noqa: F401  bootstrap so _data/opt_hist + universe import cleanly
import opt_hist
import universe

try:
    import dhan_rate_limiter as _rl_mod
except Exception:
    _rl_mod = None

LAKE_ROOT = os.path.join(ROOT, "_TRADING_DATA", "OptChainLake")
CONFIG = os.path.join(ROOT, "data", "config.json")

CHUNK_DAYS = 28                  # < 30-day API cap
COLS = ["timestamp", "open", "high", "low", "close", "volume", "iv", "oi", "strike", "spot"]
SIDES = [("CE", "CALL"), ("PE", "PUT")]

INDEX_START = dt.date(2021, 7, 1)          # ~5yr expired-option depth
STOCK_YEARS = 3
INDEX_OFF_RANGE = 10             # default; override with --off-range N
STOCK_OFF_RANGE = 5

# TRAP #198: the lake is ATM-RELATIVE, but a positional trade holds a FIXED strike.
# ATM drift walks that strike out of the window and every reader then has to guess.
# The window must be wider than the ATM drift a trade can survive, NOT just wide
# enough to hold the strikes at entry. +-10 was only enough for the entry snapshot;
# it left 19% of 02.10.01's trades priced at invented intrinsic values.


def _tok():
    c = json.load(open(CONFIG))
    return {"access-token": c["jwt_token"], "client-id": c["client_id"],
            "Content-Type": "application/json"}


def _rl_acquire_mod():
    return _rl_mod


def _offsets(off_range):
    return [0] + [s * n for n in range(1, off_range + 1) for s in (+1, -1)]


def _chunks(start):
    today = dt.date.today()
    out = []
    d = start
    while d <= today:
        e = min(d + dt.timedelta(days=CHUNK_DAYS - 1), today)
        out.append((d, e)); d = e + dt.timedelta(days=1)
    return out[::-1]              # recent-first


def _load_manifest(mpath):
    try:
        return set(json.load(open(mpath)))
    except Exception:
        return set()


def _save_manifest(mpath, done):
    os.makedirs(os.path.dirname(mpath), exist_ok=True)
    json.dump(sorted(done), open(mpath, "w"))


def _series_path(lake, flag, side, off):
    d = os.path.join(lake, flag)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s_%s.csv" % (side, opt_hist.series_slug(off)))


def _append(path, rows):
    """append rows (dedup on timestamp against existing file)."""
    seen = set()
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                seen.add(line.split(",", 1)[0])
    new = [r for r in rows if str(r[0]) not in seen]
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(COLS)
        w.writerows(new)
    return len(new)


def _build_specs(argv):
    """Return list of (symbol, sec_id, instrument, flags, off_range, start).

    Default (no selector) = NIFTY only (back-compat with the original script)."""
    args = argv[1:]
    want_stocks = "--stocks" in args
    underlyings = None
    stock_syms = None
    for i, a in enumerate(args):
        if a == "--underlying" and i + 1 < len(args):
            underlyings = [x.strip().upper() for x in args[i + 1].split(",") if x.strip()]
        if a == "--stocks" and i + 1 < len(args) and not args[i + 1].startswith("--"):
            stock_syms = [x.strip().upper() for x in args[i + 1].split(",") if x.strip()]

    specs = []
    # index underlyings
    idx = underlyings if underlyings else (None if want_stocks else ["NIFTY"])
    if idx:
        for sym in idx:
            if sym not in opt_hist.INDEX_UNDERLYINGS:
                print("  ! skip unknown index underlying:", sym, flush=True); continue
            sid, instr = opt_hist.INDEX_UNDERLYINGS[sym]
            specs.append((sym, sid, instr, ["WEEK", "MONTH"],
                          int(_arg_val("--off-range", INDEX_OFF_RANGE)), INDEX_START))
    # stocks
    if want_stocks:
        syms = stock_syms or sorted(set(universe.LIQUID_PREMIUM) |
                                    set(getattr(universe, "NIFTY50", [])))
        start = dt.date.today() - dt.timedelta(days=365 * STOCK_YEARS)
        miss = []
        for sym in syms:
            sid = universe.equity_secid(sym)
            if not sid:
                miss.append(sym); continue
            specs.append((sym, int(sid), "OPTSTK", ["MONTH"], STOCK_OFF_RANGE, start))
        if miss:
            print("  ! %d stocks unresolved (no NSE_EQ sec_id): %s"
                  % (len(miss), ",".join(miss)), flush=True)
    return specs


def _run_underlying(sym, sec_id, instrument, flags, off_range, start, rl,
                    lake_root, interval):
    lake = os.path.join(lake_root, sym)
    mpath = os.path.join(lake, "_done.json")
    os.makedirs(lake, exist_ok=True)
    done = _load_manifest(mpath)
    offsets = _offsets(off_range)
    chunks = _chunks(start)
    tasks = [(flag, side, dtype, off, frm, to)
             for flag in flags
             for off in offsets
             for (side, dtype) in SIDES
             for (frm, to) in chunks]
    total = len(tasks)
    print("\n=== %s (sec_id=%s %s) : %d calls  (%d offsets x %d sides x %d flags x %d chunks) ==="
          % (sym, sec_id, instrument, total, len(offsets), len(SIDES), len(flags), len(chunks)),
          flush=True)
    ok = skip = empty = err = 0
    t0 = time.time()
    for i, (flag, side, dtype, off, frm, to) in enumerate(tasks):
        key = "%s|%s|%s|%s" % (flag, side, off, frm)
        if key in done:
            skip += 1; continue
        try:
            rows, status = opt_hist.fetch_rolling(_tok(), sec_id, instrument, flag, off,
                                                  dtype, side.lower(), frm, to,
                                                  interval=interval, rl=rl)
            if status == 429:
                print("  [%d/%d] 429 — backoff 20s (%s)" % (i + 1, total, key), flush=True)
                time.sleep(20); continue
            if status not in (200, -1):
                err += 1
                if err <= 20:
                    print("  [%d/%d] HTTP %s %s" % (i + 1, total, status, key), flush=True)
                done.add(key); continue        # non-retryable input error → mark done
            n = len(rows)
            if n == 0:
                empty += 1; done.add(key)
                if empty <= 5:
                    print("  [%d/%d] empty %s" % (i + 1, total, key), flush=True)
            else:
                added = _append(_series_path(lake, flag, side, off), rows)
                ok += 1; done.add(key)
                if ok % 25 == 0 or i < 3:
                    rate = (i + 1) / max(time.time() - t0, 1)
                    eta = (total - i - 1) / max(rate, 0.01) / 60
                    print("  [%d/%d] %s: +%d rows (ok=%d empty=%d err=%d) ~%.1f/s ETA %.0fm"
                          % (i + 1, total, key, added, ok, empty, err, rate, eta), flush=True)
            if (i + 1) % 40 == 0:
                _save_manifest(mpath, done)
        except Exception as e:
            err += 1
            if err <= 20:
                print("  [%d/%d] EXC %s: %s" % (i + 1, total, key, e), flush=True)
            time.sleep(2)
        time.sleep(0.15)          # gentle base spacing on top of the limiter
    _save_manifest(mpath, done)
    print("--- %s DONE in %.0fm  ok=%d empty=%d err=%d skip=%d"
          % (sym, (time.time() - t0) / 60, ok, empty, err, skip), flush=True)


def _arg_val(flag, default):
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == flag and i + 1 < len(a):
            return a[i + 1]
    return default


def main():
    specs = _build_specs(sys.argv)
    if not specs:
        print("nothing to do — pass --underlying NIFTY,BANKNIFTY and/or --stocks [SYMS]", flush=True)
        return
    interval = _arg_val("--interval", "5")
    # keep the existing 5-min lake (ML pipeline reads it) untouched; 1-min etc. go
    # to a sibling OptChainLake_<N>m so granularities never mix in one CSV/manifest.
    lake_root = LAKE_ROOT if interval == "5" else "%s_%sm" % (LAKE_ROOT, interval)
    print("optchain_dl: interval=%smin  lake=%s  %d underlying(s): %s"
          % (interval, lake_root, len(specs), ", ".join(s[0] for s in specs)), flush=True)
    rl = _rl_acquire_mod()
    for (sym, sec_id, instrument, flags, off_range, start) in specs:
        _run_underlying(sym, sec_id, instrument, flags, off_range, start, rl,
                        lake_root, interval)
    print("\nALL DONE.", flush=True)


if __name__ == "__main__":
    main()
