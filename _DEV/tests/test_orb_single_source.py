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

PARAMS = dict(or_min=30, orb_k=1.0, atr_period=14, h0=11, h1=14)   # orb_v1 / mid_orb_nifty


def _b(x):
    a = np.asarray(x, dtype=object)
    return np.array([bool(v) if v == v else False for v in a])


def main():
    d = ie.resample(ie.load_1m(), "15m")
    fails = 0

    # 1) backtest design_signals("tod_orb") == shared orb_signals (backtest wired to shared)
    bl, bs = ie.design_signals(d, "tod_orb", PARAMS)
    sl, ss = shared.orb_signals(d.Datetime, d.High, d.Low, d.Close, PARAMS)
    bl, bs, sl, ss = _b(bl), _b(bs), _b(sl), _b(ss)
    ok1 = (bl == sl).all() and (bs == ss).all()
    print(f"[1] backtest tod_orb == shared orb_signals  "
          f"(L {bl.sum()}/{sl.sum()}, S {bs.sum()}/{ss.sum()})  -> {'PASS' if ok1 else 'FAIL'}")
    fails += 0 if ok1 else 1

    # 2) LIVE point-in-time (orb_signal_last on df up to each bar) == the vectorised signal
    #    at that bar → a live entry fires iff the backtest fired on that bar. Sample the
    #    last ~250 in-window bars (cheap, covers many days).
    d2 = d.rename(columns={"Datetime": "time", "High": "high", "Low": "low", "Close": "close"})
    idxs = [i for i in range(50, len(d2) - 1) if (sl[i] or ss[i])][-120:]  # bars the signal fired
    mism = 0
    for i in idxs:
        sub = d2.iloc[: i + 2].copy()          # ...so bar i is the "last CLOSED" (index -2)
        live = shared.orb_signal_last(sub, PARAMS, dt_col="time", hi="high", lo="low", cl="close")
        want = "long" if sl[i] else ("short" if ss[i] else None)
        # live also requires bar i to be "today"; in replay every bar is its own day-scoped
        # OR, so compare only the signal identity (date-guard is a live-safety, not signal, rule)
        if live != want:
            mism += 1
    ok2 = mism == 0
    print(f"[2] live orb_signal_last == backtest signal on {len(idxs)} fired bars  "
          f"({mism} mismatch)  -> {'PASS' if ok2 else 'FAIL'}")
    fails += 0 if ok2 else 1

    print("RESULT:", "ALL PASS — backtest and live fire the SAME ORB signal" if not fails
          else f"{fails} FAIL — backtest/live ORB signal DRIFTED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
