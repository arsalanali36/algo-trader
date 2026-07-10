"""Multi-leg option STRUCTURE backtester — NIFTY, Black-Scholes premium, 1x.

intraday_engine.py handles SINGLE-leg directional trades (long->buy CE, short->buy PE).
A STRUCTURE (straddle / strangle / condor / fly) is a set of legs whose P&L is the sum of
each leg's BS-priced premium change — a bet on VOLATILITY / RANGE, not direction. Entry is
a REGIME signal (breakout => long-vol; mid-day lull => short-vol), not a long/short call.

Reuses the rest of the pipeline unchanged: the res dict returned here matches
intraday_engine.backtest()'s shape, so engine.metrics / montecarlo / run_hunt._combo_from_res
all work on it directly — the P&L is ALREADY option-premium (no separate bs.reprice step).

House rules (same as everything else): force-exit 15:15, no entry >=15:15, max 2 trades/day,
no overnight, 1x (notional<=capital), real Zerodha per-leg charges (bs_option.calc_charges).

3-pass funnel for a structure (run_structure.py builds these):
    instrument = gross premium P&L (delta+theta, NO charges, NO daily caps) — "is there an edge?"
    rms        = + account daily loss/profit caps
    bs         = + real Zerodha per-leg charges = the deployable truth
"""
import datetime as dt
import numpy as np
import pandas as pd

import engine
import intraday_engine as ie
import bs_option as bs

START_CAP = engine.START_CAP
LEV_CAP = 1.0
EXIT_HM = dt.time(15, 15)
STEP = bs.STRIKE_STEP  # 50 for NIFTY

# ---- structure library: legs = (opt_type, strike_offset_in_steps, side) side +1 buy / -1 sell ----
STRUCTURES = {
    "short_straddle": [("CE", 0, -1), ("PE", 0, -1)],
    "long_straddle":  [("CE", 0, +1), ("PE", 0, +1)],
    "short_strangle": [("CE", +2, -1), ("PE", -2, -1)],
    "long_strangle":  [("CE", +2, +1), ("PE", -2, +1)],
    "iron_condor":    [("CE", +2, -1), ("PE", -2, -1), ("CE", +5, +1), ("PE", -5, +1)],
    "iron_fly":       [("CE", 0, -1), ("PE", 0, -1), ("CE", +5, +1), ("PE", -5, +1)],
}
# net direction of the structure (buy premium = long vol; sell premium = short vol)
STRUCT_KIND = {
    "short_straddle": "short_vol", "short_strangle": "short_vol",
    "iron_condor": "short_vol", "iron_fly": "short_vol",
    "long_straddle": "long_vol", "long_strangle": "long_vol",
}

# DIRECTIONAL structures: legs depend on the signal's direction (long vs short).
# vertical_debit = bull call spread on a long signal / bear put spread on a short signal
# (buy ATM, sell 2 steps OTM — defined risk, cheaper than naked ATM buy, less theta bleed).
DIRECTIONAL = {
    "vertical_debit": {
        "long":  [("CE", 0, +1), ("CE", +2, -1)],   # bull call spread
        "short": [("PE", 0, +1), ("PE", -2, -1)],   # bear put spread
    },
}
DIR_TITLE = {"vertical_debit": "Debit Vertical (Bull-Call / Bear-Put)"}


def entry_signal_dir(d, name, p):
    """Directional (long_arr, short_arr) for DIRECTIONAL structures."""
    if name == "_preset" and p.get("_entry") is not None:
        lo, sh = p["_entry"]
        return np.asarray(lo, dtype=bool), np.asarray(sh, dtype=bool)
    if name == "orb_break":
        return ie.design_signals(d, "orb", {"or_min": int(p.get("or_min", 15)),
                                            "orb_k": p.get("orb_k", 1.0)})
    if name == "tod_orb_break":
        return ie.design_signals(d, "tod_orb", {"or_min": int(p.get("or_min", 30)),
                                                "orb_k": p.get("orb_k", 1.0),
                                                "h0": int(p.get("h0", 10)), "h1": int(p.get("h1", 13))})
    raise ValueError(f"no directional form for signal {name}")


