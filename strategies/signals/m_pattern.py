"""
m_pattern.py — SINGLE SOURCE of the "IV-pop -> M-rollover" signal (ADR-010 / Rule 6E).

Pure, import-free. Operates on an ATM combined-premium minute series
`series = [(x, value), ...]` (x = minute-of-day int, value = ATM CE+PE premium).
Both the BACKTEST (scratch/nifty_m_pattern/bt.py) and the LIVE trader
(_ops/m_pattern_ironfly_live.py) call THIS — so the entry signal that fires live is
byte-identical to the one the backtest measured. Never inline a second copy.

THESIS: ATM combined premium is ~delta-neutral, so a sharp SPIKE in it is a vega/IV pop
(not a directional move). When IV pops and the combined premium spikes hard, then rolls
over forming a small "M" (double top), that is the seller's entry.

detect(series, params) -> (x_of_rollover, spike_ratio) or None   (FIRST M of the series)
"""

# strictness presets: (EXTREMA_W, SPIKE_LOOK, SPIKE_PCT, PB_PCT, TOL, BOUNCE_PCT)
M_PRESETS = {
    "loose":  (5, 90, 0.12, 0.04, 0.02, 0.015),
    "medium": (6, 90, 0.18, 0.05, 0.015, 0.02),   # deployed
    "strict": (7, 90, 0.25, 0.07, 0.01, 0.03),
}
# deployed window (minute-of-day) for a valid entry
SIG_LO = 935
SIG_HI = 1445


def _extrema(vals, w):
    """Indices that are a local max / local min within +-w. Returns (peaks, troughs) sets."""
    n = len(vals); peaks, troughs = set(), set()
    for i in range(n):
        lo, hi = max(0, i - w), min(n, i + w + 1)
        seg = vals[lo:hi]
        if vals[i] == max(seg) and vals[i] > min(seg):
            peaks.add(i)
        elif vals[i] == min(seg) and vals[i] < max(seg):
            troughs.add(i)
    return peaks, troughs


def detect(series, params, lo=SIG_LO, hi=SIG_HI):
    """First M-rollover of `series`. Returns (x_rollover, spike_ratio) or None.

    params = (EXTREMA_W, SPIKE_LOOK, SPIKE_PCT, PB_PCT, TOL, BOUNCE_PCT)
      1. SPIKE : a local peak P1 >= (1+SPIKE_PCT) x rolling-min of the SPIKE_LOOK pts before.
      2. PULLBACK : premium drops >= PB_PCT from P1 to a valley T.
      3. HUMP : a later local peak P2 with P2 >= T x (1+BOUNCE_PCT) and P2 <= P1 x (1+TOL).
      4. ROLLOVER : premium breaks BELOW T -> the entry x (must be within [lo, hi]).
    """
    EW, SL, SP, PB, TOL, BO = params
    if len(series) < SL + 10:
        return None
    xs = [s[0] for s in series]
    vals = [s[1] for s in series]
    peaks, troughs = _extrema(vals, EW)
    n = len(vals)
    for i in range(n):
        if i not in peaks:
            continue
        p1 = vals[i]
        base = min(vals[max(0, i - SL):i + 1])
        if base <= 0 or p1 < base * (1 + SP):
            continue
        j = None
        for k in range(i + 1, n):
            if k in troughs and vals[k] <= p1 * (1 - PB):
                j = k; break
            if vals[k] > p1:
                break
        if j is None:
            continue
        t = vals[j]; q = None
        for k in range(j + 1, n):
            if k in peaks and vals[k] >= t * (1 + BO) and vals[k] <= p1 * (1 + TOL):
                q = k; break
            if vals[k] > p1 * (1 + TOL):
                break
        if q is None:
            continue
        for k in range(q + 1, n):
            if vals[k] < t:
                x = xs[k]
                return (x, p1 / base) if lo <= x <= hi else None
    return None


if __name__ == "__main__":
    # clean synthetic double-top with well-spaced extrema (window-6 safe)
    def ramp(x0, v0, v1, n):
        return [(x0 + i, v0 + (v1 - v0) * i / (n - 1)) for i in range(n)]
    ser = [(m, 100.0 + (m % 3) * 0.1) for m in range(900, 990)]   # ~flat baseline (90 pts)
    ser += ramp(990, 100, 130, 9)[1:]     # up to P1=130
    ser += ramp(998, 130, 116, 11)[1:]    # down to valley T=116
    ser += ramp(1008, 116, 126, 9)[1:]    # up to P2=126 (<=P1)
    ser += ramp(1016, 126, 108, 9)[1:]    # down, breaks below T=116 -> rollover
    r = detect(ser, M_PRESETS["medium"], lo=935, hi=1445)
    assert r is not None, "expected an M signal"
    x, sr = r
    assert 1016 < x <= 1024, ("rollover after P2", x)      # entry on the down-leg
    assert round(sr, 2) == 1.30, ("spike ratio", sr)       # 130 / 100
    # no signal when nothing spikes
    assert detect([(m, 100.0) for m in range(900, 1100)], M_PRESETS["medium"]) is None
    print("m_pattern self-test PASS", r)
