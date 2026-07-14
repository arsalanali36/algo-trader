"""Task 3 (ml-strategy-mining) — precompute the per-bar trade-P&L table the GP
evolves against. For EVERY 5-min bar and EVERY (instrument x hold) combo:
"if a single-lot trade had entered at this bar's close, what was its NET P&L?"

Instruments (all ATM WEEK, held-strike — TRAP #109-safe):
    long_ce, long_pe, short_straddle, long_straddle
Holds:
    eod       exit last bar of the entry day (15:25)
    overnight exit last bar of the NEXT trading day
    expiry    exit last bar of the entry's weekly-expiry date (verified calendar)

Costs per trade (single lot = 65):
    Zerodha F&O charges per leg (exact bs_option.calc_charges formula, vectorized)
  + DOM-measured spread 0.13% per leg per side (ADR-005)

Exit strike re-mapping: held strike K priced at exit via offset (K - ATM_exit)/50.
If |offset| > 10 (spot ran >500pts over the hold) the exit premium falls back to
INTRINSIC value — dropping those rows would silently delete exactly the disaster
days short-vol needs to be punished by (survivorship). Fallback count is reported.

Output: ml_gp_pnl.npz  (pnl[n_bars,12] ₹, valid[n_bars,12], combo names, day ids)
Run once (~minutes); GP evals then cost ~ms each. Resumable by construction: if
the npz exists it is NOT rebuilt (delete it to force).
"""
import os
import json
import numpy as np
import pandas as pd

import ml_gate
import optlake_load as ol
import bs_option as bs

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ml_gp_pnl.npz")
LOT = 65
SLIP_LEG = 0.0013
STEP = 50
INSTRUMENTS = ["long_ce", "long_pe", "short_straddle", "long_straddle"]
HOLDS = ["eod", "overnight", "expiry"]
COMBOS = ["%s|%s" % (i, h) for i in INSTRUMENTS for h in HOLDS]
LAST_ENTRY_MIN = 345          # no fresh entries in the final ~25 min of the day


def charges_vec(buy_px, sell_px, qty=LOT):
    """exact bs_option.calc_charges, vectorized (single option leg round-trip)."""
    buy_turn, sell_turn = buy_px * qty, sell_px * qty
    total = buy_turn + sell_turn
    brokerage = 40.0
    stt = 0.000625 * sell_turn
    exch = 0.00053 * total
    sebi = 0.0000001 * total
    stamp = 0.00003 * buy_turn
    gst = 0.18 * (brokerage + exch + sebi)
    return brokerage + stt + exch + sebi + stamp + gst