# ---------- entry regime signals -> boolean array (direction-agnostic) ----------
def entry_signal(d, name, p):
    C = d.Close
    if name == "orb_break":               # any opening-range breakout (long OR short) = a move
        lo, sh = ie.design_signals(d, "orb",
                                   {"or_min": int(p.get("or_min", 15)), "orb_k": p.get("orb_k", 1.0)})
        return lo | sh
    if name == "tod_orb_break":            # ORB but only inside a time window (mid-day)
        lo, sh = ie.design_signals(d, "tod_orb",
                                   {"or_min": int(p.get("or_min", 30)), "orb_k": p.get("orb_k", 1.0),
                                    "h0": int(p.get("h0", 10)), "h1": int(p.get("h1", 13))})
        return lo | sh
    if name == "midday_lull":              # one entry/day: first bar >= h0, optional low-morning-range gate
        tt = pd.to_datetime(d.Datetime).dt.time
        h0 = int(p.get("h0", 11))
        at = tt >= dt.time(h0, 0)
        first = at & (~at.shift(1, fill_value=False)) & (d.day == d.day)  # first in-window bar
        # keep only the first True per day (defensive; `first` already is that bar)
        e = first & (d.groupby("day")["Datetime"].transform(lambda s: True))
        if p.get("max_morn_atr"):          # optional: only sell when morning was quiet
            a = engine.atr(d, 14)
            O = d.Open
            morn_hi = d.High.where(tt < dt.time(h0, 0)).groupby(d.day).transform("max")
            morn_lo = d.Low.where(tt < dt.time(h0, 0)).groupby(d.day).transform("min")
            day_open = O.groupby(d.day).transform("first")
            quiet = (morn_hi - morn_lo) <= p["max_morn_atr"] * a
            e = e & quiet
        return e.fillna(False).values
    raise ValueError(f"unknown entry signal {name}")


ENTRY_SIGNALS = ("orb_break", "tod_orb_break", "midday_lull")


# ---------- cached per-bar precompute (EOD flags, tte, sigma) ----------
_PRECOMP = {}

def _precomp(d, sigma_map):
    key = (len(d), str(d.Datetime.iloc[0]), str(d.Datetime.iloc[-1]), id(sigma_map))
    hit = _PRECOMP.get(key)
    if hit is not None:
        return hit
    tt = pd.to_datetime(d.Datetime).dt.time.values
    EOD = np.array([t >= EXIT_HM for t in tt])
    T_bar = np.array([bs.tte_years(t) for t in d.Datetime])
    sm = sigma_map or {}
    dates = pd.to_datetime(d.Datetime).dt.date.values
    SIG = np.array([sm.get(dd_, 0.15) for dd_ in dates])
    _PRECOMP[key] = (EOD, T_bar, SIG)
    if len(_PRECOMP) > 24:            # keep memory bounded across many optimize slices
        _PRECOMP.pop(next(iter(_PRECOMP)))
    return EOD, T_bar, SIG


