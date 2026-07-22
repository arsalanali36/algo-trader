"""ORB single-source guard — the backtest and the live trader MUST fire the same ORB
signal (they now both call strategies/signals/orb.orb_signals). This test fails the
moment they drift, so "backtest == live" can never silently break again (the exact debt
that made live diverge ~25% from the validated backtest).

Run: python -X utf8 _DEV/tests/test_orb_single_source.py
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
from strategies.signals import orb as shared  # noqa: E402

# (design, params, vectorised-shared-fn, live-point-in-time-fn)
CASES = [
    ("tod_orb", dict(or_min=30, orb_k=1.0, atr_period=14, h0=11, h1=14),
     shared.orb_signals, shared.orb_signal_last),                     # orb_v1
    ("orb_st", dict(or_min=30, orb_k=1.0, atr_period=14, st_period=10, st_mult=3.0),
     shared.orb_st_signals, shared.orb_st_signal_last),               # orbst_v1
]


def _b(x):
    a = np.asarray(x, dtype=object)
    return np.array([bool(v) if v == v else False for v in a])


def main():
    d = ie.resample(ie.load_1m(), "15m")
    d2 = d.rename(columns={"Datetime": "time", "High": "high", "Low": "low", "Close": "close"})
    fails = 0

    for design, params, vec_fn, live_fn in CASES:
        # 1) backtest design_signals == shared vectorised fn (backtest wired to shared)
        bl, bs = ie.design_signals(d, design, params)
        sl, ss = vec_fn(d.Datetime, d.High, d.Low, d.Close, params)
        bl, bs, sl, ss = _b(bl), _b(bs), _b(sl), _b(ss)
        ok1 = (bl == sl).all() and (bs == ss).all()
        print(f"[{design}] backtest == shared  (L {bl.sum()}/{sl.sum()}, S {bs.sum()}/{ss.sum()})"
              f"  -> {'PASS' if ok1 else 'FAIL'}")
        fails += 0 if ok1 else 1

        # 2) LIVE point-in-time == backtest signal on the bars the signal fired
        idxs = [i for i in range(60, len(d2) - 1) if (sl[i] or ss[i])][-120:]
        mism = 0
        for i in idxs:
            live = live_fn(d2.iloc[: i + 2], params, dt_col="time", hi="high", lo="low", cl="close")
            want = "long" if sl[i] else ("short" if ss[i] else None)
            if live != want:
                mism += 1
        ok2 = mism == 0
        print(f"[{design}] live == backtest on {len(idxs)} fired bars  ({mism} mismatch)"
              f"  -> {'PASS' if ok2 else 'FAIL'}")
        fails += 0 if ok2 else 1

    print("RESULT:", "ALL PASS — backtest and live fire the SAME signal (tod_orb, orb_st)"
          if not fails else f"{fails} FAIL — a backtest/live signal DRIFTED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
