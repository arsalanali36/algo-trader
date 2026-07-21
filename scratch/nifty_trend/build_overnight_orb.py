"""Builder: Overnight ORB -> runs/overnight_orb_nifty/{results.js, meta.json, index.html}.

Same ENTRY as Mid-Day ORB (tod_orb, or_min=30/orb_k=1.0/11:00-14:00/atr_sl=1.5) but:
  - NO 3:15 exit, NO RR target; entry-day ATR stop only.
  - if not stopped -> HOLD OVERNIGHT -> exit next trading day 09:20 (real 1-min spot).
Passes: instrument (spot) / rms (= instrument; daily caps don't apply to overnight, Rule 10)
        / bs (MONTHLY ATM BUY, reprice_positional). Periods: full/train/oos.

⚠️ BS under-prices the theta an option BUYER holding overnight bleeds (TRAP #136).
   These are RESEARCH figures — validate on the real lake / forward-paper Real-vs-BS.

Run:  python -X utf8 build_overnight_orb.py
"""
import os, sys, json, datetime as dt, shutil
import numpy as np, pandas as pd
import engine, intraday_engine as ie, bs_option as bs
import run_hunt as rh
import significance as sig, montecarlo as mc

HERE = os.path.dirname(os.path.abspath(__file__))
SLUG = "overnight_orb_nifty"
TITLE = "00 - Overnight ORB (NIFTY, naked ATM, next-day exit)"
DESIGN_HUMAN = "Overnight Opening-Range Breakout (Mid-Day entry, hold to next 09:20)"
SPLIT_DATE = "2024-06-30"
BASE = dict(or_min=30, orb_k=1.0, h0=11, h1=14, atr_sl=1.5)
RISK_PCT, LEV_CAP = 0.015, 1.0


# ---------- 1-min next-day-open lookup ----------
def build_1m_lookup(df1m):
    df = df1m.copy()
    df["date"] = df.Datetime.dt.date
    df["hm"] = df.Datetime.dt.strftime("%H:%M")
    df = df.drop_duplicates(subset=["date", "hm"], keep="first")
    op = df.set_index(["date", "hm"])["Open"].sort_index()
    days = sorted(df["date"].unique())
    return op, days, {d: k for k, d in enumerate(days)}


def spot_at(op, day, hm):
    base = dt.datetime.strptime(hm, "%H:%M")
    for add in range(0, 11):
        h = (base + dt.timedelta(minutes=add)).strftime("%H:%M")
        try:
            v = op.loc[(day, h)]
            return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
        except KeyError:
            continue
    return None


# ---------- overnight backtest (exit next-day 09:20, no target, max-2/day) ----------
def overnight(d15, op, exit_hm="09:20", max_day=2):
    p = dict(ie.DEFAULTS.get("tod_orb", {})); p.update(BASE)
    long_e, short_e = ie.design_signals(d15, "tod_orb", p)
    a = engine.atr(d15, 14).values
    O, H, L, C = d15.Open.values, d15.High.values, d15.Low.values, d15.Close.values
    DAY, DT = d15.day.values, d15.Datetime.values
    n = len(d15); warm = 60; atr_sl = BASE["atr_sl"]
    equity = engine.START_CAP; pos = None; trades = []
    cur_day = None; entries_today = 0

    def close_pos(px, xdt, i, reason):
        nonlocal equity, pos
        sgn = 1 if pos["side"] == "long" else -1
        pnl = (px - pos["entry"]) * pos["qty"] * sgn
        fee = 40.0 + abs(px * pos["qty"]) * 0.0002
        equity += pnl - fee
        trades.append(dict(side=pos["side"], entry=pos["entry"], exit=px, qty=pos["qty"],
                           pnl=pnl - fee, points=(px - pos["entry"]) * sgn,
                           entry_i=pos["entry_i"], exit_i=i, entry_dt=pos["entry_dt"],
                           exit_dt=xdt, reason=reason, bars=i - pos["entry_i"]))
        pos = None

    for i in range(warm, n):
        if DAY[i] != cur_day:
            cur_day = DAY[i]; entries_today = 0
        if pos is not None:
            if DAY[i] != pos["entry_day"]:
                px = spot_at(op, DAY[i], exit_hm) or O[i]
                xdt = pd.Timestamp(DAY[i]) + pd.Timedelta(exit_hm + ":00")
                close_pos(px, xdt, i, "NextDay 09:20")
            else:
                side = pos["side"]
                if side == "long" and L[i] <= pos["stop"]:
                    close_pos(pos["stop"], DT[i], i, "ATR SL")
                elif side == "short" and H[i] >= pos["stop"]:
                    close_pos(pos["stop"], DT[i], i, "ATR SL")
        if pos is None and entries_today < max_day and i + 1 < n:
            lo, sh = long_e[i], short_e[i]
            if lo or sh:
                side = "long" if lo else "short"; entry = O[i + 1]
                av = a[i] if a[i] > 0 else max(1.0, entry * 0.001); sd = atr_sl * av
                qty = min((RISK_PCT * equity) / max(sd, 1e-6), LEV_CAP * equity / entry)
                stop = entry - sd if side == "long" else entry + sd
                pos = dict(side=side, entry=entry, qty=qty, stop=stop, target=None,
                           entry_i=i + 1, entry_dt=DT[i + 1], entry_day=DAY[i + 1])
                entries_today += 1
    if pos is not None:
        close_pos(C[n - 1], DT[n - 1], n - 1, "End")
    return trades


