"""
dom_opt_probe.py — is there an OPTION buy/sell signal in the ATM CE/PE order book?

Two option-specific tests (the FUT probe didn't cover these):

  (A) OPTION SCALP: does the CE (or PE) book imbalance predict that option's OWN
      next premium move? (can you scalp the option off its book?)
      cost hurdle: option round-trip ~0.22% (22 bps) — moves must clear that.

  (B) OPTION-FLOW DIRECTIONAL: does CE-vs-PE book asymmetry lead the UNDERLYING?
      signal = CE_obi - PE_obi (net call-side buy pressure); forward = FUT mid return.
      cost hurdle (trade FUT): ~1.6 bps round trip.

Everything aligned on a 1-second grid per day (forward-filled), pooled across days.
OBI = (bidQty - askQty)/(sum) over top 5 levels.  DATA READ-ONLY.
"""
import os, sys, csv, zipfile
import numpy as np

DOM_DIR = r"C:\_SABHAI DATA - Copy"
BASE = 10; BID0 = BASE; ASK0 = BASE + 40
DEPTH = 5
DAY_START, DAY_END = 9*3600+15*60, 15*3600+30*60     # 1s grid window
HORIZONS = [5, 30, 60, 300]

def fnum(x):
    if not x: return None
    try:
        v = float(x); return v if v > 0 else None
    except ValueError:
        return None

def t_sec(h):
    return int(h[:2])*3600 + int(h[3:5])*60 + float(h[6:])

def grid_day(zpath, symbol="NIFTY"):
    """1s-grid forward-filled mid & obi for CE_NEAR, PE_NEAR, FUT_NEAR."""
    N = DAY_END - DAY_START
    out = {inst: dict(mid=np.full(N, np.nan), obi=np.full(N, np.nan))
           for inst in ("CE_NEAR","PE_NEAR","FUT_NEAR")}
    with zipfile.ZipFile(zpath) as z:
        entry = next((e for e in z.namelist() if e.startswith(symbol)), None)
        if not entry: return None
        with z.open(entry) as fh:
            rdr = csv.reader((ln.decode("utf-8","replace") for ln in fh))
            next(rdr, None)
            for c in rdr:
                if len(c) < ASK0+2: continue
                inst = c[3]
                if inst not in out: continue
                b1=fnum(c[BID0]); a1=fnum(c[ASK0])
                if b1 is None or a1 is None or a1 < b1: continue
                qb=qa=0.0
                for k in range(DEPTH):
                    q=fnum(c[BID0+2*k+1]); qb += q if q else 0
                    q=fnum(c[ASK0+2*k+1]); qa += q if q else 0
                if qb+qa <= 0: continue
                gi = int(t_sec(c[0])) - DAY_START
                if 0 <= gi < N:
                    out[inst]["mid"][gi] = 0.5*(b1+a1)
                    out[inst]["obi"][gi] = (qb-qa)/(qb+qa)
    # forward-fill
    def ffill(a):
        idx = np.where(~np.isnan(a), np.arange(len(a)), 0)
        np.maximum.accumulate(idx, out=idx)
        return a[idx]
    for inst in out:
        out[inst]["mid"] = ffill(out[inst]["mid"])
        out[inst]["obi"] = ffill(out[inst]["obi"])
    return out

def ic(sig, mid, H):
    fr = np.full_like(mid, np.nan)
    fr[:-H] = mid[H:]/mid[:-H] - 1.0
    m = np.isfinite(sig)&np.isfinite(fr)
    s, f = sig[m], fr[m]
    if len(s) < 200 or np.std(s)==0: return None
    return np.corrcoef(s, f)[0,1], len(s), np.abs(f).mean()*1e4

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    zips = sorted(f for f in os.listdir(DOM_DIR) if f.startswith("orderbook_") and f.endswith(".zip"))[:days]
    G = []
    for zf in zips:
        g = grid_day(os.path.join(DOM_DIR, zf))
        if g: G.append(g); print(f"  {zf}: gridded")
    ce_obi = np.concatenate([g["CE_NEAR"]["obi"] for g in G])
    pe_obi = np.concatenate([g["PE_NEAR"]["obi"] for g in G])
    ce_mid = np.concatenate([g["CE_NEAR"]["mid"] for g in G])
    pe_mid = np.concatenate([g["PE_NEAR"]["mid"] for g in G])
    fut_mid= np.concatenate([g["FUT_NEAR"]["mid"] for g in G])
    # per-day forward returns must not cross day boundary -> compute per day, concat
    def per_day_ic(sig_key, mid_key, H, transform=None):
        S=[]; F=[]
        for g in G:
            if sig_key=="ce-pe":
                sig = g["CE_NEAR"]["obi"] - g["PE_NEAR"]["obi"]
            else:
                sig = g[sig_key]["obi"]
            if transform: sig = transform(sig)
            mid = g[mid_key]["mid"]
            fr = np.full_like(mid, np.nan); fr[:-H] = mid[H:]/mid[:-H]-1.0
            m = np.isfinite(sig)&np.isfinite(fr)
            S.append(sig[m]); F.append(fr[m])
        s=np.concatenate(S); f=np.concatenate(F)
        if len(s)<200 or np.std(s)==0: return None
        return np.corrcoef(s,f)[0,1], len(s), np.abs(f).mean()*1e4

    print("\n(A) OPTION SCALP — book of the option vs its OWN premium move (cost hurdle ~22 bps):")
    print(f"  {'test':<22}{'H':>5}{'IC':>10}{'mean|ret|bps':>14}")
    for name, sk, mk in [("CE_obi -> CE prem","CE_NEAR","CE_NEAR"),
                          ("PE_obi -> PE prem","PE_NEAR","PE_NEAR")]:
        for H in HORIZONS:
            r = per_day_ic(sk, mk, H)
            if r: print(f"  {name:<22}{H:>4}s{r[0]:>10.4f}{r[2]:>14.2f}")

    print("\n(B) OPTION-FLOW DIRECTIONAL — CE/PE book asymmetry vs UNDERLYING (FUT) move (hurdle ~1.6 bps):")
    print(f"  {'signal':<22}{'H':>5}{'IC(vs FUT)':>12}{'mean|ret|bps':>14}")
    for name, sk in [("CE_obi","CE_NEAR"),("PE_obi","PE_NEAR"),("CE_obi - PE_obi","ce-pe")]:
        for H in HORIZONS:
            r = per_day_ic(sk, "FUT_NEAR", H)
            if r: print(f"  {name:<22}{H:>4}s{r[0]:>12.4f}{r[2]:>14.2f}")
    print("\nIC ~0 = no signal; |IC|>0.03-0.05 = weak edge. Option moves must clear ~22 bps spread;")
    print("directional-via-options only helps if it beats just reading the FUT book directly.")

if __name__ == "__main__":
    main()
