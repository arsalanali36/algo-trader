"""Claim-2 FULL backtest — trap-bounce at extreme pivot -> NEXT-DAY continuation.

Screen (pivot_nextday.py) showed: plain breach = no edge; breach + 15:00 recovery
(the user's TRAP) = positive both directions. This runs the honest pipeline on the
bounce variant:
  spot pass (points x lot, 1x) -> train/OOS -> day-level rotation significance
  -> BS pass via bs_option.reprice_positional (BUY monthly ATM, overnight theta real)
Entry day-D 15:00 close (captures the overnight gap = the story's momentum);
exit next-day 15:00 close. No intraday management (screen shape), skip conflicting
same-day LONG+SHORT signals.
"""
import datetime as dt
import numpy as np
import pandas as pd
import intraday_engine as ie
import engine
import bs_option as bs

CUT = dt.time(15, 0)
START_CAP = 1_000_000.0


def day_frame(tf="5m"):
    d = ie.resample(ie.load_1m(), tf)
    lvls = ie._daily_levels(d, 20, 10.0)
    days = sorted(d["day"].unique())
    rows = {}
    for day, g in d.groupby("day"):
        pre = g[g.Datetime.dt.time <= CUT]
        if pre.empty:
            continue
        rows[day] = dict(cut_close=float(pre.Close.iloc[-1]),
                         pre_low=float(pre.Low.min()), pre_high=float(pre.High.max()),
                         open_=float(g.Open.iloc[0]),
                         cut_dt=pre.Datetime.iloc[-1], open_dt=g.Datetime.iloc[0])
    daily_close = d.groupby("day").Close.last()
    return days, rows, lvls, daily_close


def signals(days, rows, lvls, band=(3, 4, 5)):
    """per-day dir: +1 long (R-extreme trap) / -1 short (S-extreme trap) / 0."""
    sig = {}
    for day in days:
        if day not in rows:
            continue
        lv = lvls.get(day)
        if lv is None:
            continue
        r = rows[day]
        s_hit = any(idx < 6 and r["pre_low"] < lv["sup"][idx] for idx in band)
        r_hit = any(idx < 6 and r["pre_high"] > lv["res"][idx] for idx in band)
        sh = lo = False
        if s_hit:
            L = min(lv["sup"][i] for i in band if i < 6 and r["pre_low"] < lv["sup"][i])
            sh = r["cut_close"] > L          # bounced back above -> trap -> SHORT next day
        if r_hit:
            L = max(lv["res"][i] for i in band if i < 6 and r["pre_high"] > lv["res"][i])
            lo = r["cut_close"] < L          # rejected back below -> trap -> LONG next day
        if sh == lo:
            sig[day] = 0                     # none or conflict -> flat
        else:
            sig[day] = -1 if sh else 1
    return sig


def backtest(days, rows, sig, entry="eod", lot=75):
    trades = []
    eq = START_CAP
    eqc = []
    for i in range(len(days) - 1):
        day, nxt = days[i], days[i + 1]
        if day not in rows or nxt not in rows:
            continue
        s = sig.get(day, 0)
        if s == 0:
            eqc.append((rows[nxt]["cut_dt"], eq))
            continue
        ep = rows[day]["cut_close"] if entry == "eod" else rows[nxt]["open_"]
        e_dt = rows[day]["cut_dt"] if entry == "eod" else rows[nxt]["open_dt"]
        xp = rows[nxt]["cut_close"]
        pts = (xp - ep) * s
        pnl = pts * lot
        eq += pnl
        trades.append(dict(side="long" if s > 0 else "short", entry=ep, exit=xp,
                           entry_dt=str(e_dt), exit_dt=str(rows[nxt]["cut_dt"]),
                           points=round(pts, 1), pnl=pnl, qty=lot, bars=1,
                           reason="NextDay 15:00", entry_i=i, exit_i=i + 1))
        eqc.append((rows[nxt]["cut_dt"], eq))
    eqdf = pd.DataFrame(eqc, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=eq, mode="positional",
                variant="pivot_nextday", params=dict(entry=entry))


def day_sharpe(pnl_by_day):
    a = np.array(pnl_by_day, dtype=float)
    if a.std() == 0:
        return 0.0
    return float(a.mean() / a.std() * np.sqrt(252))


