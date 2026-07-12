"""
dom_probe.py — is there a TRADEABLE signal in the order book? (honest IC check)

Tests the classic microstructure-alpha idea on the most liquid instrument (NIFTY
FUT): does order-book IMBALANCE at time T predict the FUT mid move over the next
few seconds/minutes? Reports the Information Coefficient (corr of signal vs forward
return) + directional hit-rate at several horizons, so we can SEE the decay and
judge whether any of it survives retail latency (100-300ms) and cost.

OBI = (sum bid qty L1..L5 - sum ask qty L1..L5) / (sum both)   [-1..+1]
Signal is measured on the FUT; forward return is the FUT mid return.

DATA READ-ONLY (brother's DOM).
"""
import os, sys, csv, zipfile
import numpy as np

DOM_DIR = r"C:\_SABHAI DATA - Copy"
BASE = 10; BID0 = BASE; ASK0 = BASE + 40
DEPTH = 5                       # levels to sum for imbalance
HORIZONS = [1, 5, 15, 30, 60, 300]   # seconds
SPREAD_TICK = 0.05

def fnum(x):
    if not x: return None
    try:
        v = float(x); return v if v > 0 else None
    except ValueError:
        return None

def t_sec(hhmmss):
    return int(hhmmss[:2])*3600 + int(hhmmss[3:5])*60 + float(hhmmss[6:])

def day_series(zpath, symbol="NIFTY", inst="FUT_NEAR"):
    """return (ts[], mid[], obi[]) for one day's FUT snapshots (cleaned)."""
    ts=[]; mid=[]; obi=[]
    with zipfile.ZipFile(zpath) as z:
        entry = next((e for e in z.namelist() if e.startswith(symbol)), None)
        if not entry: return None
        with z.open(entry) as fh:
            rdr = csv.reader((ln.decode("utf-8","replace") for ln in fh))
            next(rdr, None)
            for c in rdr:
                if len(c) < ASK0+2 or c[3] != inst: continue
                b1=fnum(c[BID0]); a1=fnum(c[ASK0])
                if b1 is None or a1 is None or a1 < b1: continue
                qb=0.0; qa=0.0
                for k in range(DEPTH):
                    q=fnum(c[BID0+2*k+1]);  qb += q if q else 0
                    q=fnum(c[ASK0+2*k+1]);  qa += q if q else 0
                if qb+qa <= 0: continue
                ts.append(t_sec(c[0])); mid.append(0.5*(b1+a1)); obi.append((qb-qa)/(qb+qa))
    return np.array(ts), np.array(mid), np.array(obi)

def fwd_returns(ts, mid, H):
    """forward mid-return over H seconds via searchsorted (irregular grid)."""
    idx = np.searchsorted(ts, ts + H, side="left")
    ok = idx < len(ts)
    fr = np.full(len(ts), np.nan)
    fr[ok] = mid[idx[ok]]/mid[ok*np.arange(len(ts))[ok]//1 if False else np.arange(len(ts))[ok]] - 1.0
    return fr

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    zips = sorted(f for f in os.listdir(DOM_DIR) if f.startswith("orderbook_") and f.endswith(".zip"))[:days]
    OBI=[]; MID=[]; TS=[]; DAYID=[]
    for i,zf in enumerate(zips):
        r = day_series(os.path.join(DOM_DIR, zf))
        if r is None: continue
        ts,mid,obi = r
        TS.append(ts); MID.append(mid); OBI.append(obi); DAYID.append(np.full(len(ts), i))
        print(f"  {zf}: {len(ts):,} FUT snaps")
    print(f"\nOBI = (bidQty - askQty)/(sum) over top {DEPTH} levels; signal on FUT, forward = FUT mid return")
    print(f"{'horizon':>8} {'n':>10} {'IC(pearson)':>12} {'hit_rate%':>10} {'mean|ret|bps':>12}")
    for H in HORIZONS:
        allobi=[]; allfr=[]
        for ts,mid,obi in zip(TS,MID,OBI):
            idx = np.searchsorted(ts, ts + H, side="left")
            ok = idx < len(ts)
            fr = mid[np.clip(idx,0,len(ts)-1)]/mid - 1.0
            allobi.append(obi[ok]); allfr.append(fr[ok])
        o=np.concatenate(allobi); f=np.concatenate(allfr)
        m = np.isfinite(o)&np.isfinite(f)
        o,f = o[m], f[m]
        if len(o) < 100: continue
        ic = np.corrcoef(o,f)[0,1]
        # directional hit-rate on non-flat signal
        nz = np.abs(o) > 0.05
        hit = (np.sign(o[nz])==np.sign(f[nz])).mean()*100 if nz.sum() else float('nan')
        print(f"{H:>7}s {len(o):>10,} {ic:>12.4f} {hit:>10.1f} {np.abs(f).mean()*1e4:>12.2f}")
    print("\nIC ~0 = no linear predictive signal; IC>0.03-0.05 with decay = weak microstructure edge.")
    print("Retail reality: even a real 5-30s edge needs sub-100ms execution — futures-only, no colo = mostly gone.")

if __name__ == "__main__":
    main()