def build():
    ch = ol.chain_frame("WEEK", "5m", offs=range(-10, 11))
    ml_gate.assert_no_lockbox(ch, what="chain(gp)")
    ch = ch.sort_values("Datetime").reset_index(drop=True)
    n = len(ch)
    offs = list(range(-10, 11)); off0 = offs.index(0)
    ce = np.full((n, len(offs)), np.nan); pe = np.full((n, len(offs)), np.nan)
    for j, off in enumerate(offs):
        tag = ol._off_tag(off)
        if "CE%s_c" % tag in ch.columns:
            ce[:, j] = ch["CE%s_c" % tag].values
        if "PE%s_c" % tag in ch.columns:
            pe[:, j] = ch["PE%s_c" % tag].values
    katm = ch["CEATM_k"].values.astype(float)
    spot = ch["spot"].values.astype(float)
    edt = pd.to_datetime(ch.Datetime)
    day = edt.dt.date.values
    mins = (edt.dt.hour * 60 + edt.dt.minute - (9 * 60 + 15)).values

    days = pd.unique(day)
    day_id = pd.Series(pd.Categorical(day, categories=days).codes)
    last_bar = day_id.groupby(day_id).apply(lambda s: s.index[-1]).to_dict()  # day_id -> row
    # exit row per hold
    u_eod = np.array([last_bar[d] for d in day_id])
    u_on = np.array([last_bar.get(d + 1, -1) for d in day_id])
    # expiry date per day (evaluated at that day's open — verified calendar)
    exp_by_day = {}
    for i, d in enumerate(days):
        exp_by_day[i] = bs._next_weekly_expiry(pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=15)).date()
    date_to_id = {d: i for i, d in enumerate(days)}
    u_exp = np.full(n, -1)
    for i in range(n):
        eid = date_to_id.get(exp_by_day[day_id.iloc[i]])
        if eid is not None:
            u_exp[i] = last_bar[eid]
    # CONTRACT-ROLL GUARD (TRAP #114, found 2026-07-14): the WEEK rolling series
    # switches to the NEXT week's contract the day after expiry. Any hold whose
    # exit day is AFTER the entry's expiry date would price TWO DIFFERENT
    # contracts (buy the dying expiry-day straddle for pennies, "exit" into the
    # fresh weekly at full premium = fake profit — the overnight GP run converged
    # exactly onto this: 94% of its winning signals were expiry-day bars).
    # -> overnight entries are INVALID when next trading day > entry's expiry.
    exp_ok = np.array([exp_by_day[d] >= (days[d + 1] if d + 1 < len(days) else days[d])
                       for d in day_id])
    u_on = np.where(exp_ok, u_on, -1)
    U = {"eod": u_eod, "overnight": u_on, "expiry": u_exp}

    ent_ce, ent_pe = ce[:, off0], pe[:, off0]
    pnl = np.full((n, len(COMBOS)), np.nan)
    fallback = 0
    for hi, hold in enumerate(HOLDS):
        u = U[hold].copy()
        ok = (u > np.arange(n)) & (mins <= LAST_ENTRY_MIN)
        uu = np.where(ok, u, 0)
        joff = off0 + np.rint((katm - katm[uu]) / STEP).astype(int)
        in_rng = (joff >= 0) & (joff < len(offs)) & ok
        jj = np.clip(joff, 0, len(offs) - 1)
        x_ce = np.where(in_rng, ce[uu, jj], np.nan)
        x_pe = np.where(in_rng, pe[uu, jj], np.nan)
        # intrinsic fallback where strike ran out of the +-10 window
        out_rng = ok & ~in_rng & np.isfinite(katm) & np.isfinite(spot[uu])
        fallback += int(out_rng.sum())
        x_ce = np.where(out_rng, np.maximum(spot[uu] - katm, 0.0), x_ce)
        x_pe = np.where(out_rng, np.maximum(katm - spot[uu], 0.0), x_pe)
        # also fallback where in-range price is missing but spot known
        miss_ce = ok & in_rng & ~np.isfinite(x_ce) & np.isfinite(spot[uu])
        miss_pe = ok & in_rng & ~np.isfinite(x_pe) & np.isfinite(spot[uu])
        x_ce = np.where(miss_ce, np.maximum(spot[uu] - katm, 0.0), x_ce)
        x_pe = np.where(miss_pe, np.maximum(katm - spot[uu], 0.0), x_pe)

        slip_ce = SLIP_LEG * (ent_ce + x_ce) * LOT
        slip_pe = SLIP_LEG * (ent_pe + x_pe) * LOT
        ch_ce_long = charges_vec(ent_ce, x_ce); ch_pe_long = charges_vec(ent_pe, x_pe)
        ch_ce_short = charges_vec(x_ce, ent_ce); ch_pe_short = charges_vec(x_pe, ent_pe)

        base = {"long_ce": (x_ce - ent_ce) * LOT - ch_ce_long - slip_ce,
                "long_pe": (x_pe - ent_pe) * LOT - ch_pe_long - slip_pe,
                "short_straddle": (ent_ce - x_ce + ent_pe - x_pe) * LOT
                                  - ch_ce_short - ch_pe_short - slip_ce - slip_pe,
                "long_straddle": (x_ce - ent_ce + x_pe - ent_pe) * LOT
                                 - ch_ce_long - ch_pe_long - slip_ce - slip_pe}
        for ii, inst in enumerate(INSTRUMENTS):
            v = np.where(ok & np.isfinite(ent_ce) & np.isfinite(ent_pe)
                         & np.isfinite(x_ce) & np.isfinite(x_pe), base[inst], np.nan)
            pnl[:, ii * len(HOLDS) + hi] = v

    valid = np.isfinite(pnl)
    print("bars=%d combos=%d  valid entries per combo:" % (n, len(COMBOS)))
    for c, name in enumerate(COMBOS):
        print("  %-26s %7d" % (name, int(valid[:, c].sum())))
    print("intrinsic fallbacks (strike ran >±10):", fallback)
    np.savez_compressed(OUT, pnl=pnl, valid=valid,
                        combos=np.array(COMBOS),
                        day_id=day_id.values.astype(np.int32),
                        dt=edt.values.astype("datetime64[m]").astype(np.int64),
                        days=np.array([str(d) for d in days]))
    print("wrote", OUT)


if __name__ == "__main__":
    if os.path.exists(OUT):
        print(OUT, "exists — not rebuilding (delete to force)")
    else:
        build()
