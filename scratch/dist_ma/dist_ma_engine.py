#!/usr/bin/env python3
r"""
dist_ma_engine.py — stateful, one-bar-at-a-time decision engine for the
Distance-from-MA extreme-oversold BUY strategy.

WHY a separate engine: the live paper/real trader must make the SAME entry/exit
decisions the backtest made (Critical Rule 10 — backtest fidelity). A vectorised
backtest loop can't run "one new day at a time" the way a live process does. This
engine processes a single daily bar per call, holding state (pending buy-stop +
open position) between calls — exactly the shape a live daily scan needs — and a
replay of it over history reproduces `dist_ma.backtest()`'s trades. Parity proven
in parity_check() below.

Per-symbol decision rules (mirror of dist_ma.backtest, exit_style="hold"):
  * FLAT: a reversal candle in the extreme (-10%) zone ARMS a buy-stop at that
    candle's HIGH for `entry_win` days; SL = candle LOW - sl_atr*ATR. Latest
    signal re-arms. Enter on the first day High >= trigger high.
  * OPEN: exit if Low <= SL (from the day after entry), else hold to `max_hold`
    trading days then exit at close.
Sizing / how-many-concurrent / rupee book = caller's job (see portfolio.py).
"""
import dist_ma as m

DEFAULTS = dict(thresh=-10.0, look=3, entry_win=3, max_hold=40,
                sl_atr=1.5, cost_pct=0.30, slip_pct=0.10)


def new_state():
    return {"pending": None, "pos": None}


def step(st, t, O, H, L, C, A, sig, cfg):
    """Advance one bar. Mutates st. Returns an action dict or None.
    action: {act:'ENTER', px, sl} | {act:'EXIT', px, reason}"""
    slip = cfg["slip_pct"] / 100.0
    # 1) manage an open position first
    if st["pos"]:
        p = st["pos"]
        held = t - p["entry_i"]
        if held >= 1 and L[t] <= p["sl"]:
            st["pos"] = None
            return {"act": "EXIT", "px": p["sl"] * (1 - slip), "reason": "SL"}
        if held >= cfg["max_hold"]:
            st["pos"] = None
            return {"act": "EXIT", "px": C[t] * (1 - slip), "reason": "TIME"}
        return None
    # 2) flat: try a pending buy-stop entry (a prior signal's window)
    if st["pending"]:
        pd = st["pending"]
        if H[t] >= pd["hi"]:
            entry = max(O[t], pd["hi"]) * (1 + slip)
            st["pos"] = {"entry": entry, "sl": pd["sl"], "entry_i": t}
            st["pending"] = None
            return {"act": "ENTER", "px": entry, "sl": pd["sl"]}
        pd["left"] -= 1
        if pd["left"] <= 0:
            st["pending"] = None
    # 3) arm on a fresh signal (latest signal wins)
    if st["pos"] is None and sig[t]:
        sl = L[t] - (cfg["sl_atr"] * A[t] if cfg["sl_atr"] > 0 else 0.0)
        st["pending"] = {"hi": H[t], "lo": L[t], "sl": sl, "left": cfg["entry_win"]}
    return None


def replay_symbol(sym, cfg=None):
    """Replay the engine bar-by-bar over one symbol's whole history.
    Returns list of trades (same shape as dist_ma.backtest rows)."""
    cfg = {**DEFAULTS, **(cfg or {})}
    d = m.prep(m.load(sym))
    O, H, L, C, A = d.Open.values, d.High.values, d.Low.values, d.Close.values, d.atr.values
    DATE = d.Date.values
    sig = m.signal_bars(d, cfg["thresh"], cfg["look"]).values
    st = new_state()
    trades, cur = [], None
    for t in range(len(C)):
        a = step(st, t, O, H, L, C, A, sig, cfg)
        if not a:
            continue
        if a["act"] == "ENTER":
            cur = {"sym": sym, "entry_date": DATE[t], "entry": a["px"], "sl": a["sl"]}
        elif a["act"] == "EXIT" and cur:
            gross = a["px"] / cur["entry"] - 1.0
            cur.update(exit_date=DATE[t], exit=a["px"], gross=gross,
                       net=gross - cfg["cost_pct"] / 100.0, reason=a["reason"])
            trades.append(cur)
            cur = None
    return trades


