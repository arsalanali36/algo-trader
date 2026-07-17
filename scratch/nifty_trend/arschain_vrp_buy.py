"""Ars chain — make the BUYER pay VRP. The mirror of the SELL steelman.

arschain_backtest.vrp_sweep() paid the SELLER a volatility risk premium before concluding
"selling loses". The BUY side never got the same test — and it needs it MORE, because the
modelling error runs the other way:

    sigma = realised vol  ->  premium modelled CHEAPER than the market really charges
      - buyer pays less extrinsic than reality  -> less theta bled over the hold
      - ATM gamma ~ 1/(S*sigma*sqrt(T))         -> understated sigma OVERSTATES gamma

Both flatter the buyer. So every BUY number so far is an UPPER bound, not a conservative
one (an earlier docstring of mine claimed the opposite — it was wrong).

vrp_mult scales sigma at BOTH entry and exit = "IV sits persistently X% above realised",
which is what VRP actually is. 1.2 is the ordinary NIFTY level.

Run against both exit configs — they have different theta exposure: the current one
trails out early, "dono OFF" holds to 3:15 and pays the most time value. If the sigma
assumption is carrying the result, that is where it breaks first.

Diagnostic/one-off. Reads the repo, writes nothing to it.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import arschain_backtest as ab   # noqa: E402  engine runner (Rule 6B)
import bs_option as bs           # noqa: E402

MULTS = ((1.0, "realised vol (koi VRP nahi — ab tak ke saare numbers yahan hain)"),
         (1.1, "IV 10% upar"),
         (1.2, "IV 20% upar (NIFTY ka aam)"),
         (1.3, "IV 30% upar"),
         (1.5, "IV 50% upar (bahut hi udaar)"))

CONFIGS = (("jaisa abhi hai (trail ON, zone ON)", {}),
           ("dono OFF (sirf 3:15 + reversal)", dict(exit_atr=False, exit_zone=False)))


def main():
    print("\n  bars + engine...", flush=True)
    cont5 = ab.load_5m(None)
    daily = ab.daily_from_5m(cont5)
    lot = bs.get_nifty_lot()
    sig = bs.realised_vol_map(daily.set_index("date")["close"])

    for label, over in CONFIGS:
        trades = ab.run_engine(cont5, daily, ab.engine_cfg(**over))
        print("\n  " + "=" * 96)
        print("  %s  —  BUY @ ATM, 1 lot, %d trades" % (label, len(trades)))
        print("  " + "-" * 96)
        print("  %-10s %13s %8s %8s %13s   %s"
              % ("vrp_mult", "NET Rs", "PF", "Sharpe", "maxDD Rs", "matlab"))
        for m, note in MULTS:
            s2 = {d: v * m for d, v in sig.items()}   # IV persistently above realised
            rows = bs.reprice(trades, s2, lot, lots=1, itm_steps=0)
            st = ab.stats([r["pnl"] for r in rows])
            print("  %-10s %13s %8.2f %8.2f %13s   %s"
                  % (m, f"{st['net']:,.0f}", st["pf"], st["sharpe"],
                     f"{st['maxdd']:,.0f}", note))
        print("  " + "=" * 96)
    print()


if __name__ == "__main__":
    main()
