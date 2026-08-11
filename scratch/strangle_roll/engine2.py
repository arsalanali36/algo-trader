"""
engine2.py — v2: adds (1) cheap protective HEDGE (iron-strangle, wing within lake's
ATM+/-10 window), (2) SEQUENTIAL positional book (no re-entry until the open position
exits/expires — user rule), (3) train/OOS split + bootstrap significance.

Reuses engine.load_lake / _prem / _spot_at / _minutes / _round50 + charges.py (Rule 6B).
Naked (wing=0) reproduces engine.py's leg logic. RESEARCH ONLY.
"""
import os, sys, json
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "nifty_trend"))
sys.path.insert(0, HERE)   # HERE must win: local engine.py shadows nifty_trend/engine.py
import charges as CH
import expiry_calendar as EC
from engine import (load_lake, _prem, _spot_at, _minutes, _round50,
                    STEP, LOT, DIST, ENTRY_HM, INTRADAY_EXIT_HM, POS_EXIT_HM,
                    MAX_ROLLS_PER_DAY, _nearest_weekly)


def run_trade(days, entry_date, holding, roll_mode, trig, wing=0, take_pct=0.50):
    """One strangle. wing>0 => buy a protective same-side wing `wing` pts beyond each
       sold strike (defined risk). Returns result dict incl. exit_date, or None/skip."""
    grid = days.get(entry_date)
    if not grid:
        return None
    spot0 = _spot_at(grid, ENTRY_HM)
    if spot0 is None:
        return None

    # open book: {(ot,strike): {'side','p0','last'}}, sidelegs maps a side->its leg keys
    book = {}
    sidelegs = {"CE": [], "PE": []}
    cash = 0.0
    charge_legs = []           # (entry_prem, exit_prem, side, when)

    def openleg(ot, k, side, p, when):
        nonlocal cash
        cash += p if side == "SELL" else -p
        book[(ot, k)] = {"side": side, "p0": p, "last": p}
        sidelegs[ot].append((ot, k))

    def closeleg(ot, k, p, when):
        nonlocal cash
        leg = book.pop((ot, k))
        cash += (-p if leg["side"] == "SELL" else p)
        charge_legs.append((leg["p0"], p, leg["side"], when))
        sidelegs[ot].remove((ot, k))

    def open_side(ot, spot, when, hm=ENTRY_HM):
        """establish sold leg (+hedge) for one side at 250 from spot. False if data-gap."""
        sk = _round50(spot + DIST) if ot == "CE" else _round50(spot - DIST)
        sp, _ = _prem(grid_of(when), hm, ot, sk)
        if sp is None:
            return False
        hk = None; hp = None
        if wing > 0:
            hk = _round50(sk + wing) if ot == "CE" else _round50(sk - wing)
            hp, _ = _prem(grid_of(when), hm, ot, hk)
            if hp is None:
                return False
        openleg(ot, sk, "SELL", sp, when)
        if wing > 0:
            openleg(ot, hk, "BUY", hp, when)
        return True

    _grid_cache = {}
    def grid_of(d):
        if d not in _grid_cache:
            _grid_cache[d] = days.get(d)
        return _grid_cache[d]

    # ---- ENTRY (both sides) at entry_date 9:20
    if not open_side("CE", spot0, entry_date) or not open_side("PE", spot0, entry_date):
        return {"date": entry_date, "skip": "entry_strike_missing"}
    entry_credit = cash          # net credit received (sells - hedge cost)
    if entry_credit <= 0:
        return {"date": entry_date, "skip": "nonpositive_credit"}
    target = take_pct * entry_credit

    # ---- day sequence
    if holding == "intraday":
        seq = [(entry_date, ENTRY_HM + 1, INTRADAY_EXIT_HM)]
        deadline = (entry_date, INTRADAY_EXIT_HM)
    else:
        exp = str(_nearest_weekly(entry_date))
        alldays = sorted(days.keys())
        seq = []
        for d in alldays:
            if d < entry_date or d > exp:
                continue
            lo = ENTRY_HM + 1 if d == entry_date else 916
            hi = POS_EXIT_HM if d == exp else 1529
            seq.append((d, lo, hi))
        deadline = (seq[-1][0], POS_EXIT_HM) if seq else (entry_date, POS_EXIT_HM)

    def running(dgrid, m):
        tot = cash
        for (ot, k), leg in book.items():
            p, _ = _prem(dgrid, m, ot, k)
            if p is not None:
                leg["last"] = p
            p = leg["last"]
            tot += (p if leg["side"] == "BUY" else -p)
        return tot

    rolls = 0
    exited = False; reason = None; exit_day = deadline[0]
    for (d, lo, hi) in seq:
        dgrid = grid_of(d)
        if not dgrid:
            continue
        for m in _minutes(dgrid, lo, hi):
            spot = dgrid[m]["spot"]
            rp = running(dgrid, m)
            if rp >= target:
                for (ot, k), leg in list(book.items()):
                    p, _ = _prem(dgrid, m, ot, k)
                    closeleg(ot, k, p if p is not None else leg["last"], d)
                exited = True; reason = "target"; exit_day = d
                break
            if rolls < MAX_ROLLS_PER_DAY:
                touched = []
                for ot in ("CE", "PE"):
                    sk = next((k for (o, k) in sidelegs[ot] if book[(o, k)]["side"] == "SELL"), None)
                    if sk is not None and abs(spot - sk) <= trig:
                        touched.append(ot)
                sides = ["CE", "PE"] if (roll_mode == "recenter" and touched) else touched
                for ot in sides:
                    # new target strike; skip if unchanged
                    nsk = _round50(spot + DIST) if ot == "CE" else _round50(spot - DIST)
                    cur_sk = next((k for (o, k) in sidelegs[ot] if book[(o, k)]["side"] == "SELL"), None)
                    if cur_sk == nsk:
                        continue
                    # stage new prems
                    nsp, _ = _prem(dgrid, m, ot, nsk)
                    nhk = (_round50(nsk + wing) if ot == "CE" else _round50(nsk - wing)) if wing else None
                    nhp = None
                    if wing:
                        nhp, _ = _prem(dgrid, m, ot, nhk)
                    if nsp is None or (wing and nhp is None):
                        continue
                    # close this side's legs
                    for (o, k) in list(sidelegs[ot]):
                        p, _ = _prem(dgrid, m, o, k)
                        closeleg(o, k, p if p is not None else book[(o, k)]["last"], d)
                    openleg(ot, nsk, "SELL", nsp, d)
                    if wing:
                        openleg(ot, nhk, "BUY", nhp, d)
                    rolls += 1
        if exited:
            break

    if not exited:
        d, hm = deadline
        dgrid = grid_of(d)
        sp = _spot_at(dgrid, hm) if dgrid else spot0
        sp = sp if sp is not None else spot0
        for (ot, k), leg in list(book.items()):
            p = None
            if dgrid:
                p, _ = _prem(dgrid, hm, ot, k)
            if p is None:
                p = max(0.0, sp - k) if ot == "CE" else max(0.0, k - sp)
            closeleg(ot, k, p, d)
        reason = "deadline"; exit_day = d

    gross = cash * LOT
    charge = sum(CH.option_charges(e, x, LOT, entry_side=s, when=w) for (e, x, s, w) in charge_legs)
    return {"date": entry_date, "exit_date": exit_day, "holding": holding,
            "roll": roll_mode, "trig": trig, "wing": wing,
            "entry_credit": round(entry_credit, 2), "spot0": round(spot0, 1),
            "rolls": rolls, "gross": round(gross, 1), "charges": round(charge, 1),
            "net": round(gross - charge, 1), "reason": reason, "n_legs": len(charge_legs)}


