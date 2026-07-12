"""POSITIONAL short-vol on REAL option data (held-strike) — overnight/multi-day, defined risk.

The honest lesson from intraday (TRAP #109): intraday theta is too small vs costs. Sellers
earn on OVERNIGHT theta held toward expiry. So: enter early in a weekly cycle, HOLD across
days to expiry, defined risk via wings (iron-fly / iron-condor). Overnight gap risk is capped
by the long wings (the whole point of "proper hedge").

Engine: builds on real_struct2.grid (per-bar HELD-strike premium via the ±10 offset grid;
beyond window → intrinsic floor — conservative, and near expiry intrinsic dominates so the
final mark is accurate). NO 3:15 daily exit; exits on expiry-day time-stop / % of credit
target / % stop / max-hold. Real Zerodha per-leg charges + slippage. 1x.

Structures (defined risk):
  iron_fly    : sell ATM CE+PE, buy ATM±wing CE/PE
  iron_condor : sell ATM±short CE/PE, buy ATM±(short+wing) CE/PE
"""
import datetime as dt
import numpy as np
import pandas as pd

import engine
import bs_option as bs
import real_struct2 as r2
import expiry_calendar as xcal

START_CAP = engine.START_CAP
STEP = 50


def _cycle_info(g):
    """per-bar: date, is_expiry_day, trading-days-to-next-expiry (0 on expiry day)."""
    days = g["DAY"]
    uniq = sorted(set(days))
    is_exp = {d: (d.weekday() == xcal.weekly_expiry_weekday(d)) for d in uniq}
    # next expiry trading-day index for each unique day
    exp_days = [d for d in uniq if is_exp[d]]
    tdte = {}
    ei = 0
    for d in uniq:
        while ei < len(exp_days) and exp_days[ei] < d:
            ei += 1
        if ei < len(exp_days):
            # trading days between d and exp_days[ei] (inclusive of neither/both consistent)
            lo = uniq.index(d); hi = uniq.index(exp_days[ei])
            tdte[d] = hi - lo
        else:
            tdte[d] = 999
    return is_exp, tdte, uniq


