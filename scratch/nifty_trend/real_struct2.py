"""HELD-STRIKE real-premium structure backtester — fixes the rolling-ATM proxy bug.

real_struct.py read the ROLLING-ATM columns for exit values — i.e. it marked the
position at "whatever the CURRENT ATM option is worth", not the CONTRACT YOU HOLD.
When spot trends away from the entry strike, the held short straddle loses INTRINSIC
value that the rolling proxy never sees (understates seller losses / buyer gains).

Fix: the lake has the full ±10 offset grid. At entry we record the held strikes; each
bar we locate each held strike's CURRENT offset vs the rolling ATM (offset = (K −
atm_now)/50) and read its REAL premium from that offset column. |offset| > 10 → the
strike left the recorded window → conservative intrinsic-floor mark (and exits fire
long before that in practice).

Structures: short_straddle, iron_fly (wing param). Same res-shape as real_struct.
"""
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import optlake_load as lake
import expiry_calendar as xcal

START_CAP = engine.START_CAP
EXIT_HM = dt.time(15, 15)
STEP = 50
_G_CACHE = {}


def grid(flag="WEEK", tf="5m"):
    """per-bar premium grid: dict side -> 2D array [bar, offset(-10..10)], + atm strike, spot."""
    key = (flag, tf)
    if key in _G_CACHE:
        return _G_CACHE[key]
    ch = lake.chain_frame(flag, tf, offs=range(-10, 11))
    atm_full = lake.atm_frame(flag, tf)
    atm = atm_full[["Datetime", "strike", "atm_iv"]]
    m = ch.merge(atm, on="Datetime", how="inner").reset_index(drop=True)
    # coverage guard (TRAP #107): chain-lake still filling vs the ATM reference →
    # inner-join truncates to the overlap and yields a bogus short-history backtest.
    import data_integrity
    _ok, _det = data_integrity.assert_coverage(m, atm_full, min_pct=0.9, label="chain⋈atm grid")
    if not _ok:
        print(f"[real_struct2] {_det['label']}: {_det['note']}", flush=True)
    offs = list(range(-10, 11))
    def col(side, off):
        tag = "ATM" if off == 0 else (f"ATMp{off}" if off > 0 else f"ATMm{abs(off)}")
        return f"{side}{tag}_c"
    CE = np.column_stack([m[col("CE", o)].values if col("CE", o) in m else np.full(len(m), np.nan) for o in offs])
    PE = np.column_stack([m[col("PE", o)].values if col("PE", o) in m else np.full(len(m), np.nan) for o in offs])
    out = dict(m=m, CE=CE.astype(float), PE=PE.astype(float),
               ATMK=m["strike"].values.astype(float), SPOT=m["spot"].values.astype(float),
               DT=m.Datetime.values, DAY=m.Datetime.dt.date.values,
               TT=m.Datetime.dt.time.values, offs=offs)
    _G_CACHE[key] = out
    return out


def _px(g, i, side, K):
    """REAL premium of strike K at bar i (via offset vs current ATM); intrinsic floor if out of window."""
    off = int(round((K - g["ATMK"][i]) / STEP))
    if -10 <= off <= 10:
        v = (g["CE"] if side == "CE" else g["PE"])[i, off + 10]
        if v and not np.isnan(v) and v > 0:
            return float(v)
    S = g["SPOT"][i]
    return max(0.0, (S - K) if side == "CE" else (K - S))   # intrinsic floor fallback


