"""
Weekly positional iron-fly (user spec 2026-08-24) — REAL premium backtest on
OptChainLake_1m/NIFTY/WEEK (2021-07 -> 2026-07). RESEARCH ONLY. No live/order path.

STRATEGY
  Entry   : the first trading day AFTER a weekly expiry, at 09:20.
            SELL ATM CE + SELL ATM PE (short straddle, both at round50(spot)) = max premium.
  Hedge   : BUY CE at ATM+250 (+5 strikes) + BUY PE at ATM-250 (-5 strikes) -> iron fly,
            defined risk. (user: "+-5 ka hedge")
  Size    : 5 lots (5 x 65 = 325 units).
  Exit    : combined running P&L >= TAKE_PCT of net credit (max profit) -> close all.
            No SL (pure-edge test). Positional: hold across the whole week; if target
            not hit, settle at that week's expiry 15:20 (leftover leg -> intrinsic).
  Charges : real date-aware Zerodha F&O (charges.py), 5-lot qty, per round-tripped leg.

All premiums REAL (lake). A required strike missing at the needed minute = honest
data-gap -> trade flagged, not faked.
"""
import os, sys, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "nifty_trend"))
sys.path.insert(0, os.path.join(HERE, "..", "strangle_roll"))  # must win over nifty_trend/engine.py
import charges as CH
from engine import (load_lake, _prem, _spot_at, _minutes, _round50,
                    LOT, ENTRY_HM, POS_EXIT_HM, _nearest_weekly)

WING = 250          # +-5 strikes hedge (default; overridden per-run below)
LOTS = 5
QTY  = LOTS * LOT   # 325 units


def entry_days(days):
    """First lake trading day strictly after each weekly expiry."""
    alld = sorted(days.keys())
    out = []
    for i in range(1, len(alld)):
        prev = alld[i - 1]
        if str(_nearest_weekly(prev)) == prev:      # prev was a weekly expiry
            out.append(alld[i])
    return out


def run_trade(days, entry_date, take_pct, wing=WING):
    grid = days.get(entry_date)
    if not grid:
        return None
    spot0 = _spot_at(grid, ENTRY_HM)
    if spot0 is None:
        return None
    atm = _round50(spot0)
    ce_wk = atm + wing
    pe_wk = atm - wing

    # legs: sell ATM CE+PE, buy wings
    sce, _ = _prem(grid, ENTRY_HM, "CE", atm)
    spe, _ = _prem(grid, ENTRY_HM, "PE", atm)
    bce, _ = _prem(grid, ENTRY_HM, "CE", ce_wk)
    bpe, _ = _prem(grid, ENTRY_HM, "PE", pe_wk)
    if None in (sce, spe, bce, bpe):
        return {"date": entry_date, "skip": "entry_strike_missing"}

    # net credit (max profit) per unit
    entry_credit = (sce + spe) - (bce + bpe)
    if entry_credit <= 0:
        return {"date": entry_date, "skip": "nonpositive_credit"}
    target = take_pct * entry_credit

    # book: (ot,strike) -> {'side','p0'}
    book = {("CE", atm): {"side": "SELL", "p0": sce},
            ("PE", atm): {"side": "SELL", "p0": spe},
            ("CE", ce_wk): {"side": "BUY", "p0": bce},
            ("PE", pe_wk): {"side": "BUY", "p0": bpe}}
    cash = entry_credit          # points collected net (per unit)
    last = {k: v["p0"] for k, v in book.items()}

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
        # MTM P&L vs entry: 0 at entry, rises to entry_credit at max profit.
        tot = 0.0
        for (ot, k), leg in book.items():
            p, _ = _prem(dgrid, m, ot, k)
            if p is not None:
                last[(ot, k)] = p
            p = last[(ot, k)]
            # BUY leg gains value adds to P&L, SELL leg loses when it gains value
            tot += (p - leg["p0"]) if leg["side"] == "BUY" else (leg["p0"] - p)
        return tot

    charge_legs = []   # (p0, exit_p, side, when)
    exited = False; reason = None; exit_day = deadline[0]; peak = 0.0
    for (d, lo, hi) in seq:
        dgrid = days.get(d)
        if not dgrid:
            continue
        for m in _minutes(dgrid, lo, hi):
            rp = running(dgrid, m)
            if rp > peak:
                peak = rp
            if rp >= target:
                for (ot, k), leg in list(book.items()):
                    p, _ = _prem(dgrid, m, ot, k)
                    charge_legs.append((leg["p0"], p if p is not None else last[(ot, k)],
                                        leg["side"], d))
                book.clear()
                exited = True; reason = "target"; exit_day = d
                break
        if exited:
            break

    if not exited:
        d, hm = deadline
        dgrid = days.get(d)
        sp = (_spot_at(dgrid, hm) if dgrid else None) or spot0
        for (ot, k), leg in list(book.items()):
            p = None
            if dgrid:
                p, _ = _prem(dgrid, hm, ot, k)
            if p is None:
                p = max(0.0, sp - k) if ot == "CE" else max(0.0, k - sp)
            charge_legs.append((leg["p0"], p, leg["side"], d))
        book.clear()
        reason = "expiry"; exit_day = d

    # realised points = sum over legs
    pts = 0.0
    for (p0, xp, side, w) in charge_legs:
        pts += (xp - p0) if side == "BUY" else (p0 - xp)
    gross = pts * QTY
    charge = sum(CH.option_charges(p0, xp, QTY, entry_side=side, when=w)
                 for (p0, xp, side, w) in charge_legs)
    return {"date": entry_date, "exit_date": exit_day, "expiry": exp,
            "entry_credit": round(entry_credit, 2), "target_pts": round(target, 2),
            "peak_pts": round(peak, 2), "spot0": round(spot0, 1), "atm": atm,
            "gross": round(gross, 1), "charges": round(charge, 1),
            "net": round(gross - charge, 1), "reason": reason,
            "hold_days": len(set(w for (_, _, _, w) in charge_legs)) or 1}


