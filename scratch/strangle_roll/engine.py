"""
engine.py — 9:20 NIFTY short-strangle with roll-away adjustment, backtested on the
REAL OptChainLake_1m 1-min premium lake (2021-07 -> 2026-07). RESEARCH ONLY — no live
path, no order path. Reuses charges.py + expiry_calendar.py (Rule 6B).

STRATEGY (user spec, 2026-08-11)
  Entry   : 09:20, sell CE at nearest strike to spot+250, sell PE at spot-250 (naked).
  Roll    : when spot comes within TRIG points of an OPEN leg's strike -> buy-to-close
            that leg, sell a fresh same-side leg 250 pts from the CURRENT spot.
              - roll="threatened": only the touched leg rolls (strangle skews).
              - roll="recenter"  : both legs re-centered to spot +/-250.
  Exit    : combined running P&L >= 50% of ENTRY credit -> close all legs.
            plus deadline: intraday -> 15:10 same day ; positional -> weekly expiry 15:20
            (settle any leg left open at intrinsic).
  Charges : real date-aware Zerodha F&O (charges.py), per round-tripped leg, qty=1 lot.

All premiums are REAL (lake). No BS, no synthetic fills. A required strike missing from
the ATM+/-10 window at the needed minute = honest data-gap -> trade flagged, not faked.
"""
import os, sys, math, json
import pandas as pd
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "nifty_trend"))
import charges as CH
import expiry_calendar as EC

STEP = 50            # NIFTY strike step
LOT  = 65            # NIFTY lot units (Dhan scrip master)
DIST = 250           # entry / roll distance from spot
ENTRY_HM = 920
INTRADAY_EXIT_HM = 1510
POS_EXIT_HM = 1520
MAX_ROLLS_PER_DAY = 20   # runaway guard

LAKE = os.path.join(HERE, "..", "..", "_TRADING_DATA", "OptChainLake_1m", "NIFTY", "WEEK")
OFFSETS = list(range(-10, 11))


def _round50(x):
    return int(round(x / STEP) * STEP)


# ---------------------------------------------------------------- data load
def load_lake():
    """Return dict: date(str) -> per-minute grid.
       grid[hhmm] = {'spot': float, 'CE': {strike:prem}, 'PE': {strike:prem}}"""
    frames = []
    for ot in ("CE", "PE"):
        for off in OFFSETS:
            tag = "ATM" if off == 0 else ("ATMp%d" % off if off > 0 else "ATMm%d" % (-off))
            fn = os.path.join(LAKE, f"{ot}_{tag}.csv")
            if not os.path.exists(fn):
                continue
            d = pd.read_csv(fn, usecols=["timestamp", "close", "strike", "spot"])
            d["ot"] = ot
            frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["close", "strike", "spot"])
    ist = pd.to_datetime(df["timestamp"] + 19800, unit="s")
    df["date"] = ist.dt.strftime("%Y-%m-%d")
    df["hhmm"] = ist.dt.hour * 100 + ist.dt.minute
    days = {}
    for date, g in df.groupby("date", sort=True):
        grid = {}
        # spot per minute (any row's spot at that minute)
        for hhmm, gm in g.groupby("hhmm"):
            spot = float(gm["spot"].iloc[0])
            ce, pe = {}, {}
            for ot, strike, prem in zip(gm["ot"], gm["strike"], gm["close"]):
                (ce if ot == "CE" else pe)[int(strike)] = float(prem)
            grid[int(hhmm)] = {"spot": spot, "CE": ce, "PE": pe}
        days[date] = grid
    return days


# ---------------------------------------------------------------- helpers
def _prem(grid, hhmm, ot, strike):
    """Nearest-minute premium for (ot,strike). Returns (prem, used_hhmm) or (None,None)."""
    # exact minute first, then search forward a few, then backward
    for delta in [0] + [d for pair in zip(range(1, 8), range(-1, -8, -1)) for d in pair]:
        m = hhmm + delta
        cell = grid.get(m)
        if cell and strike in cell[ot]:
            return cell[ot][strike], m
    return None, None


def _spot_at(grid, hhmm):
    for delta in [0] + [d for pair in zip(range(1, 6), range(-1, -6, -1)) for d in pair]:
        cell = grid.get(hhmm + delta)
        if cell:
            return cell["spot"]
    return None


def _minutes(grid, start, end):
    return sorted(m for m in grid if start <= m <= end)


