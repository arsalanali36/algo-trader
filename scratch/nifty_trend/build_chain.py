"""Chain-Zone (Ars Auto-Rev-Chain) -> option-SELLING credit spread, 3-pass hunt.

Ports the user's Pine "Ars_Auto_Rev_Chain" strategy: prev-day levels (PDH/L/C),
pivots R1-5/S1-5, and a chain of multiple previous highs/lows -> candle-pattern
zones at those levels -> breakout entry (see intraday_engine.chain_zone).

Reuses run_hunt.main() end-to-end (screen -> optimize[min(train,OOS)] -> 1000-perm
rotation significance -> 3-pass dashboard -> runs/<slug>/) but:
  * pins the screen to the `chain_zone` design only, and
  * overrides pass-3 repricing to a CREDIT SPREAD (long->bull-put SELL,
    short->bear-call SELL) via bs_option.reprice_spread — the option-SELLING form
    the user asked for (defined risk: the bought wing is the hedge).

TFs: 1/2/3/5/7/10-min (user's list). Usage: python build_chain.py
"""
import sys
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import run_hunt as rh

# --- credit-spread knobs (deployable form) ---
WING_STEPS = 10        # OTM wing distance from ATM in strike-steps (10 * 50 = 500 pts)
VRP_MULT = 0.95        # honest premium stress: price the sold credit ~5% THINNER

rh.DESIGN_TITLE["chain_zone"] = "Auto Rev-Chain — Zone Breakout (credit spread)"

_real_screen = rh.screen


def _only_chain(cache, tfs):
    rows = [r for r in _real_screen(cache, tfs) if r["design"] == "chain_zone"]
    print(f"\n[pin] restricted to chain_zone: {len(rows)} TF candidates", flush=True)
    return rows


def _bs_spread(rms_res, d, sigma_map, lot, lots):
    """Pass 3: reprice the RMS trade set into the ATM/OTM CREDIT SPREAD premium P&L."""
    opt = bs.reprice_spread(rms_res["trades"], sigma_map, lot, lots,
                            wing_steps=WING_STEPS, vrp_mult=VRP_MULT)
    etr = [dict(side=o["side"], entry=o["entry_prem"], exit=o["exit_prem"], qty=o["qty"],
                pnl=o["pnl"], points=o["points"], bars=o["bars"], reason=o["reason"],
                entry_i=o["entry_i"], exit_i=o["exit_i"],
                entry_dt=o["entry_dt"], exit_dt=o["exit_dt"]) for o in opt]
    add = np.zeros(len(d))
    for o in opt:
        if 0 <= o["exit_i"] < len(d):
            add[o["exit_i"]] += o["pnl"]
    eqv = engine.START_CAP + np.cumsum(add)
    bs_eq = pd.DataFrame({"Datetime": d.Datetime.values, "equity": eqv})
    bs_res = dict(trades=etr, equity=bs_eq, final=float(eqv[-1]),
                  variant=rms_res["variant"], mode="intraday", params=rms_res["params"])
    at = []
    for o in opt:
        o = dict(o); o["entry"] = o["entry_spot"]; o["exit"] = o["exit_spot"]
        o.pop("entry_i", None); o.pop("exit_i", None)
        at.append(o)
    fees = round(sum(o["fee"] for o in opt), 0)
    return bs_res, at, fees


rh.screen = _only_chain
rh._bs_res_and_trades = _bs_spread

if __name__ == "__main__":
    sys.argv = ["run_hunt.py", "--tf", "1m,2m,3m,5m,7m,10m",
                "--trials", "250", "--name", "chain_zone_credit"]
    rh.main()