def run_intraday(days, roll_mode, trig, wing, dates):
    out = []
    for d in dates:
        r = run_trade(days, d, "intraday", roll_mode, trig, wing)
        if r and "skip" not in r:
            out.append(r)
    return out


def run_positional_seq(days, roll_mode, trig, wing, dates):
    """sequential: after a position exits, next entry is the first date AFTER exit_date."""
    dates = sorted(dates)
    n = len(dates); idx = 0; out = []
    while idx < n:
        d = dates[idx]
        r = run_trade(days, d, "positional", roll_mode, trig, wing)
        if r and "skip" not in r:
            out.append(r)
            exd = r.get("exit_date", d)
            while idx < n and dates[idx] <= exd:
                idx += 1
        else:
            idx += 1
    return out


def stats(rows):
    if not rows:
        return dict(n=0)
    net = np.array([r["net"] for r in rows])
    eq = np.cumsum(net); dd = (eq - np.maximum.accumulate(eq)).min()
    gp = net[net > 0].sum(); gl = -net[net < 0].sum()
    return dict(n=len(net), net=round(net.sum()), avg=round(net.mean(), 1),
                win=round(100 * (net > 0).mean(), 1),
                pf=round(gp / gl, 2) if gl else 99.0, dd=round(dd),
                best=round(net.max()), worst=round(net.min()),
                std=round(net.std(), 1), rolls=round(np.mean([r["rolls"] for r in rows]), 2))