def replay_upto(sym, upto_date=None, cfg=None):
    """Live-facing: replay the engine over sym's daily bars up to `upto_date`
    (inclusive; None = all). Returns (completed_trades, open_pos_or_None).
    open_pos = {entry_date, entry, sl} for a position still held at upto_date.
    The live trader calls this each new completed day and diffs against its book —
    so LIVE decisions come from the SAME engine the backtest parity-check covers."""
    import pandas as pd
    cfg = {**DEFAULTS, **(cfg or {})}
    d = m.prep(m.load(sym))
    if upto_date is not None:
        d = d[d.Date <= pd.Timestamp(upto_date)].reset_index(drop=True)
    if len(d) < 2:
        return [], None
    O, H, L, C, A = d.Open.values, d.High.values, d.Low.values, d.Close.values, d.atr.values
    DATE = d.Date.values
    sig = m.signal_bars(d, cfg["thresh"], cfg["look"]).values
    st = new_state()
    trades, cur = [], None
    for t in range(len(C)):
        a = step(st, t, O, H, L, C, A, sig, cfg)
        if not a:
            continue
        if a["act"] == "ENTER":
            cur = {"sym": sym, "entry_date": str(pd.Timestamp(DATE[t]).date()),
                   "entry": a["px"], "sl": a["sl"]}
        elif a["act"] == "EXIT" and cur:
            gross = a["px"] / cur["entry"] - 1.0
            cur.update(exit_date=str(pd.Timestamp(DATE[t]).date()), exit=a["px"],
                       gross=gross, net=gross - cfg["cost_pct"] / 100.0, reason=a["reason"])
            trades.append(cur); cur = None
    return trades, cur


def latest_date():
    """Newest completed daily-bar date available across the lake (the 'today' the
    live trader acts on). Data-driven so a nightly lake update advances it."""
    import pandas as pd
    mx = None
    for s in m.symbols()[:30]:          # 30 liquid names enough to find the max date
        try:
            d = m.load(s)
            dt = pd.Timestamp(d.Date.iloc[-1])
            mx = dt if mx is None or dt > mx else mx
        except Exception:
            pass
    return str(mx.date()) if mx is not None else None


def parity_check():
    """Prove the stateful engine reproduces dist_ma.backtest() trade-for-trade."""
    import numpy as np, pandas as pd
    bt = m.backtest(thresh=-10, exit_style="hold", max_hold=40, sl_atr=1.5, cost_pct=0.30)
    eng = []
    for s in m.symbols():
        try:
            eng.extend(replay_symbol(s))
        except Exception:
            pass
    eng = pd.DataFrame(eng)
    def key(df):
        return set(zip(df.sym, pd.to_datetime(df.entry_date).astype(str),
                       pd.to_datetime(df.exit_date).astype(str),
                       df.entry.round(2), df.exit.round(2)))
    kb, ke = key(bt), key(eng)
    same = len(kb & ke)
    print(f"backtest trades : {len(bt)}")
    print(f"engine trades   : {len(eng)}")
    print(f"identical (sym+dates+px) : {same}  ({same/max(len(kb),1)*100:.1f}% of backtest)")
    print(f"only in backtest: {len(kb-ke)}   only in engine: {len(ke-kb)}")
    print(f"net% sum  backtest {bt.net.sum()*100:+.0f}%   engine {eng.net.sum()*100:+.0f}%")
    return same == len(kb) == len(ke)


if __name__ == "__main__":
    ok = parity_check()
    print("\nPARITY:", "EXACT ✓ live decisions == backtest" if ok else "MISMATCH — investigate")
