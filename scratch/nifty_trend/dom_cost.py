"""
dom_cost.py — load the DOM-measured spread calibration (dom_spread_calib.json)
and expose it as a per-leg slippage fraction a backtest can subtract.

The BS pass (bs_option.reprice*) gives the option MID (fair value). A real fill
crosses the spread: BUY at ask = mid*(1+h), SELL at bid = mid*(1-h), where h is
the one-way half-spread FRACTION. So the extra cost per round trip is
h*(entry_prem + exit_prem)*qty  — same form real_struct2 already uses for its
flat slip knob, but here h comes from measured data instead of a guess.

Numbers are measured on ~21 recent days (Jun-Jul 2026), ATM CE/PE. This is the
RECENT-regime spread — the right cost for the OOS/recent window (the mission's
primary judge). For pre-2022 years it is optimistic; use slip_mult>1 to stress.
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB = os.path.join(HERE, "dom_spread_calib.json")

# premium-bucket edges must match dom_spread.prem_bucket()
def _prem_bucket(mid):
    if mid < 50:   return "p_lt50"
    if mid < 100:  return "p_50_100"
    if mid < 200:  return "p_100_200"
    if mid < 400:  return "p_200_400"
    return "p_gt400"

_STAT = "median"   # use the median half-spread as the base cost (mean is skewed by open spikes)

def _load():
    with open(CALIB) as f:
        return json.load(f)

class DomCost:
    """per-leg half-spread fraction, keyed on option premium, averaged over CE+PE."""
    def __init__(self, stat=_STAT, slip_mult=1.0, path=CALIB):
        with open(path) as f:
            self.raw = json.load(f)
        self.stat = stat
        self.slip_mult = float(slip_mult)
        self.by_prem = {}        # bucket -> half-spread FRACTION (avg of CE,PE)
        insts = self.raw.get("instruments", {})
        for pb in ["p_lt50","p_50_100","p_100_200","p_200_400","p_gt400"]:
            vals = []
            for leg in ("CE_NEAR","PE_NEAR"):
                d = insts.get(leg, {}).get("half_spread_pct_by_prem", {}).get(pb)
                if d and d.get(stat) is not None:
                    vals.append(d[stat] / 100.0)   # pct -> fraction
            if vals:
                self.by_prem[pb] = sum(vals) / len(vals)
        # a single flat representative = the p_100_200/p_200_400 blend (typical ATM premium)
        atmish = [self.by_prem[b] for b in ("p_100_200","p_200_400") if b in self.by_prem]
        self.flat = (sum(atmish)/len(atmish)) if atmish else \
                    (sum(self.by_prem.values())/len(self.by_prem) if self.by_prem else 0.0015)

    def slip_for(self, premium):
        """per-leg half-spread fraction for an option at this premium."""
        h = self.by_prem.get(_prem_bucket(premium), self.flat)
        return h * self.slip_mult

    def flat_frac(self):
        return self.flat * self.slip_mult


def load(stat=_STAT, slip_mult=1.0):
    """convenience: returns a DomCost, or None if calib not built yet."""
    if not os.path.exists(CALIB):
        return None
    return DomCost(stat=stat, slip_mult=slip_mult)


if __name__ == "__main__":
    dc = load()
    if dc is None:
        print("dom_spread_calib.json not found — run dom_spread.py first")
    else:
        print(f"n_clean CE: {dc.raw['instruments'].get('CE_NEAR',{}).get('n_clean')}")
        print(f"flat per-leg half-spread fraction (ATM-ish): {dc.flat:.5f}  ({dc.flat*100:.3f}%)")
        print("per-premium-bucket half-spread fraction:")
        for k, v in dc.by_prem.items():
            print(f"  {k:<12} {v:.5f}  ({v*100:.3f}%)")