def boot_p(net, iters=10000, seed=7):
    """one-sample bootstrap p-value: P(resampled mean <= 0). Non-overlapping trades ~indep."""
    rng = np.random.default_rng(seed)
    net = np.asarray(net, float)
    if len(net) < 5:
        return 1.0
    means = net[rng.integers(0, len(net), size=(iters, len(net)))].mean(axis=1)
    return float((means <= 0).mean())


def significance(rows, split="2025-01-01"):
    tr = [r for r in rows if r["date"] < split]
    oos = [r for r in rows if r["date"] >= split]
    net_all = [r["net"] for r in rows]
    st, so, sa = stats(tr), stats(oos), stats(rows)
    # per-trade Sharpe -> annualized (trades/yr from span)
    yrs = (max(r["date"] for r in rows)[:4], min(r["date"] for r in rows)[:4])
    span_yrs = max(1.0, (int(yrs[0]) - int(yrs[1])) + 1)
    sharpe = round((sa["avg"] / sa["std"]) * np.sqrt(sa["n"] / span_yrs), 2) if sa.get("std") else 0
    return dict(train=st, oos=so, full=sa,
                p_full=round(boot_p(net_all), 4),
                p_train=round(boot_p([r["net"] for r in tr]), 4),
                p_oos=round(boot_p([r["net"] for r in oos]), 4),
                sharpe_ann=sharpe,
                gate_pass=bool(st.get("avg", -1) > 0 and so.get("avg", -1) > 0
                               and round(boot_p(net_all), 4) < 0.05))


if __name__ == "__main__":
    print("loading lake ...", flush=True)
    days = load_lake()
    alld = sorted(days.keys())
    print(f"  {len(alld)} days {alld[0]}->{alld[-1]}", flush=True)

    WINGS = [0, 150, 250]     # 0=naked, 150=hedge spot+/-400, 250=hedge spot+/-500 (cheapest in lake)
    results = {}

    # ---- INTRADAY: baseline + best roll (threatened trig100), each wing
    print("\n== INTRADAY (every day, 15:10 exit) ==", flush=True)
    for wing in WINGS:
        for (roll, trig, name) in [("threatened", 0, "baseline"), ("threatened", 100, "roll t100")]:
            rows = run_intraday(days, roll, trig, wing, alld)
            key = f"intraday | {name} | wing{wing}"
            results[key] = rows
            s = stats(rows)
            print(f"{key:34s} n={s['n']:4d} net={s['net']:>9} avg={s['avg']:>6} "
                  f"win={s['win']:>5} pf={s['pf']:>5} dd={s['dd']:>9}", flush=True)

    # ---- POSITIONAL SEQUENTIAL: baseline + rolls, naked vs hedged
    print("\n== POSITIONAL (sequential, no overlap, to weekly expiry) ==", flush=True)
    for wing in [0, 250]:
        for (roll, trig, name) in [("threatened", 0, "baseline"),
                                   ("threatened", 50, "thr t50"),
                                   ("threatened", 100, "thr t100"),
                                   ("recenter", 100, "rec t100")]:
            rows = run_positional_seq(days, roll, trig, wing, alld)
            key = f"positional | {name} | wing{wing}"
            results[key] = rows
            s = stats(rows)
            print(f"{key:34s} n={s['n']:4d} net={s['net']:>9} avg={s['avg']:>6} "
                  f"win={s['win']:>5} pf={s['pf']:>5} dd={s['dd']:>9} rolls={s['rolls']}", flush=True)

    # ---- SIGNIFICANCE on the headline positional configs
    print("\n== SIGNIFICANCE (train<2025-01 | OOS>=2025-01, bootstrap p) ==", flush=True)
    sig = {}
    for key in ["positional | thr t100 | wing0", "positional | thr t100 | wing250",
                "positional | rec t100 | wing250", "positional | baseline | wing0"]:
        if key in results:
            z = significance(results[key])
            sig[key] = z
            print(f"{key:34s} train avg={z['train'].get('avg')}/n{z['train'].get('n')} "
                  f"OOS avg={z['oos'].get('avg')}/n{z['oos'].get('n')} "
                  f"p_full={z['p_full']} sharpe={z['sharpe_ann']} GATE={'PASS' if z['gate_pass'] else 'fail'}",
                  flush=True)

    json.dump({"rows": results, "sig": sig}, open(os.path.join(HERE, "hedge_results.json"), "w"),
              indent=1, default=str)
    print("\nwrote hedge_results.json", flush=True)