def backtest(flag="WEEK", tf="5m", struct="iron_fly", params=None, lot=75, lots=1,
             charges=True, rms_caps=None):
    p = dict(params or {})
    wing = int(p.get("wing_off", 5)); short = int(p.get("short_off", 2))
    tp = float(p.get("tp_frac", 0.5)); sl = float(p.get("sl_frac", 1.5))
    slip = float(p.get("slip_frac", 0.005))
    dte_entry = int(p.get("dte_entry", 4))      # enter when this many trading-days to expiry
    h_entry = int(p.get("h_entry", 9)); m_entry = int(p.get("m_entry", 20))
    exit_dte = int(p.get("exit_dte", 0))        # exit on the expiry day (0) unless target/stop first
    exit_h = int(p.get("exit_h", 15)); exit_m = int(p.get("exit_m", 0))
    qty = int(lots) * int(lot)
    g = r2.grid(flag, tf)
    n = len(g["DT"]); DT, DAY, TT = g["DT"], g["DAY"], g["TT"]
    is_exp, tdte, _ = _cycle_info(g)

    # optional IV-rank gate: only sell when premium is historically RICH (top of the range)
    iv_min = float(p.get("iv_min", 0.0))
    ivr = {}
    if iv_min > 0:
        import optlake_load as _ll
        ivr = _ll.iv_rank_daily(flag, tf, int(p.get("iv_lb", 40)))

    equity = START_CAP; pos = None; trades = []; eqc = []
    entered_cycle = set()      # expiry-dates whose cycle we've already entered

    def legs_at(i):
        K = g["ATMK"][i]
        if struct == "iron_fly":
            return [("CE", K + wing * STEP, +1), ("PE", K - wing * STEP, +1),
                    ("CE", K, -1), ("PE", K, -1)]
        if struct == "iron_condor":
            return [("CE", K + (short + wing) * STEP, +1), ("PE", K - (short + wing) * STEP, +1),
                    ("CE", K + short * STEP, -1), ("PE", K - short * STEP, -1)]
        raise ValueError(struct)

    def val(i):
        return sum(s * r2._px(g, i, side, K) for (side, K, s) in pos["legs"])

    def close(i, reason):
        nonlocal equity, pos
        cv = val(i); pnl_u = cv - pos["entry_val"]
        fee = 0.0; slip_cost = 0.0
        for (side, K, s) in pos["legs"]:
            ep = pos["eps"][(side, K)]; xp = r2._px(g, i, side, K)
            if charges:
                fee += bs.calc_charges(ep, xp, qty, entry_side=("BUY" if s > 0 else "SELL"))
            slip_cost += slip * (abs(ep) + abs(xp))
        pnl = (pnl_u - slip_cost) * qty - fee
        equity += pnl
        trades.append(dict(side="short-vol-pos", entry=round(pos["entry_val"], 2), exit=round(cv, 2),
                           qty=qty, pnl=pnl, points=round(pnl_u, 2), entry_i=pos["i"], exit_i=i,
                           entry_dt=pos["dt"], exit_dt=DT[i], reason=reason, bars=i - pos["i"],
                           fee=round(fee, 0), entry_prem=round(abs(pos["entry_val"]), 2),
                           exit_prem=round(abs(cv), 2), atm=round(pos["K0"]), struct=struct,
                           spot_in=round(g["SPOT"][pos["i"]], 1), spot_out=round(g["SPOT"][i], 1),
                           held_days=(pd.Timestamp(DT[i]).date() - pd.Timestamp(pos["dt"]).date()).days))
        pos = None

    for i in range(1, n):
        d = DAY[i]
        # ---- manage open ----
        if pos is not None:
            cv = val(i); mtm = (cv - pos["entry_val"]) * qty; ref = pos["ref"]
            hit = None
            if mtm >= tp * ref * qty:
                hit = "Target %credit"
            elif mtm <= -sl * ref * qty:
                hit = "Stop %credit"
            elif tdte.get(d, 999) <= exit_dte and (TT[i].hour, TT[i].minute) >= (exit_h, exit_m):
                hit = "Expiry exit"
            elif is_exp.get(d) and (TT[i].hour, TT[i].minute) >= (15, 15):
                hit = "Expiry EOD"
            if hit:
                close(i, hit)
        # ---- entry: once per weekly cycle, at dte_entry days before expiry ----
        if pos is None and tdte.get(d, 999) == dte_entry:
            # the expiry that ends this cycle
            exp_key = None
            for dd in sorted(set(DAY)):
                if dd >= d and is_exp.get(dd):
                    exp_key = dd; break
            if exp_key is not None and exp_key not in entered_cycle:
                if (TT[i].hour, TT[i].minute) >= (h_entry, m_entry) and (iv_min <= 0 or ivr.get(d, 0.0) >= iv_min):
                    legs = legs_at(i)
                    eps = {(side, K): r2._px(g, i, side, K) for (side, K, s) in legs}
                    if all(v > 0 for v in eps.values()):
                        entry_val = sum(s * eps[(side, K)] for (side, K, s) in legs)
                        ref = abs(entry_val) if abs(entry_val) > 1e-6 else 1.0
                        pos = dict(legs=legs, eps=eps, entry_val=entry_val, ref=ref,
                                   i=i, dt=DT[i], K0=g["ATMK"][i])
                        entered_cycle.add(exp_key)
        mk = equity
        if pos is not None:
            mk += (val(i) - pos["entry_val"]) * qty
        eqc.append((DT[i], mk))
    if pos is not None:
        close(n - 1, "End")
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=f"{struct}/positional", mode="positional")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    lot = bs.get_nifty_lot()
    print("POSITIONAL short-vol (REAL held-strike, overnight, defined risk, charges+slip):\n")
    print(f"{'struct':12s}{'dte':>4s}{'wing':>5s}{'tp':>5s}{'sl':>5s}{'trades':>7s}{'net%':>8s}{'sharpe':>8s}{'maxDD':>8s}{'win%':>6s}{'worst':>9s}{'avgD':>6s}")
    grid = []
    for struct in ("iron_fly", "iron_condor"):
        for dte in (4, 3, 2):
            for wing in (5, 8):
                for tp, sl in ((0.5, 1.5), (0.35, 1.0), (0.6, 2.0)):
                    grid.append((struct, dict(dte_entry=dte, wing_off=wing, short_off=3,
                                              tp_frac=tp, sl_frac=sl, slip_frac=0.005)))
    for struct, p in grid:
        res = backtest("WEEK", "5m", struct, p, lot=lot, charges=True)
        m, _ = engine.metrics(res)
        if m["trades"] < 20:
            continue
        tr = sorted(res["trades"], key=lambda t: t["pnl"])
        avgd = np.mean([t["held_days"] for t in res["trades"]]) if res["trades"] else 0
        print(f"{struct:12s}{p['dte_entry']:>4d}{p['wing_off']:>5d}{p['tp_frac']:>5.2f}{p['sl_frac']:>5.1f}"
              f"{m['trades']:>7d}{m['net_pct']:>8.1f}{m['sharpe']:>8.2f}{m['maxdd']:>8.1f}{m['win_rate']:>6.0f}"
              f"{tr[0]['pnl']:>9.0f}{avgd:>6.1f}")