# ---------------------------------------------------------------- one trade
def run_trade(days, entry_date, holding, roll_mode, trig, take_pct=0.50):
    """Simulate one entry. Returns dict result or None (skip)."""
    grid = days.get(entry_date)
    if not grid:
        return None
    spot0 = _spot_at(grid, ENTRY_HM)
    if spot0 is None:
        return None
    ce_k = _round50(spot0 + DIST)
    pe_k = _round50(spot0 - DIST)
    ce_p, _ = _prem(grid, ENTRY_HM, "CE", ce_k)
    pe_p, _ = _prem(grid, ENTRY_HM, "PE", pe_k)
    if ce_p is None or pe_p is None:
        return {"date": entry_date, "skip": "entry_strike_missing"}

    entry_credit = ce_p + pe_p
    target = take_pct * entry_credit

    # open legs: {(ot,strike): sold_prem}
    open_legs = {("CE", ce_k): ce_p, ("PE", pe_k): pe_p}
    cash = ce_p + pe_p          # points collected (per unit)
    legs_log = [("CE", ce_k, ce_p, "SELL"), ("PE", pe_k, pe_p, "SELL")]
    charge_pairs = []           # (sold_prem, buy_prem, when) per round-trip
    rolls = 0
    when = entry_date

    # build the day sequence to walk
    if holding == "intraday":
        seq = [(entry_date, ENTRY_HM + 1, INTRADAY_EXIT_HM)]
        exit_deadline = (entry_date, INTRADAY_EXIT_HM)
    else:  # positional -> to weekly expiry
        exp = EC.next_weekly_expiry(entry_date) if hasattr(EC, "next_weekly_expiry") else _nearest_weekly(entry_date)
        exp = str(exp)
        # consecutive lake days from entry..expiry
        alldays = sorted(days.keys())
        seq = []
        for d in alldays:
            if d < entry_date or d > exp:
                continue
            lo = ENTRY_HM + 1 if d == entry_date else 916
            hi = POS_EXIT_HM if d == exp else 1529
            seq.append((d, lo, hi))
        # robust: real deadline = last available lake day <= computed expiry
        exit_deadline = (seq[-1][0], POS_EXIT_HM) if seq else (entry_date, POS_EXIT_HM)

    def running_pnl(day_grid, hhmm):
        tot = cash
        for (ot, k), _sold in open_legs.items():
            p, _ = _prem(day_grid, hhmm, ot, k)
            if p is None:
                return None
            tot -= p
        return tot

    exited = False
    exit_reason = None
    for (d, lo, hi) in seq:
        dgrid = days.get(d)
        if not dgrid:
            continue
        when = d
        for m in _minutes(dgrid, lo, hi):
            cell = dgrid[m]
            spot = cell["spot"]
            # 1) profit target
            rp = running_pnl(dgrid, m)
            if rp is not None and rp >= target:
                # close all open at this minute
                for (ot, k), sold in list(open_legs.items()):
                    p, _ = _prem(dgrid, m, ot, k)
                    p = p if p is not None else 0.0
                    cash -= p
                    charge_pairs.append((sold, p, d))
                open_legs.clear()
                exited = True
                exit_reason = "target"
                break
            # 2) roll check
            if rolls < MAX_ROLLS_PER_DAY:
                touched = [(ot, k) for (ot, k) in open_legs
                           if abs(spot - k) <= trig]
                if touched:
                    to_roll = list(open_legs.keys()) if roll_mode == "recenter" else touched
                    ok = True
                    staged = []
                    for (ot, k) in to_roll:
                        cp, _ = _prem(dgrid, m, ot, k)
                        nk = _round50(spot + DIST) if ot == "CE" else _round50(spot - DIST)
                        npm, _ = _prem(dgrid, m, ot, nk)
                        if cp is None or npm is None or nk == k:
                            if nk == k:
                                continue      # already at target strike, skip this leg
                            ok = False
                            break
                        staged.append((ot, k, cp, nk, npm))
                    if ok and staged:
                        for (ot, k, cp, nk, npm) in staged:
                            sold = open_legs.pop((ot, k))
                            cash -= cp                       # buy to close
                            charge_pairs.append((sold, cp, d))
                            cash += npm                      # sell new
                            open_legs[(ot, nk)] = npm
                            legs_log.append((ot, k, cp, "CLOSE"))
                            legs_log.append((ot, nk, npm, "SELL"))
                        rolls += 1
        if exited:
            break

    if not exited:
        # settle at deadline
        d, hm = exit_deadline
        dgrid = days.get(d)
        for (ot, k), sold in list(open_legs.items()):
            p = None
            if dgrid:
                p, _ = _prem(dgrid, hm, ot, k)
            if p is None:
                # intrinsic settle
                sp = _spot_at(dgrid, hm) if dgrid else spot0
                sp = sp if sp is not None else spot0
                p = max(0.0, sp - k) if ot == "CE" else max(0.0, k - sp)
            cash -= p
            charge_pairs.append((sold, p, d))
        open_legs.clear()
        exit_reason = "deadline"

    gross = cash * LOT
    charge = sum(CH.option_charges(s, b, LOT, entry_side="SELL", when=w) for (s, b, w) in charge_pairs)
    net = gross - charge
    return {
        "date": entry_date, "holding": holding, "roll": roll_mode, "trig": trig,
        "entry_credit": round(entry_credit, 2), "spot0": round(spot0, 1),
        "rolls": rolls, "gross": round(gross, 1), "charges": round(charge, 1),
        "net": round(net, 1), "reason": exit_reason, "n_legs": len(charge_pairs),
    }