def _spot_res(trades, d):
    add = np.zeros(len(d))
    for t in trades:
        ei = int(t.get("exit_i", 0))
        if 0 <= ei < len(d):
            add[ei] += t["pnl"]
    eqv = engine.START_CAP + np.cumsum(add)
    return dict(trades=trades, equity=pd.DataFrame({"Datetime": d.Datetime.values, "equity": eqv}),
                final=float(eqv[-1]), variant="overnight_orb", mode="positional", params={})


def _bs_view(trades, d, sigma_map, lot):
    opt = bs.reprice_positional(trades, sigma_map, lot, lots=1)
    etr = [dict(side=o["side"], entry=o["entry_prem"], exit=o["exit_prem"], qty=o["qty"],
                pnl=o["pnl"], points=o["points"], bars=o["bars"], reason=o["reason"],
                entry_i=o["entry_i"], exit_i=o["exit_i"], entry_dt=o["entry_dt"], exit_dt=o["exit_dt"])
           for o in opt]
    add = np.zeros(len(d))
    for o in opt:
        if 0 <= o["exit_i"] < len(d):
            add[o["exit_i"]] += o["pnl"]
    eqv = engine.START_CAP + np.cumsum(add)
    bs_res = dict(trades=etr, equity=pd.DataFrame({"Datetime": d.Datetime.values, "equity": eqv}),
                  final=float(eqv[-1]), variant="overnight_orb", mode="positional", params={})
    at = []
    for o in opt:
        o = dict(o); o["entry"] = o["entry_spot"]; o["exit"] = o["exit_spot"]
        o.pop("entry_i", None); o.pop("exit_i", None); at.append(o)
    return bs_res, at, round(sum(o["fee"] for o in opt), 0)


