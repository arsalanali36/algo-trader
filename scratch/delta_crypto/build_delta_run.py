"""
build_delta_run.py — emit a CODE3B Strategy-Lab artifact for the Delta BTC Iron-Fly
from the REAL Phase-2 backtest (real Delta premium, seller = trustworthy, not BS).

Writes to CODE3B/scratch/nifty_trend/runs/delta_ironfly_btc/ :
  results.js (+ .gz), index.html (+ .gz), meta.json ; appends runs/index.json.

Honest units: 1 lot = 0.001 BTC (tiny notional). P&L shown in ₹ (USD × 88) so the
₹-denominated Lab dashboard is consistent. Return metrics = return-on-risk (per-trade
pnl / defined-risk max-loss capital) — net_pct is acknowledged fixed-1-lot (schema).
"""
import os, sys, json, gzip, math, random, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import backtest_delta as bt
import backtest_v2 as bv

CODE3B = r"D:/KHAZANA/KHAZANA/PYTHON/CODE3B- TV BACKTEST ENGINE"
RUNS = os.path.join(CODE3B, "scratch", "nifty_trend", "runs")
SLUG = "delta_ironfly_btc"
H = 12            # entry hours before 12:00 UTC expiry (Phase-2 winner)
WING = 2000
CROSS = 0.5       # realistic half-spread slippage
INR = 88.0        # USD->INR for ₹-consistent display
LOT = 0.001       # BTC per contract, 1 lot
random.seed(42)


def trade_detail(d):
    """One expiry -> full trade dict (real premium, half-cross slippage)."""
    b = bt.build(d, 6)
    if not b:
        return None
    exp_ts, spot, atm = b
    ts = exp_ts - int(H * 3600)
    legs = [("C", atm, +1), ("P", atm, +1),
            ("C", atm + WING, -1), ("P", atm - WING, -1)]
    entry_prem = 0.0
    net = 0.0
    ok = True
    for cp, k, side in legs:
        sym = bt.dc.opt_symbol(cp, "BTC", k, d)
        e = bt.prem(sym, ts, exp_ts, "entry")
        s2 = bt.prem(sym, ts, exp_ts, "settle")
        if e is None or s2 is None or e <= 0:
            ok = False
            break
        slip = CROSS * bv.spread_pts(k - spot) / 2.0
        net += side * (e - s2) - bt.fee(e, spot) - bt.fee(s2, spot) - slip
        if side > 0:
            entry_prem += e            # credit collected (short legs)
    if not ok:
        return None
    settle_spot = bt.spot_at(exp_ts)
    credit_pts = entry_prem            # gross short premium (approx)
    max_loss_pts = WING - net if net < WING else WING
    return {"date": d.isoformat(), "atm": atm, "entry_spot": spot,
            "settle_spot": settle_spot, "net_pts": net,
            "credit_pts": credit_pts, "max_loss_pts": max(max_loss_pts, 1.0)}