# ---------- structure backtest (option-premium P&L, engine-shaped res) ----------
def backtest_structure(d, sig_name, struct_name, params=None, sigma_map=None,
                       lot=75, lots=1, charges=True, rms_caps=None):
    """Returns a res dict shaped like intraday_engine.backtest() but with option-premium P&L.
    tp_frac / sl_frac (params) are fractions of the entry net-premium magnitude:
      exit when position P&L >= tp_frac*ref*qty  or  <= -sl_frac*ref*qty  or  15:15 EOD."""
    p = dict(params or {})
    directional = struct_name in DIRECTIONAL
    if directional:
        entry_l, entry_s = entry_signal_dir(d, sig_name, p)
        entry = np.asarray(entry_l, dtype=bool) | np.asarray(entry_s, dtype=bool)
        legs, kind = None, "directional"
    else:
        legs = STRUCTURES[struct_name]
        kind = STRUCT_KIND[struct_name]
        entry = entry_signal(d, sig_name, p)
    qty = int(lots) * int(lot)
    tp_frac = float(p.get("tp_frac", 0.5))
    sl_frac = float(p.get("sl_frac", 1.0))
    O, H, L, C = d.Open.values, d.High.values, d.Low.values, d.Close.values
    DAY = d.day.values
    DT = d.Datetime.values
    n = len(d)

    # per-bar time-to-expiry / EOD / sigma — CACHED by content key (these cost ~10s per call
    # on 5m data and are identical across the hundreds of optimize/permutation re-runs;
    # without the cache a 300-perm significance test takes 1.5h instead of minutes)
    EOD, T_bar, SIG = _precomp(d, sigma_map)
    LATE = EOD
    # variance-risk-premium stress: real-market IV ≈ realized + VRP, so long-vol pays MORE
    # premium than a realized-vol-priced BS world. vrp_mult>1 makes premium richer (honest
    # stress for any long-vol structure; also usable as a cheap IV proxy for short-vol).
    vrp_mult = float(p.get("vrp_mult", 1.0))
    if vrp_mult != 1.0:
        SIG = SIG * vrp_mult

    LOSS_CAP = (rms_caps or {}).get("loss_cap")
    PROFIT_TGT = (rms_caps or {}).get("profit_target")

    def leg_prices(spot, T, sig, legs_spec):
        atm = round(spot / STEP) * STEP
        out = []
        for (ot, off, side) in legs_spec:
            K = atm + off * STEP
            out.append((K, ot, side, bs.bs_price(spot, K, T, sig, opt=ot)))
        return atm, out

    def net_val(leg_state, spot, T, sig):
        """current net premium value per unit (sum of side*price)."""
        v = 0.0
        for (K, ot, side, _ep) in leg_state:
            v += side * bs.bs_price(spot, K, T, sig, opt=ot)
        return v

    equity = START_CAP
    pos = None
    trades = []
    eq_curve = []
    cur_day = None
    trades_today = 0
    day_realized = 0.0
    day_locked = False

    def close_pos(i, spot, reason):
        nonlocal equity, pos, day_realized
        legstate = pos["legs"]
        # per-unit exit value + per-leg charges
        exit_val = 0.0
        fee = 0.0
        for (K, ot, side, ep) in legstate:
            xp = bs.bs_price(spot, K, pos_T(i), ot_sig(i), opt=ot)
            exit_val += side * xp
            if charges:
                fee += bs.calc_charges(ep, xp, qty, entry_side=("BUY" if side > 0 else "SELL"))
        pnl_units = (exit_val - pos["entry_val"])          # already sign-correct (side baked in)
        pnl = pnl_units * qty - fee
        equity += pnl
        day_realized += pnl
        trades.append(dict(
            side=pos.get("side", kind), entry=round(pos["entry_val"], 2), exit=round(exit_val, 2), qty=qty,
            pnl=pnl, points=round(pnl_units, 2),
            entry_i=pos["entry_i"], exit_i=i, entry_dt=pos["entry_dt"], exit_dt=DT[i],
            reason=reason, bars=i - pos["entry_i"],
            atm=pos["atm"], struct=struct_name, entry_prem=round(abs(pos["entry_val"]), 2),
            exit_prem=round(abs(exit_val), 2), fee=round(fee, 0),
            spot_in=round(float(pos["spot_in"]), 1), spot_out=round(float(spot), 1)))
        pos = None

    def pos_T(i):
        return T_bar[i]

    def ot_sig(i):
        return SIG[i]

    for i in range(1, n):
        if DAY[i] != cur_day:
            cur_day = DAY[i]; trades_today = 0; day_realized = 0.0; day_locked = False

        # ---- manage open structure ----
        if pos is not None:
            cur_val = net_val(pos["legs"], C[i], T_bar[i], SIG[i])
            mtm = (cur_val - pos["entry_val"]) * qty         # ₹, sign-correct
            ref = pos["ref"]
            if not EOD[i]:
                if mtm >= tp_frac * ref * qty:
                    close_pos(i, C[i], "Target %credit")
                elif mtm <= -sl_frac * ref * qty:
                    close_pos(i, C[i], "Stop %credit")

        # ---- account daily caps (RMS overlay) ----
        if rms_caps and pos is not None and not EOD[i]:
            cur_val = net_val(pos["legs"], C[i], T_bar[i], SIG[i])
            day_mtm = day_realized + (cur_val - pos["entry_val"]) * qty
            if LOSS_CAP and day_mtm <= -LOSS_CAP:
                close_pos(i, C[i], "RMS max-loss"); day_locked = True
            elif PROFIT_TGT and day_mtm >= PROFIT_TGT:
                close_pos(i, C[i], "RMS profit-target"); day_locked = True
        if rms_caps and not day_locked:
            if (LOSS_CAP and day_realized <= -LOSS_CAP) or (PROFIT_TGT and day_realized >= PROFIT_TGT):
                day_locked = True

        # ---- EOD 15:15 force-exit ----
        if pos is not None and EOD[i]:
            close_pos(i, C[i], "EOD 3:15")

        # ---- entry (fill at next bar open) ----
        if (pos is None and not day_locked and not LATE[i] and trades_today < 2
                and i + 1 < n and not EOD[i] and entry[i]):
            spot = O[i + 1]
            if directional:
                dirn = "long" if entry_l[i] else "short"
                legs_spec = DIRECTIONAL[struct_name][dirn]
                w = int(p.get("wing_off", 0))
                if w:  # override short-leg distance (steps from ATM); wider = more room to run
                    legs_spec = [(ot, (w if off > 0 else -w) if s < 0 else off, s)
                                 for (ot, off, s) in legs_spec]
            else:
                dirn, legs_spec = kind, legs
            atm, legstate = leg_prices(spot, T_bar[i + 1], SIG[i + 1], legs_spec)
            entry_val = sum(side * ep for (_K, _ot, side, ep) in legstate)
            ref = abs(entry_val) if abs(entry_val) > 1e-6 else 1.0
            # 1x / no-leverage guard: total premium notional must not exceed capital
            notional = sum(abs(ep) for (_K, _ot, _s, ep) in legstate) * qty
            if notional <= LEV_CAP * equity:
                pos = dict(legs=legstate, entry_val=entry_val, ref=ref, atm=atm, side=dirn,
                           entry_i=i + 1, entry_dt=DT[i + 1], spot_in=spot)
                trades_today += 1

        # ---- mark equity ----
        m = equity
        if pos is not None:
            cur_val = net_val(pos["legs"], C[i], T_bar[i], SIG[i])
            m += (cur_val - pos["entry_val"]) * qty
        eq_curve.append((DT[i], m))

    if pos is not None:
        close_pos(n - 1, C[n - 1], "End")

    eqdf = pd.DataFrame(eq_curve, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=f"{struct_name}/{sig_name}", mode="intraday")