def run(days, take_pct, wing=WING):
    out = []
    for d in entry_days(days):
        r = run_trade(days, d, take_pct, wing)
        if r and "skip" not in r:
            out.append(r)
    return out


def stats(rows, label):
    if not rows:
        return {"label": label, "n": 0}
    net = np.array([r["net"] for r in rows])
    eq = np.cumsum(net); dd = (eq - np.maximum.accumulate(eq)).min()
    gp = net[net > 0].sum(); gl = -net[net < 0].sum()
    tgt = sum(1 for r in rows if r["reason"] == "target")
    yrs = (int(max(r["date"] for r in rows)[:4]) - int(min(r["date"] for r in rows)[:4])) + 1
    sharpe = (net.mean() / net.std()) * np.sqrt(len(net) / max(1, yrs)) if net.std() else 0
    return {"label": label, "n": len(rows), "net": round(net.sum()),
            "avg": round(net.mean(), 1), "win": round(100 * (net > 0).mean(), 1),
            "pf": round(gp / gl, 2) if gl else 99.0, "maxdd": round(dd),
            "best": round(net.max()), "worst": round(net.min()),
            "target_hit": tgt, "expiry_settle": len(rows) - tgt,
            "sharpe": round(sharpe, 2)}


def yearly(rows):
    ys = {}
    for r in rows:
        ys.setdefault(r["date"][:4], []).append(r["net"])
    return {y: {"n": len(v), "net": round(sum(v)), "win": round(100 * np.mean([x > 0 for x in v]), 1)}
            for y, v in sorted(ys.items())}


def boot_p(net, iters=10000, seed=7):
    rng = np.random.default_rng(seed)
    net = np.asarray(net, float)
    if len(net) < 5:
        return 1.0
    means = net[rng.integers(0, len(net), size=(iters, len(net)))].mean(axis=1)
    return float((means <= 0).mean())


if __name__ == "__main__":
    print("loading lake ...", flush=True)
    days = load_lake()
    alld = sorted(days.keys())
    print(f"  {len(alld)} trading days  {alld[0]} -> {alld[-1]}", flush=True)
    eds = entry_days(days)
    print(f"  {len(eds)} weekly entry days (first day after each expiry)\n", flush=True)

    out = {"lots": LOTS, "variants": {}}
    for wing in (250, 350):
        print(f"\n############ WING +-{wing//50} strikes (+-{wing} pts) ############")
        for pct, name in [(0.50, "TARGET 50% of credit"),
                          (0.75, "TARGET 75% of credit"),
                          (1.01, "HOLD to expiry (no target)")]:
            rows = run(days, pct, wing)
            s = stats(rows, name)
            s["p_value"] = round(boot_p([r["net"] for r in rows]), 4)
            s["yearly"] = yearly(rows)
            tr = [r for r in rows if r["date"] < "2025-01-01"]
            oos = [r for r in rows if r["date"] >= "2025-01-01"]
            s["train"] = stats(tr, "train"); s["oos"] = stats(oos, "oos")
            out["variants"][f"wing{wing} | {name}"] = {"stats": s, "rows": rows}
            print(f"== +-{wing} | {name} ==")
            print(f"  trades={s['n']}  net(5lot)=Rs {s['net']:,}  avg/trade=Rs {s['avg']:,}  "
                  f"win={s['win']}%  PF={s['pf']}  Sharpe={s['sharpe']}  p={s['p_value']}")
            print(f"  maxDD=Rs {s['maxdd']:,}  best=Rs {s['best']:,}  worst=Rs {s['worst']:,}  "
                  f"target-hit={s['target_hit']}  expiry-settle={s['expiry_settle']}")
            print(f"  train net=Rs {s['train'].get('net',0):,} (n{s['train'].get('n',0)})  "
                  f"OOS net=Rs {s['oos'].get('net',0):,} (n{s['oos'].get('n',0)})")
            print(f"  yearly: " + "  ".join(f"{y}:Rs{v['net']:,}(n{v['n']},{v['win']}%)"
                                            for y, v in s["yearly"].items()))

    json.dump(out, open(os.path.join(HERE, "results.json"), "w"), indent=1, default=str)
    print("\nwrote results.json")