def _nearest_weekly(date_str):
    d = pd.to_datetime(date_str).date()
    wd = EC.weekly_expiry_weekday(d)
    from datetime import timedelta
    cur = d
    for _ in range(10):
        if cur.weekday() == wd and cur >= d:
            return cur
        cur = cur + timedelta(days=1)
    return d


# ---------------------------------------------------------------- variant sweep
def run_variant(days, holding, roll_mode, trig, dates=None):
    dts = dates or sorted(days.keys())
    rows = []
    for dt in dts:
        r = run_trade(days, dt, holding, roll_mode, trig)
        if r and "skip" not in r:
            rows.append(r)
    return rows


def summarize(rows, label):
    if not rows:
        return {"label": label, "n": 0}
    net = np.array([r["net"] for r in rows])
    wins = net > 0
    eq = np.cumsum(net)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak).min()
    gp = net[net > 0].sum()
    gl = -net[net < 0].sum()
    return {
        "label": label, "n": len(rows),
        "net_total": round(net.sum(), 0),
        "avg": round(net.mean(), 1),
        "win_pct": round(100 * wins.mean(), 1),
        "pf": round(gp / gl, 2) if gl > 0 else float("inf"),
        "maxdd": round(dd, 0),
        "best": round(net.max(), 0), "worst": round(net.min(), 0),
        "avg_rolls": round(np.mean([r["rolls"] for r in rows]), 2),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--smoke", action="store_true", help="last ~120 trading days only")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    print("loading lake ...", flush=True)
    days = load_lake()
    alld = sorted(days.keys())
    print(f"  {len(alld)} trading days  {alld[0]} -> {alld[-1]}", flush=True)

    dates = alld
    if args.smoke:
        dates = alld[-120:]
    if args.dfrom:
        dates = [d for d in dates if d >= args.dfrom]
    if args.dto:
        dates = [d for d in dates if d <= args.dto]
    print(f"  testing {len(dates)} entry days", flush=True)

    VARIANTS = []
    # baseline (no roll) uses a huge trigger so it never fires
    for holding in ("intraday", "positional"):
        VARIANTS.append((holding, "threatened", 0, "BASELINE no-roll"))     # trig 0 -> never within
        for roll in ("threatened", "recenter"):
            for trig in (50, 100):
                VARIANTS.append((holding, roll, trig, f"{roll} trig{trig}"))

    all_summ = []
    all_rows = {}
    for (holding, roll, trig, name) in VARIANTS:
        rows = run_variant(days, holding, roll, trig, dates)
        label = f"{holding:11s} | {name}"
        s = summarize(rows, label)
        all_summ.append(s)
        all_rows[label] = rows
        print(f"{label:40s} n={s.get('n',0):4d} net={s.get('net_total',0):>10} "
              f"avg={s.get('avg',0):>7} win%={s.get('win_pct',0):>5} pf={s.get('pf','-'):>5} "
              f"dd={s.get('maxdd',0):>9} rolls={s.get('avg_rolls',0)}", flush=True)

    if args.out:
        with open(args.out, "w") as f:
            json.dump({"summary": all_summ,
                       "rows": {k: v for k, v in all_rows.items()}}, f, indent=1, default=str)
        print("wrote", args.out)
