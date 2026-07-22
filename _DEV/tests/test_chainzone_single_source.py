"""Chain-zone single-source guard — the backtest and the live 04_chainzone_trader MUST
fire the SAME zone-breakout signal (they now both call strategies/signals/chain_zone).
This test fails the moment they drift, so chainzone_v1's "backtest == live" can never
silently break (the exact debt flagged in TRAP #130 — never-matched implementations).

Run: python -X utf8 _DEV/tests/test_chainzone_single_source.py
(needs scratch/nifty_trend/nifty_1min.csv — the research NIFTY 1-min store.)
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scratch", "nifty_trend"))

import numpy as np  # noqa: E402
import intraday_engine as ie  # noqa: E402
from strategies.signals import chain_zone as shared  # noqa: E402

# chainzone_v1 runs at 5m; test a few TFs + the hawa variant.
BASE = dict(chain_lookback=20, max_jump=10.0, touch_tol=5.0, zone_age=2,
            max_cs=40.0, hawa=False, hawa_k=3)
CASES = [("5m", BASE), ("3m", BASE), ("15m", BASE),
         ("5m", {**BASE, "hawa": True})]


def _b(x):
    a = np.asarray(x, dtype=object)
    return np.array([bool(v) if v == v else False for v in a])


def main():
    fails = 0
    for tf, params in CASES:
        d = ie.resample(ie.load_1m(), tf)
        d2 = d.rename(columns={"Datetime": "time", "Open": "open", "High": "high",
                               "Low": "low", "Close": "close"})
        tag = f"{tf}{' hawa' if params['hawa'] else ''}"

        # 1) backtest design_signals == shared vectorised fn (backtest wired to shared)
        bl, bs = ie.design_signals(d, "chain_zone", params)
        sl, ss = shared.chain_zone_signals(d.Datetime, d.Open, d.High, d.Low, d.Close, params)
        bl, bs, sl, ss = _b(bl), _b(bs), _b(sl), _b(ss)
        ok1 = (bl == sl).all() and (bs == ss).all()
        print(f"[{tag}] backtest == shared  (L {bl.sum()}/{sl.sum()}, S {bs.sum()}/{ss.sum()})"
              f"  -> {'PASS' if ok1 else 'FAIL'}")
        fails += 0 if ok1 else 1

        # 2) LIVE point-in-time (chain_zone_signal_last — what 04_chainzone_trader calls)
        #    == the vectorised signal on the bars it fired. Done on a bounded TAIL window
        #    (~4k bars ≈ 50 trading days, far more than the 20-day chain lookback needs) so
        #    each point-in-time replay is cheap; the tail's own vectorised run is the
        #    reference, and test #1 above already ties the vectorised run to the backtest.
        WARM = 4000
        dt = d2.tail(WARM).reset_index(drop=True)
        tl, ts = shared.chain_zone_signals(dt["time"], dt["open"], dt["high"], dt["low"],
                                           dt["close"], params)
        tl, ts = _b(tl), _b(ts)
        idxs = [i for i in range(30, len(dt) - 1) if (tl[i] or ts[i])][-150:]
        mism = 0
        for i in idxs:
            live = shared.chain_zone_signal_last(dt.iloc[: i + 2], params, dt_col="time",
                                                 op="open", hi="high", lo="low", cl="close")
            want = "long" if tl[i] else ("short" if ts[i] else None)
            if live != want:
                mism += 1
        ok2 = mism == 0
        print(f"[{tag}] live == backtest on {len(idxs)} fired bars  ({mism} mismatch)"
              f"  -> {'PASS' if ok2 else 'FAIL'}")
        fails += 0 if ok2 else 1

    print("RESULT:", "ALL PASS — chain-zone backtest and live fire the SAME signal"
          if not fails else f"{fails} FAIL — chain-zone backtest/live signal DRIFTED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