# ---------- rotation significance: is the ENTRY TIMING better than random? ----------
def sig_test(d, sig_name, struct_name, params, sigma_map, lot, lots=1, n_perm=500, seed=7):
    """Real structure Sharpe vs a null built by rotating the entry array by random offsets
    (same structure, same count-ish, random timing). p = P(null_sharpe >= real_sharpe)."""
    base = backtest_structure(d, sig_name, struct_name, params, sigma_map, lot, lots, charges=True)
    real_m, _ = engine.metrics(base)
    real = real_m.get("sharpe", 0.0)
    directional = struct_name in DIRECTIONAL
    if directional:
        lo_arr, sh_arr = entry_signal_dir(d, sig_name, params)
        lo_arr = np.asarray(lo_arr, dtype=bool); sh_arr = np.asarray(sh_arr, dtype=bool)
        idx = np.where(lo_arr | sh_arr)[0]
        n = len(lo_arr)
    else:
        entry = entry_signal(d, sig_name, params)
        idx = np.where(entry)[0]
        n = len(entry)
    if len(idx) < 10:
        return real, 1.0, 0.0
    rng = np.random.default_rng(seed)
    null = []
    ilo = np.where(lo_arr)[0] if directional else None
    ish = np.where(sh_arr)[0] if directional else None
    for j in range(n_perm):
        shift = int(rng.integers(50, n - 50))
        if directional:
            rl = np.zeros(n, dtype=bool); rl[(ilo + shift) % n] = True
            rs = np.zeros(n, dtype=bool); rs[(ish + shift) % n] = True
            rot = (rl, rs)
        else:
            rot = np.zeros(n, dtype=bool)
            rot[(idx + shift) % n] = True
        res = _backtest_with_entry(d, rot, struct_name, params, sigma_map, lot, lots)
        m, _ = engine.metrics(res)
        null.append(m.get("sharpe", 0.0))
        if (j + 1) % 100 == 0:
            print(f"    [sig] {j+1}/{n_perm} perms ...", flush=True)
    null = np.array(null)
    p = float((null >= real).mean())
    return float(real), p, float(np.percentile(null, 95))


def _backtest_with_entry(d, entry_arr, struct_name, params, sigma_map, lot, lots):
    """Same as backtest_structure but with a pre-supplied entry boolean array (for permutation)."""
    return backtest_structure(d, "_preset", struct_name, {**(params or {}), "_entry": entry_arr},
                              sigma_map, lot, lots, charges=True)


# allow a preset entry array (used by the permutation test) without a named signal
_orig_entry_signal = entry_signal
def entry_signal(d, name, p):  # noqa: F811  (shadow to support "_preset")
    if name == "_preset" and p.get("_entry") is not None:
        return np.asarray(p["_entry"], dtype=bool)
    return _orig_entry_signal(d, name, p)