def build():
    df1m = ie.load_1m(); d15 = ie.resample(df1m, "15m"); lot = bs.get_nifty_lot()
    dd = (d15.set_index("Datetime").resample("1D")
            .agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna())
    sigma_map = bs.realised_vol_map(dd.Close)
    op, days, _ = build_1m_lookup(df1m)
    candles = [[str(ix.date()), round(r.Open, 1), round(r.High, 1), round(r.Low, 1), round(r.Close, 1)]
               for ix, r in dd.iterrows()]

    tr_full = overnight(d15, op)
    pos = sig.position_series(d15, tr_full)
    ret = d15.Close.pct_change().shift(-1).fillna(0).values
    real = sig._sharpe_from_bars(pos * ret, d15.Datetime.values)
    rng = np.random.default_rng(7); N = len(pos); null = np.empty(1000)
    for i in range(1000):
        k = rng.integers(25, N - 25); null[i] = sig._sharpe_from_bars(np.roll(pos, k) * ret, d15.Datetime.values)
    p = float((null >= real).mean())
    SIG = dict(real_sharpe=round(float(real), 3), p_value=round(p, 4),
               null_p95=round(float(np.percentile(null, 95)), 3), null_mean=round(float(null.mean()), 3),
               n_perm=1000, significant=bool(p < 0.05))

    def slc(lo, hi):
        dts = pd.to_datetime(d15.Datetime); m = pd.Series(True, index=d15.index)
        if lo: m &= dts >= pd.Timestamp(lo)
        if hi: m &= dts <= pd.Timestamp(hi + " 23:59")
        return d15[m].reset_index(drop=True)

    combos = {}
    for period, (lo, hi) in (("full", (None, None)), ("train", (None, SPLIT_DATE)), ("oos", ("2024-07-01", None))):
        dp = slc(lo, hi)
        trades = overnight(dp, op)
        sres = _spot_res(trades, dp); sat = rh._spot_trades(sres)
        combos[f"instrument|{period}"] = rh._combo_from_res(sres, dp, sat, SIG)
        combos[f"rms|{period}"] = rh._combo_from_res(sres, dp, sat, SIG)
        bres, bat, bfee = _bs_view(trades, dp, sigma_map, lot)
        combos[f"bs|{period}"] = rh._combo_from_res(bres, dp, bat, SIG, fees_override=bfee)

    RESULTS = {"meta": {"window": [str(d15.Datetime.min())[:10], str(d15.Datetime.max())[:10]],
                        "days": int(d15.day.nunique()), "start_cap": int(engine.START_CAP),
                        "design": DESIGN_HUMAN, "tf": "15m", "candles": candles,
                        "passes": ["instrument", "rms", "bs"], "periods": ["full", "train", "oos"],
                        "instrument": "NIFTY 50", "lot_size": lot, "lots": 1},
               "combos": combos}

    outdir = os.path.join(HERE, "runs", SLUG); os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(RESULTS, ensure_ascii=False) + ";\n")
    tmpl = os.path.join(HERE, "dashboard_intraday.html")
    if os.path.exists(tmpl):
        shutil.copyfile(tmpl, os.path.join(outdir, "index.html"))

    bsf, bst, bso = (combos[f"bs|{k}"]["metrics"] for k in ("full", "train", "oos"))
    meta = {"slug": SLUG, "design": "overnight_orb", "title": TITLE, "tf": "15m",
            "params": {"or_min": 30, "orb_k": 1.0, "h0": 11, "h1": 14, "atr_sl": 1.5,
                       "rr": None, "exit": "nextday_0920", "hold": "overnight", "option": "monthly_atm_buy"},
            "exit": "nextday_0920", "instrument": "NIFTY 50", "lot_size": lot,
            "window": RESULTS["meta"]["window"], "days": RESULTS["meta"]["days"],
            "significant": SIG["significant"],
            "bs_full": {k: bsf[k] for k in ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
            "bs_train_sharpe": bst["sharpe"], "bs_oos_sharpe": bso["sharpe"],
            "p_value": SIG["p_value"], "config_key": "orb_overnight_v1",
            "note": "BS = Black-Scholes MONTHLY ATM buy. Option BUYER + OVERNIGHT hold => BS "
                    "under-prices theta (TRAP #136). Validate on real lake / forward-paper Real-vs-BS."}
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1, ensure_ascii=False)

    print("WROTE", outdir)
    for per in ("full", "train", "oos"):
        m = combos[f"bs|{per}"]["metrics"]
        print(f"  bs|{per:5s} net={m['net_pct']:7.1f}% Sh={m['sharpe']:5.2f} DD={m['maxdd']:6.1f}% "
              f"win={m['win_rate']:4.1f}% PF={m['profit_factor']:.2f} tr={m['trades']}")
    mcf = combos["bs|full"]["mc"]["sharpe_dist"]
    print(f"  sig p={SIG['p_value']}  MC orig={mcf['original']:.2f} med={mcf['median']:.2f} "
          f"worst5={mcf['worst5']:.2f}")
    return meta


if __name__ == "__main__":
    build()