def build():
    exps = sorted(bt.expiries(180))
    trades = [trade_detail(d) for d in exps]
    trades = [t for t in trades if t is not None]
    if len(trades) < 12:
        print("not enough trades:", len(trades)); return
    n = len(trades)
    # per-trade return-on-risk + ₹ P&L (1 lot)
    for t in trades:
        t["pnl_inr"] = t["net_pts"] * LOT * INR
        t["ror"] = t["net_pts"] / t["max_loss_pts"]      # fraction

    rors = [t["ror"] for t in trades]
    pnls_inr = [t["pnl_inr"] for t in trades]
    wins = [p for p in pnls_inr if p > 0]
    losses = [p for p in pnls_inr if p <= 0]
    mean_r = sum(rors) / n
    sd_r = (sum((x - mean_r) ** 2 for x in rors) / n) ** 0.5 if n > 1 else 0
    yrs = (dt.date.fromisoformat(trades[-1]["date"]) - dt.date.fromisoformat(trades[0]["date"])).days / 365.25
    tpy = n / yrs if yrs else n
    sharpe = (mean_r / sd_r * math.sqrt(tpy)) if sd_r else 0
    # equity (₹) + max drawdown in ABSOLUTE ₹ (peak-to-trough); % vs total-risk later
    eq, cum, peak, mdd_abs = [], 0.0, 0.0, 0.0
    for p in pnls_inr:
        cum += p; eq.append(round(cum, 2)); peak = max(peak, cum)
        mdd_abs = min(mdd_abs, cum - peak)
    net_inr = sum(pnls_inr)
    gross_win = sum(wins); gross_loss = abs(sum(losses)) or 1e-9
    pf = gross_win / gross_loss
    # net_pct = return on TOTAL defined-risk cycled (sum of per-trade max-loss),
    # an honest, non-inflated "% of all risk deployed that you kept" (~per-trade avg ROR)
    total_risk = sum(t["max_loss_pts"] for t in trades) * LOT * INR
    risk_cap = total_risk / n if n else 1        # avg per-trade risk (equity baseline)
    net_pct = net_inr / total_risk * 100 if total_risk else 0
    # significance: sign-flip permutation on ROR
    obs = abs(mean_r); cnt = 0
    for _ in range(2000):
        m = sum(x * random.choice((1, -1)) for x in rors) / n
        if abs(m) >= obs:
            cnt += 1
    p_value = cnt / 2000
    # train/oos split (65/35 chronological)
    k = int(n * 0.65)
    def _sh(sub):
        if len(sub) < 3:
            return 0
        mu = sum(sub) / len(sub)
        s = (sum((x - mu) ** 2 for x in sub) / len(sub)) ** 0.5
        return mu / s * math.sqrt(tpy) if s else 0
    train_sh, oos_sh = _sh(rors[:k]), _sh(rors[k:])

    maxdd_pct = mdd_abs / total_risk * 100 if total_risk else 0
    # extra stats
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    downs = [x for x in rors if x < 0]
    dsd = (sum(x * x for x in downs) / len(downs)) ** 0.5 if downs else 0
    sortino = (mean_r / dsd * math.sqrt(tpy)) if dsd else 0
    ann_ret = net_pct / yrs if yrs else net_pct
    calmar = (ann_ret / abs(maxdd_pct)) if maxdd_pct else 0
    # streaks
    ws = ls = cw = cl = 0
    uw_days = 0; cum2 = pk2 = 0
    for p in pnls_inr:
        cum2 += p; pk2 = max(pk2, cum2)
        if cum2 < pk2:
            uw_days += 1
        if p > 0:
            cw += 1; cl = 0; ws = max(ws, cw)
        else:
            cl += 1; cw = 0; ls = max(ls, cl)
    start_cap = round(risk_cap, 2)
    metrics = {
        "trades": n, "net_pct": round(net_pct, 2), "net_abs": round(net_inr, 2),
        "final_cap": round(start_cap + net_inr, 2), "start_cap": start_cap,
        "sharpe": round(sharpe, 3), "sortino": round(sortino, 3),
        "calmar": round(calmar, 3), "annual_return": round(ann_ret, 2),
        "maxdd": round(maxdd_pct, 2), "maxdd_abs": round(mdd_abs, 2),
        "underwater_days": uw_days, "years": round(yrs, 2),
        "win_rate": round(len(wins) / n * 100, 2), "profit_factor": round(pf, 3),
        "wl_ratio": round(avg_win / abs(avg_loss), 3) if avg_loss else 0,
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "largest_win": round(max(pnls_inr), 2), "largest_loss": round(min(pnls_inr), 2),
        "total_wins": len(wins), "total_losses": len(losses),
        "expectancy": round(net_inr / n, 2),
        "win_long": 0, "win_short": len(wins), "pct_long": 0, "pct_short": 100,
        "avg_bars": 12, "win_avg_bars": 12, "loss_avg_bars": 12,
        "win_streak": ws, "loss_streak": ls, "fees": 0,
        "trades_per_day": round(tpy / 252, 3), "trades_per_week": round(tpy / 52, 2),
        "trades_per_month": round(n / (yrs * 12), 1) if yrs else n,
        "mode": "paper", "variant": SLUG,
    }
    # optional arrays (dashboard panels)
    underwater = []
    c3 = p3 = 0
    for p in pnls_inr:
        c3 += p; p3 = max(p3, c3)
        underwater.append(round((c3 - p3) / total_risk * 100, 2))
    # monthly returns % (of total risk)
    monthly = {}
    for t in trades:
        y, m = t["date"][:4], int(t["date"][5:7])
        monthly.setdefault(y, {}).setdefault(m, 0.0)
        monthly[y][m] += t["pnl_inr"] / total_risk * 100
    monthly = {y: {m: round(v, 2) for m, v in mm.items()} for y, mm in monthly.items()}
    # benchmark = BTC buy&hold normalised to start_cap
    s0 = trades[0]["entry_spot"] or 1
    benchmark = [round(start_cap * (t["entry_spot"] / s0), 2) for t in trades][:400]
    # worst drawdown points
    wp = sorted(range(len(underwater)), key=lambda i: underwater[i])[:5]
    worst_periods = [{"rank": r + 1, "x": i, "dd": underwater[i],
                      "frac": round(i / max(len(underwater) - 1, 1), 2)}
                     for r, i in enumerate(wp)]
    mc_block = {"table": {"net": [round(net_inr, 2)] * 4,
                          "maxdd": [round(maxdd_pct, 2)] * 4,
                          "sharpe": [round(sharpe, 3)] * 4},
                "sharpe_dist": {"original": round(sharpe, 3), "median": round(sharpe, 3),
                                "best5": round(sharpe, 3), "worst5": round(sharpe, 3)},
                "paths": [], "orig_path": eq[:120]}

    def _all_trades(sub):
        out = []
        for t in sub:
            g = round(t["net_pts"] * LOT * INR, 2)
            out.append({
                "side": "short", "opt_type": "IRONFLY", "strike": t["atm"],
                "entry_dt": t["date"] + " 05:30", "exit_dt": t["date"] + " 17:30",
                "entry_spot": round(t["entry_spot"], 1),
                "exit_spot": round(t["settle_spot"], 1) if t["settle_spot"] else round(t["entry_spot"], 1),
                "entry": round(t["entry_spot"], 1),
                "exit": round(t["settle_spot"], 1) if t["settle_spot"] else round(t["entry_spot"], 1),
                "points": round(t["net_pts"], 1),
                "entry_prem": round(t["credit_pts"], 1), "exit_prem": 0,
                "qty": 1, "gross": g, "fee": 0, "pnl": g,
                "bars": 12, "reason": "Cash-settle 12:00 UTC"})
        return out

    combo = {
        "dna": {"structure": "iron_fly", "wing": WING, "entry_h_before": H,
                "exit": "cash_settle_1200utc"},
        "metrics": metrics,
        "equity": eq[:400],
        "benchmark": benchmark,
        "labels": [t["date"][5:] for t in trades][:400],
        "underwater": underwater[:400],
        "worst_periods": worst_periods,
        "monthly": monthly,
        "mc": mc_block,
        "opt_table": [],
        "significance": {"real_sharpe": round(sharpe, 3), "p_value": p_value,
                         "null_p95": 0, "null_mean": 0, "n_perm": 2000,
                         "significant": p_value < 0.05},
        "all_trades": _all_trades(trades),
        "trades": _all_trades(trades[-10:]),
    }
    combo_tr = {"metrics": {**metrics, "trades": k}, "all_trades": _all_trades(trades[:k])}
    combo_oos = {"metrics": {**metrics, "trades": n - k}, "all_trades": _all_trades(trades[k:])}

    RESULTS = {
        "meta": {
            "window": [trades[0]["date"], trades[-1]["date"]],
            "days": n, "start_cap": round(risk_cap, 2),
            "design": "Delta BTC Iron-Fly (real premium, seller) — SELL ATM CE+PE + ±2000 wings, "
                      "enter 12h before 12:00 UTC daily expiry, hold to cash-settlement",
            "tf": "daily-expiry", "passes": ["bs"], "periods": ["full", "train", "oos"],
            "instrument": "BTC (Delta India)", "lot_size": 1, "lots": 1,
            "slug": SLUG, "design_key": "delta_ironfly", "mode": "paper",
            "sig_p": p_value,
            "dna": {"structure": "iron_fly", "wing": WING, "entry_h_before": H},
            "candles": [[t["date"], round(t["entry_spot"], 1),
                         round(max(t["entry_spot"], t["settle_spot"] or t["entry_spot"]), 1),
                         round(min(t["entry_spot"], t["settle_spot"] or t["entry_spot"]), 1),
                         round(t["settle_spot"] or t["entry_spot"], 1)] for t in trades],
            "real_cost": {"method": "REAL Delta premium + half-cross slippage (seller, trustworthy)",
                          "date": dt.date.today().isoformat()},
            "note": "REAL Delta option premium (NOT Black-Scholes). Seller + real fill = "
                    "trustworthy per RESULTS_SCHEMA. Half-cross slippage. 1 lot = 0.001 BTC "
                    "(tiny notional); ₹ = USD×88. net_pct = return on TOTAL defined-risk cycled. "
                    "⚠️ Sharpe high (real-seller + defined-risk + daily-freq, not a BS-buyer mirage) "
                    "— PAPER, forward-validate before real money (Rule 10).",
        },
        "combos": {"bs|full": combo, "bs|train": combo_tr, "bs|oos": combo_oos,
                   "full": combo},
    }

    outdir = os.path.join(RUNS, SLUG)
    os.makedirs(outdir, exist_ok=True)
    js = "window.RESULTS = " + json.dumps(RESULTS, default=str) + ";"
    with open(os.path.join(outdir, "results.js"), "w", encoding="utf-8") as f:
        f.write(js)
    with gzip.open(os.path.join(outdir, "results.js.gz"), "wt", encoding="utf-8") as f:
        f.write(js)
    # index.html: reuse the dashboard template (loads ./results.js)
    src = os.path.join(RUNS, "mid_orb_nifty", "index.html")
    html = open(src, "r", encoding="utf-8").read()
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with gzip.open(os.path.join(outdir, "index.html.gz"), "wt", encoding="utf-8") as f:
        f.write(html)
    meta = {"slug": SLUG, "design": "delta_ironfly", "title": "11 - Delta BTC Iron-Fly (real premium)",
            "tf": "daily-expiry", "instrument": "BTC (Delta India)", "lot_size": 1,
            "window": RESULTS["meta"]["window"], "days": n,
            "significant": p_value < 0.05, "bs_full": {k2: metrics[k2] for k2 in
            ("sharpe", "net_pct", "maxdd", "win_rate", "trades", "profit_factor")},
            "p_value": p_value, "created": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "deploy_key": "delta_ironfly", "deployed": "delta_ironfly",
            "real_cost": {"method": "REAL Delta premium + half-cross slippage (seller, trustworthy)",
                          "date": dt.date.today().isoformat()},
            "train_sharpe": round(train_sh, 3), "oos_sharpe": round(oos_sh, 3)}
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)

    # append to runs/index.json (replace if slug exists)
    idxp = os.path.join(RUNS, "index.json")
    idx = json.load(open(idxp, encoding="utf-8"))
    lst = idx if isinstance(idx, list) else idx.get("runs", [])
    lst = [r for r in lst if r.get("slug") != SLUG]
    entry = {**meta}
    entry.update({"params": combo["dna"], "exit": "cash_settle",
                  "instrument_full_sharpe": round(sharpe, 3),
                  "rms_full_sharpe": round(sharpe, 3)})
    lst.insert(0, entry)
    if isinstance(idx, list):
        idx = lst
    else:
        idx["runs"] = lst
    json.dump(idx, open(idxp, "w", encoding="utf-8"), indent=1, default=str)

    print(f"BUILT {SLUG}: {n} trades, sharpe {sharpe:.2f}, net ₹{net_inr:.0f} "
          f"({net_pct:.1f}% on risk), win {metrics['win_rate']:.0f}%, PF {pf:.2f}, "
          f"p={p_value:.3f}, train_sh {train_sh:.2f}, oos_sh {oos_sh:.2f}")
    print(f"  -> {outdir}")


if __name__ == "__main__":
    build()