def backtest(flag="WEEK", tf="5m", struct="iron_fly", params=None, lot=75, lots=1,
             charges=True, rms_caps=None, entry_arr=None):
    p = dict(params or {})
    h0 = int(p.get("h0", 10)); wing = int(p.get("wing_off", 8))
    tp = float(p.get("tp_frac", 0.5)); sl = float(p.get("sl_frac", 1.0))
    # slip_mode: "dom" = DOM-measured per-leg half-spread (bs.slip_cost_leg, ATM≈0.11%/OTM-wing≈0.24%);
    # "flat" = the legacy flat slip_frac (default 0.5%/leg) to reproduce the old retracted numbers.
    slip = float(p.get("slip_frac", 0.005))
    slip_mode = p.get("slip_mode", "dom")
    skip_exp = bool(p.get("skip_expiry", True))
    qty = int(lots) * int(lot)
    g = grid(flag, tf)
    n = len(g["DT"]); DT, DAY, TT = g["DT"], g["DAY"], g["TT"]
    EXP = np.array([d.weekday() == xcal.weekly_expiry_weekday(d) for d in DAY])
    equity = START_CAP; pos = None; trades = []; eqc = []
    cur_day = None; day_real = 0.0; locked = False; entered_today = False
    LOSS = (rms_caps or {}).get("loss_cap"); PROF = (rms_caps or {}).get("profit_target")

    def legs_at_entry(i):
        K = g["ATMK"][i]
        if struct == "short_straddle":
            return [("CE", K, -1), ("PE", K, -1)]
        if struct == "iron_fly":
            return [("CE", K + wing * STEP, +1), ("PE", K - wing * STEP, +1),
                    ("CE", K, -1), ("PE", K, -1)]
        raise ValueError(struct)

    def val(i):
        return sum(s * _px(g, i, side, K) for (side, K, s) in pos["legs"])

    def close(i, reason):
        nonlocal equity, pos, day_real
        cv = val(i)
        pnl_u = cv - pos["entry_val"]
        fee = 0.0; slip_rs = 0.0
        for (side, K, s) in pos["legs"]:
            ep = pos["eps"][(side, K)]
            xp = _px(g, i, side, K)
            if charges:
                fee += bs.calc_charges(ep, xp, qty, entry_side=("BUY" if s > 0 else "SELL"),
                                       when=pos["dt"])
            if slip_mode == "flat":
                slip_rs += slip * (abs(ep) + abs(xp)) * qty     # legacy flat per-leg
            else:
                slip_rs += bs.slip_cost_leg(ep, xp, qty)        # DOM per-leg (premium-band aware)
        pnl = pnl_u * qty - fee - slip_rs
        equity += pnl; day_real += pnl
        trades.append(dict(side="short-vol", entry=round(pos["entry_val"], 2), exit=round(cv, 2),
                           qty=qty, pnl=pnl, points=round(pnl_u, 2), entry_i=pos["i"], exit_i=i,
                           entry_dt=pos["dt"], exit_dt=DT[i], reason=reason, bars=i - pos["i"],
                           fee=round(fee, 0), entry_prem=round(abs(pos["entry_val"]), 2),
                           exit_prem=round(abs(cv), 2), atm=round(pos["K0"]), struct=struct,
                           spot_in=round(g["SPOT"][pos["i"]], 1), spot_out=round(g["SPOT"][i], 1)))
        pos = None

    for i in range(1, n):
        if DAY[i] != cur_day:
            cur_day = DAY[i]; day_real = 0.0; locked = False; entered_today = False
        if pos is not None:
            if TT[i] >= EXIT_HM:
                close(i, "EOD 3:15")
            else:
                cv = val(i)
                mtm = (cv - pos["entry_val"]) * qty
                if mtm >= tp * pos["ref"] * qty:
                    close(i, "Target %credit")
                elif mtm <= -sl * pos["ref"] * qty:
                    close(i, "Stop %credit")
        if rms_caps and not locked:
            mtm_d = day_real + (((val(i)) - pos["entry_val"]) * qty if pos is not None else 0.0)
            if LOSS and mtm_d <= -LOSS:
                if pos is not None:
                    close(i, "RMS max-loss")
                locked = True
            elif PROF and mtm_d >= PROF:
                if pos is not None:
                    close(i, "RMS profit-target")
                locked = True
        do_enter = (entry_arr[i] if entry_arr is not None
                    else (TT[i] >= dt.time(h0, 0) and not entered_today))
        if (pos is None and not locked and do_enter and TT[i] < EXIT_HM
                and not (skip_exp and EXP[i]) and i + 1 < n):
            e = i
            legs = legs_at_entry(e)
            eps = {(side, K): _px(g, e, side, K) for (side, K, s) in legs}
            if all(v > 0 for v in eps.values()):
                entry_val = sum(s * eps[(side, K)] for (side, K, s) in legs)
                ref = abs(entry_val) if abs(entry_val) > 1e-6 else 1.0
                pos = dict(legs=legs, eps=eps, entry_val=entry_val, ref=ref,
                           i=e, dt=DT[e], K0=g["ATMK"][e])
                entered_today = True
        mk = equity
        if pos is not None:
            mk += (val(i) - pos["entry_val"]) * qty
        eqc.append((DT[i], mk))
    if pos is not None:
        close(n - 1, "End")
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=f"{struct}/held", mode="intraday")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    lot = bs.get_nifty_lot()
    print("HELD-STRIKE vol family — flat 0.5% (old) vs DOM real per-leg spread vs DOM 2x:\n")
    print(f"  {'struct':16s} {'slip':10s} {'trades':>7} {'net%':>8} {'sharpe':>7} {'maxDD%':>7} {'win%':>5} {'worst':>9}")
    scenarios = [("flat_0.5%", dict(slip_mode="flat", slip_frac=0.005), 1.0),
                 ("DOM_real",  dict(slip_mode="dom"), 1.0),
                 ("DOM_2x",    dict(slip_mode="dom"), 2.0)]
    for struct, base in [("iron_fly", dict(h0=10, wing_off=8, tp_frac=0.5, sl_frac=1.0)),
                         ("short_straddle", dict(h0=10, tp_frac=0.5, sl_frac=1.0))]:
        for name, extra, mult in scenarios:
            bs.SLIP_MULT = mult; bs.SLIP_ENABLED = True
            p = dict(base); p.update(extra)
            res = backtest("WEEK", "5m", struct, p, lot=lot, charges=True)
            mt, _ = engine.metrics(res)
            tr = sorted(res["trades"], key=lambda t: t["pnl"])
            print(f"  {struct:16s} {name:10s} {mt['trades']:>7d} {mt['net_pct']:>8.1f} {mt['sharpe']:>7.2f} "
                  f"{mt['maxdd']:>7.1f} {mt['win_rate']:>5.0f} {tr[0]['pnl']:>9.0f}")
        bs.SLIP_MULT = 1.0
        print()