def rotation_sig(days, rows, sig, entry="eod", lot=75, n_perm=1000, seed=7):
    """day-level rotation: roll the signal vector across days, recompute sharpe."""
    ds = [d for d in days if d in rows]
    pos = np.array([sig.get(d, 0) for d in ds], dtype=float)
    # next-interval return per day (15:00 -> next 15:00), aligned with pos[i]
    ret = np.zeros(len(ds))
    for i in range(len(ds) - 1):
        ep = rows[ds[i]]["cut_close"] if entry == "eod" else rows[ds[i + 1]]["open_"]
        ret[i] = (rows[ds[i + 1]]["cut_close"] - ep) * lot
    real = day_sharpe(pos * ret)
    rng = np.random.default_rng(seed)
    null = np.empty(n_perm)
    for i in range(n_perm):
        k = int(rng.integers(5, len(pos) - 5))
        null[i] = day_sharpe(np.roll(pos, k) * ret)
    p = float((null >= real).mean())
    return dict(real_sharpe=real, p_value=p, null_p95=float(np.percentile(null, 95)))


def metr(res):
    m, _ = engine.metrics(res)
    return m


def split_days(days, frac=0.7):
    cut = days[int(len(days) * frac)]
    return cut


def main():
    lot = bs.get_nifty_lot()
    days, rows, lvls, daily_close = day_frame()
    sigma_map = bs.realised_vol_map(daily_close)
    cut = split_days(days)
    print(f"days={len(days)}  train<= {cut}  lot={lot}\n")
    hdr = f"{'band':>12s}{'entry':>9s} | {'N':>4s}{'net%':>7s}{'sh':>6s}{'dd':>6s} | tr/oos sh | {'p':>6s} | BS: {'net%':>6s}{'sh':>6s}{'dd':>6s}{'fees':>8s}"
    print(hdr)
    best = None
    for band in [(3, 4, 5), (2, 3, 4, 5), (4, 5)]:
        for entry in ("eod", "nxtopen"):
            sig = signals(days, rows, lvls, band)
            res = backtest(days, rows, sig, entry, lot)
            if len(res["trades"]) < 60:
                continue
            m = metr(res)
            # train/oos on spot
            tr_days = [d for d in days if d <= cut]; oo_days = [d for d in days if d >= cut]
            m_tr = metr(backtest(tr_days, rows, sig, entry, lot))
            m_oo = metr(backtest(oo_days, rows, sig, entry, lot))
            s = rotation_sig(days, rows, sig, entry, lot)
            # BS pass (monthly ATM buy)
            bstr = bs.reprice_positional(res["trades"], sigma_map, lot, 1)
            eq = START_CAP; eqc = []
            for t in bstr:
                eq += t["pnl"]; eqc.append((t["exit_dt"], eq))
            bres = dict(trades=[dict(t, entry=t["entry_prem"], exit=t["exit_prem"]) for t in bstr],
                        equity=pd.DataFrame(eqc, columns=["Datetime", "equity"]),
                        final=eq, mode="positional", variant="pivot_nextday_bs",
                        params=dict(entry=entry))
            bm = metr(bres)
            fees = sum(t["fee"] for t in bstr)
            row = (f"{str(list(band)):>12s}{entry:>9s} | {m['trades']:>4d}{m['net_pct']:>7.1f}"
                   f"{m['sharpe']:>6.2f}{m['maxdd']:>6.1f} | {m_tr['sharpe']:>4.2f}/{m_oo['sharpe']:>4.2f} | "
                   f"{s['p_value']:>6.3f} | {bm['net_pct']:>9.1f}{bm['sharpe']:>6.2f}{bm['maxdd']:>6.1f}{fees:>8.0f}")
            print(row)
            key = min(m_tr["sharpe"], m_oo["sharpe"])
            if s["p_value"] < 0.05 and (best is None or key > best[0]):
                best = (key, band, entry, bm, s)
    print()
    if best:
        print(f"BEST significant: band={list(best[1])} entry={best[2]} "
              f"min(tr,oos)={best[0]:.2f} p={best[4]['p_value']:.3f} BS sharpe={best[3]['sharpe']:.2f}")
    else:
        print("NO variant passed p<0.05")


if __name__ == "__main__":
    import warnings; warnings.filterwarnings("ignore")
    main()
