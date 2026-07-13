"""DIRECTIONAL CALENDAR on REAL option data (held-strike, two expiries) — "theta machine".

The honest gap this fills (vs the already-REJECTED neutral calendar / positional condor):
  - Neutral ATM calendar was dead (+18% but Sharpe 0.12, -Rs48.5k tail) — pure theta, cost ate it.
  - The video's "theta machine" is DIRECTIONAL: strike K is a MOVE-TARGET (OTM in the bias
    direction), so theta + a small delta edge both work. Slow grind toward K = ideal.

Structure (per entry, single side):
  FRONT  = SELL strike K in the WEEK (front) expiry   (-1)   fast theta decay
  BACK   = BUY  strike K in the MONTH (back) expiry    (+1)   slow theta decay
  side   = CE (bullish calendar, K above spot) or PE (bearish, K below spot)
  Net DEBIT paid = back_prem - front_prem  ->  MAX LOSS = debit (defined risk, low margin).

Data: reuses real_struct2.grid for BOTH flags (WEEK + MONTH), aligned on Datetime; premium is
REAL (lake), not Black-Scholes — calendar is vega/term-structure sensitive, BS-from-realized
can't see the two-expiry IV gap. Held strike via r2._px (offset vs current ATM; intrinsic floor
beyond the +-10 window). Real Zerodha per-leg charges + DOM slippage. 1x, overnight, 1 pos max.

Hold is short (<= front-expiry - 1 day) so neither the WEEK nor the MONTH rolling series rolls
mid-hold (front week is the same contract until its expiry; monthly rolls only at month-end).
Entries in the last week before MONTHLY expiry are skipped (back leg would roll) — see _skip_back_roll.

NOTE: back=MONTH is directly real-backtestable here. back=NEXT_WEEK is NOT in the lake (only WEEK
front + MONTH are collected) and is deliberately NOT faked — it needs a NEXT_WEEK collector series.
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


def _aligned(flagF="WEEK", flagB="MONTH", tf="5m"):
    """Build front+back grids and the list of (iF, iB) bar pairs sharing a Datetime.

    Returns (gF, gB, pairs) where pairs is an (N,2) int array of aligned bar indices,
    front-driven (iterate front bars; keep only those with a matching back bar)."""
    gF = r2.grid(flagF, tf)
    gB = r2.grid(flagB, tf)
    # map back Datetime -> back bar index
    bidx = {np.datetime64(d): j for j, d in enumerate(gB["DT"])}
    pairs = []
    for iF, d in enumerate(gF["DT"]):
        j = bidx.get(np.datetime64(d))
        if j is not None:
            pairs.append((iF, j))
    return gF, gB, np.array(pairs, dtype=int)


def _cycle_info(days):
    """per-day: is_expiry_day (front weekly) + trading-days-to-next-front-expiry (0 on expiry)."""
    uniq = sorted(set(days))
    is_exp = {d: (d.weekday() == xcal.weekly_expiry_weekday(d)) for d in uniq}
    exp_days = [d for d in uniq if is_exp[d]]
    tdte = {}
    ei = 0
    for d in uniq:
        while ei < len(exp_days) and exp_days[ei] < d:
            ei += 1
        tdte[d] = (uniq.index(exp_days[ei]) - uniq.index(d)) if ei < len(exp_days) else 999
    return is_exp, tdte, uniq


def _skip_back_roll(d):
    """True if entering on day d risks the MONTHLY back leg rolling during the hold.

    Conservative: skip the final 5 trading days before a monthly expiry. Monthly expiry weekday
    == the last weekly-expiry weekday of the month; approximate via xcal.is_monthly_expiry if present."""
    try:
        # xcal exposes a monthly-expiry check in this repo; fall back to "never skip" if not.
        return bool(getattr(xcal, "monthly_expiry_soon", lambda x: False)(d))
    except Exception:
        return False


def backtest(flagF="WEEK", flagB="MONTH", tf="5m", params=None, lot=75, lots=1,
             charges=True, rms_caps=None, bias_arr=None):
    """Directional calendar backtest.

    params:
      sig          : "mom" (spot momentum) | "fixed_up" | "fixed_dn" | "neutral"(k_off=0 ablation)
      mom_lb       : bars lookback for momentum bias (sig="mom")
      k_off        : strikes OTM from front-ATM in the bias direction (0..3); K = ATM +- k_off*50
      entry        : "time" (h_entry:m_entry) | "signalbar" (first qualifying bar/day)
      h_entry,m_entry
      tp_frac,sl_frac : profit/stop as fraction of entry DEBIT
      max_hold_days   : force-close after this many calendar days
      slip_mode    : "dom" (per-leg DOM half-spread) | "flat"
    bias_arr: optional per-front-bar external bias (+1 CALL / -1 PUT / 0 skip) — for ORB/chain wiring.
    """
    p = dict(params or {})
    sig = p.get("sig", "mom")
    mom_lb = int(p.get("mom_lb", 12))
    k_off = int(p.get("k_off", 2))
    entry_mode = p.get("entry", "time")
    h_entry = int(p.get("h_entry", 14)); m_entry = int(p.get("m_entry", 45))
    tp = float(p.get("tp_frac", 0.5)); sl = float(p.get("sl_frac", 1.0))
    max_hold = int(p.get("max_hold_days", 5))
    slip = float(p.get("slip_frac", 0.005)); slip_mode = p.get("slip_mode", "dom")
    qty = int(lots) * int(lot)

    gF, gB, pairs = _aligned(flagF, flagB, tf)
    DTf = gF["DT"]; DAYf = gF["DAY"]; TTf = gF["TT"]; SPOT = gF["SPOT"]; ATMK = gF["ATMK"]
    is_exp, tdte, _ = _cycle_info(DAYf)

    equity = START_CAP; pos = None; trades = []; eqc = []
    entered_day = set()   # front-days we've already opened on (max 1 pos anyway)
    LOSS = (rms_caps or {}).get("loss_cap"); PROF = (rms_caps or {}).get("profit_target")
    day_real = {}         # per-front-day realized (for RMS)

    def cal_val(iF, iB, side, K):
        """current calendar value = back(BUY) - front(SELL), both strike K, real premium."""
        return r2._px(gB, iB, side, K) - r2._px(gF, iF, side, K)

    def bias_at(iF):
        if bias_arr is not None:
            return int(bias_arr[iF])
        if sig == "fixed_up":
            return +1
        if sig == "fixed_dn":
            return -1
        if sig == "neutral":
            return +1                      # ATM (k_off forced 0 below) — pure-theta ablation
        # momentum: compare front-ATM spot now vs mom_lb bars back
        if iF - mom_lb < 0:
            return 0
        return +1 if SPOT[iF] > SPOT[iF - mom_lb] else -1

    def close(iF, iB, reason):
        nonlocal equity, pos
        side = pos["side"]; K = pos["K"]
        cv = cal_val(iF, iB, side, K)
        pnl_u = cv - pos["debit"]                     # debit paid up front; value rises = profit
        fee = 0.0; slip_rs = 0.0
        # front leg: entry SELL, back leg: entry BUY
        for (g_, i_, s_, ep_) in ((gF, iF, -1, pos["epF"]), (gB, iB, +1, pos["epB"])):
            xp_ = r2._px(g_, i_, side, K)
            if charges:
                fee += bs.calc_charges(ep_, xp_, qty, entry_side=("BUY" if s_ > 0 else "SELL"))
            if slip_mode == "flat":
                slip_rs += slip * (abs(ep_) + abs(xp_)) * qty
            else:
                slip_rs += bs.slip_cost_leg(ep_, xp_, qty)
        pnl = pnl_u * qty - fee - slip_rs
        equity += pnl
        d0 = pd.Timestamp(pos["dt"]).date()
        day_real[d0] = day_real.get(d0, 0.0) + pnl
        trades.append(dict(side=("CALL-cal" if side == "CE" else "PUT-cal"),
                           entry=round(pos["debit"], 2), exit=round(cv, 2), qty=qty, pnl=pnl,
                           points=round(pnl_u, 2), entry_i=pos["iF"], exit_i=iF,
                           entry_dt=pos["dt"], exit_dt=DTf[iF], reason=reason, bars=iF - pos["iF"],
                           fee=round(fee, 0), entry_prem=round(abs(pos["debit"]), 2),
                           exit_prem=round(abs(cv), 2), atm=round(pos["K"]), struct="calendar",
                           spot_in=round(SPOT[pos["iF"]], 1), spot_out=round(SPOT[iF], 1),
                           held_days=(pd.Timestamp(DTf[iF]).date() - d0).days))
        pos = None

    for k in range(1, len(pairs)):
        iF, iB = pairs[k]
        d = DAYf[iF]
        # ---- manage open ----
        if pos is not None:
            cv = cal_val(iF, iB, pos["side"], pos["K"]); mtm = (cv - pos["debit"]) * qty
            ref = pos["debit"] * qty
            hit = None
            if ref > 0 and mtm >= tp * ref:
                hit = "Target %debit"
            elif ref > 0 and mtm <= -sl * ref:
                hit = "Stop %debit"
            elif (pd.Timestamp(DTf[iF]).date() - pd.Timestamp(pos["dt"]).date()).days >= max_hold:
                hit = "Max-hold"
            elif tdte.get(d, 999) <= 1 and (TTf[iF].hour, TTf[iF].minute) >= (15, 0):
                hit = "Front-expiry-1"     # close before front expiry-day gamma/pin
            if hit:
                close(iF, iB, hit)
        # ---- RMS daily caps (optional) ----
        if rms_caps and pos is not None:
            md = day_real.get(pd.Timestamp(pos["dt"]).date(), 0.0) + (cal_val(iF, iB, pos["side"], pos["K"]) - pos["debit"]) * qty
            if (LOSS and md <= -LOSS) or (PROF and md >= PROF):
                close(iF, iB, "RMS cap")
        # ---- entry (1 pos max, once/day) ----
        if pos is None and d not in entered_day and tdte.get(d, 999) >= 2 and not _skip_back_roll(d):
            want = ((entry_mode == "time" and (TTf[iF].hour, TTf[iF].minute) >= (h_entry, m_entry))
                    or (entry_mode == "signalbar"))
            if want:
                b = bias_at(iF)
                if b != 0:
                    side = "CE" if b > 0 else "PE"
                    ko = 0 if sig == "neutral" else k_off
                    K = ATMK[iF] + (ko * STEP if b > 0 else -ko * STEP)
                    epF = r2._px(gF, iF, side, K); epB = r2._px(gB, iB, side, K)
                    debit = epB - epF
                    if epF > 0 and epB > 0 and debit > 0:
                        pos = dict(side=side, K=K, epF=epF, epB=epB, debit=debit,
                                   iF=iF, dt=DTf[iF])
                        entered_day.add(d)
        mk = equity
        if pos is not None:
            mk += (cal_val(iF, iB, pos["side"], pos["K"]) - pos["debit"]) * qty
        eqc.append((DTf[iF], mk))

    if pos is not None:
        iF, iB = pairs[-1]
        close(iF, iB, "End")
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=f"calendar-{flagF}/{flagB}", mode="positional")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    lot = bs.get_nifty_lot()
    print(f"DIRECTIONAL CALENDAR screen (front WEEK sell / back MONTH buy, real held-strike, "
          f"charges+DOM slip, lot={lot}):\n")
    print(f"{'sig':9s}{'entry':10s}{'koff':>5s}{'tp':>5s}{'sl':>5s}{'hold':>5s}"
          f"{'trades':>7s}{'net%':>8s}{'sharpe':>8s}{'maxDD':>8s}{'win%':>6s}{'worst':>10s}{'avgD':>6s}")
    grid = []
    for sig in ("mom", "neutral"):
        for entry in ("time", "signalbar"):
            for koff in ((2,) if sig == "neutral" else (0, 1, 2, 3)):
                for tp, sl in ((0.5, 1.0), (0.3, 1.0), (0.75, 1.0)):
                    for hold in (3, 5):
                        grid.append(dict(sig=sig, entry=entry, k_off=koff, tp_frac=tp,
                                         sl_frac=sl, max_hold_days=hold, mom_lb=12, slip_mode="dom"))
    for p in grid:
        try:
            res = backtest("WEEK", "MONTH", "5m", p, lot=lot, charges=True)
        except Exception as e:
            print(f"  {p['sig']:8s} ERROR: {e}"); continue
        m, _ = engine.metrics(res)
        if m["trades"] < 15:
            continue
        tr = sorted(res["trades"], key=lambda t: t["pnl"])
        avgd = np.mean([t["held_days"] for t in res["trades"]]) if res["trades"] else 0
        print(f"{p['sig']:9s}{p['entry']:10s}{p['k_off']:>5d}{p['tp_frac']:>5.2f}{p['sl_frac']:>5.1f}"
              f"{p['max_hold_days']:>5d}{m['trades']:>7d}{m['net_pct']:>8.1f}{m['sharpe']:>8.2f}"
              f"{m['maxdd']:>8.1f}{m['win_rate']:>6.0f}{tr[0]['pnl']:>10.0f}{avgd:>6.1f}")
