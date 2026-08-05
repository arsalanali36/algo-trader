"""Backtest half of the StockMock-style (_sm) strategy pipeline.

The SAME `_sm` config that drives the live-paper firer (_ops/sm_runner +
trader_dashboard._fire_sm_strategy) is backtested here on OUR lake (2021->), then emitted as
a runs/<slug>/ Lab report (RESULTS_SCHEMA) so it shows in the Lab hub + Stats "Backtest"
toggle + registry. Rule 10: same config → backtest and live can't silently diverge.

Model (validated vs StockMock's exported per-expiry PDF, 2026-08-05): entry = the entry-
minute's OPEN (StockMock fill convention); held ATM strike (not rolling-ATM); per-leg SL fires
on the minute HIGH at entry×(1+sl%), fills at that level (square-off-one = legs independent);
0.5% slippage/leg (StockMock includes) + real date-aware Zerodha charges + date-aware lot.
Win-rate matched StockMock 56% ≈ 56.7%; trending/loss days ~1%.

Usage:  python scratch/nifty_trend/sm_backtest.py sm_nifty_expiry_v1   # reads nifty_config, emits run
"""
import os, sys, json, math, datetime as _dt

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, os.path.join(_ROOT, "_ops"), os.path.join(_ROOT, "_core"),
           os.path.join(_ROOT, "_data"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _paths  # noqa

import pandas as pd
import backtest_lab as bl          # STEP, IST, DAY, lot_for, _di_to_date, _date_to_di, _hm_to_mod, _ffill, _w
import sm_runner as smr            # is_expiry_day, parse_cfg
try:
    import charges as _ch
except Exception:
    _ch = None

SLIP = 0.005   # 0.5% per leg (StockMock includes)


def _wide(u, ot):
    """Wide offset frame (open+high+close) for held-strike tracking across the day."""
    root = bl._w._lake_root(u)
    frames = []
    for off in range(-10, 11):
        p = os.path.join(root, bl._w._ofn(ot, off))
        if os.path.exists(p):
            try:
                frames.append(pd.read_csv(p, usecols=["timestamp", "open", "high", "close", "strike", "spot"]))
            except Exception:
                pass
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True).dropna(subset=["close", "strike"])
    istp = df["timestamp"].values.astype("int64") + bl.IST
    df["di"] = istp // bl.DAY
    df["mod"] = ((istp % bl.DAY) // 60).astype("int32")
    df["strike"] = df["strike"].astype("int32")
    return df


def simulate(cfg, from_date, to_date):
    """Run the _sm strategy over [from_date, to_date] on the lake. Returns list of per-day
    dicts: {date, spot, atm, lot, net, gross, chg, legs:[{opt,side,lots,strike,entry,exit,
    pnl,gross,fee,points,reason,qty}]}."""
    u = cfg["instrument"]
    legs_cfg = cfg["legs"]
    m_e = bl._hm_to_mod(cfg["entry_hm"])
    m_x = bl._hm_to_mod(cfg["exit_hm"])
    grid = list(range(m_e, m_x + 1))
    step = bl.STEP.get(u, 50)

    opts = set(lg["opt"] for lg in legs_cfg)
    if any(lg.get("strike_mode") in ("cp_pct_sp", "sw_mult") for lg in legs_cfg):
        opts |= {"CE", "PE"}          # ATM straddle premium needs both sides
    DF = {}
    for ot in sorted(opts):
        DF[ot] = _wide(u, ot)
        if DF[ot] is None:
            return []
    # Pre-group each opt's wide frame by di ONCE → O(1) small day-subset per day (not an
    # O(all-rows) filter per strike/day; the cp_pct_sp scan made that a timeout otherwise).
    DFG = {ot: {int(di): g for di, g in df.groupby("di")} for ot, df in DF.items()}
    ref_g = DFG[legs_cfg[0]["opt"]]
    all_dates = sorted(bl._di_to_date(d) for d in ref_g.keys())
    dates = [d for d in all_dates if from_date <= d <= to_date and smr.should_fire_today(d, cfg)]

    def leg_series(day_df, strike):
        """(entry_open, close_ser, high_ser) for `strike` from an already-di-filtered day frame."""
        g = day_df[(day_df["strike"] == strike) & (day_df["mod"] >= m_e) & (day_df["mod"] <= m_x)].sort_values("mod")
        if len(g) == 0:
            return None, None, None
        entry_open = float(g.iloc[0]["open"])
        cmap, hmap = {}, {}
        for mo, cl, hi in zip(g["mod"].values.tolist(), g["close"].values.tolist(), g["high"].values.tolist()):
            cmap.setdefault(int(mo), float(cl)); hmap.setdefault(int(mo), float(hi))
        return entry_open, bl._ffill(cmap, grid), bl._ffill(hmap, grid)

    need_sp = any(lc.get("strike_mode") in ("cp_pct_sp", "sw_mult") for lc in legs_cfg)

    def pick_by_prem(day_df, atm, target, otm_above):
        """Strike whose entry-OPEN premium is closest to `target`, on the OTM side
        (CE: strike>=ATM ; PE: strike<=ATM). One pass over the day frame's entry-minute rows."""
        e0 = day_df[(day_df["mod"] >= m_e) & (day_df["mod"] < m_e + 3)].sort_values("mod")
        prem = {}
        for s, o in zip(e0["strike"].values.tolist(), e0["open"].values.tolist()):
            prem.setdefault(int(s), float(o))
        cand = [s for s in prem if (s >= atm if otm_above else s <= atm)]
        if not cand:
            return None
        best = min(cand, key=lambda s: abs(prem[s] - target))
        e, ser, ser_hi = leg_series(day_df, best)
        return (best, e, ser, ser_hi) if e is not None else None

    out = []
    for date in dates:
        di = bl._date_to_di(date)
        day = {ot: DFG[ot].get(di) for ot in DFG}
        rd = day[legs_cfg[0]["opt"]]
        if rd is None:
            continue
        g = rd[rd["mod"] >= m_e].sort_values("mod")
        if len(g) == 0:
            continue
        spot = float(g.iloc[0]["spot"])
        atm = round(spot / step) * step
        lot = bl.lot_for(u, date)
        # ATM straddle premium (for cp_pct_sp legs) = ATM CE + ATM PE entry-OPEN
        straddle = None
        if need_sp:
            if day.get("CE") is None or day.get("PE") is None:
                continue
            ce_atm = leg_series(day["CE"], atm); pe_atm = leg_series(day["PE"], atm)
            if ce_atm[0] is None or pe_atm[0] is None:
                continue
            straddle = ce_atm[0] + pe_atm[0]
        legs_out = []
        ok = True
        for lc in legs_cfg:
            if lc.get("strike_mode") in ("cp_pct_sp", "cp_rs"):
                target = (lc["sp_pct"] / 100.0 * straddle) if lc["strike_mode"] == "cp_pct_sp" else lc["cp_rs"]
                pick = pick_by_prem(day[lc["opt"]], atm, target, otm_above=(lc["opt"] == "CE"))
                if not pick:
                    ok = False; break
                strike, entry, ser, ser_hi = pick
            else:
                if lc.get("strike_mode") == "atm_pct":
                    strike = int(round(spot * (1 + lc["atm_pct"] / 100.0) / step) * step)
                elif lc.get("strike_mode") == "sw_mult":
                    strike = int(round((atm + lc["sw_mult"] * straddle) / step) * step)
                else:
                    strike = int(atm + lc["off"] * step)
                entry, ser, ser_hi = leg_series(day[lc["opt"]], strike)
            if entry is None or not ser:
                ok = False; break
            qty = lot * lc["lots"]
            sgn = 1.0 if lc["side"] == "SELL" else -1.0
            exitp, reason = None, "EOD"
            if lc.get("sl_pct"):
                sl_lvl = entry * (1 + lc["sl_pct"] / 100.0) if lc["side"] == "SELL" else entry * (1 - lc["sl_pct"] / 100.0)
                for hi in ser_hi:                       # SL triggers on the minute HIGH
                    if hi is None:
                        continue
                    if (lc["side"] == "SELL" and hi >= sl_lvl):
                        exitp, reason = sl_lvl, "SL"; break
            if exitp is None:
                exitp = next((v for v in reversed(ser) if v is not None), entry)   # exit-time close
            e_eff = entry * (1 - SLIP) if lc["side"] == "SELL" else entry * (1 + SLIP)
            x_eff = exitp * (1 + SLIP) if lc["side"] == "SELL" else exitp * (1 - SLIP)
            gross = sgn * (e_eff - x_eff) * qty
            fee = 0.0
            if _ch:
                try:
                    fee = _ch.option_charges(entry, exitp, qty, entry_side=lc["side"],
                                             when=_dt.datetime.strptime(date, "%Y-%m-%d"))
                except Exception:
                    fee = 0.0
            legs_out.append({
                "opt": lc["opt"], "side": lc["side"], "lots": lc["lots"], "strike": strike,
                "entry": round(entry, 2), "exit": round(exitp, 2), "qty": qty,
                "gross": round(gross), "fee": round(fee), "pnl": round(gross - fee),
                "points": round(sgn * (entry - exitp), 2), "reason": reason})
        if not ok or not legs_out:
            continue
        gross = sum(l["gross"] for l in legs_out); chg = sum(l["fee"] for l in legs_out)
        out.append({"date": date, "spot": round(spot), "atm": atm, "lot": lot,
                    "gross": round(gross), "chg": round(chg), "net": round(gross - chg),
                    "legs": legs_out})
    return out


# ------------------------------------------------------------------- metrics + emit
def _metrics(days, start_cap):
    """Day-level (per straddle) metrics on the per-day net series."""
    nets = [d["net"] for d in days]
    n = len(nets)
    if n == 0:
        return {}
    net = sum(nets)
    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x < 0]
    mean = net / n
    var = sum((x - mean) ** 2 for x in nets) / n if n else 0
    sd = math.sqrt(var)
    yrs = max(1e-9, (_dt.date.fromisoformat(days[-1]["date"]) - _dt.date.fromisoformat(days[0]["date"])).days / 365.25)
    tpy = n / yrs
    sharpe = (mean / sd * math.sqrt(tpy)) if sd else 0.0
    # cumulative equity + maxdd
    eq = 0.0; peak = 0.0; mdd = 0.0
    for x in nets:
        eq += x; peak = max(peak, eq); mdd = max(mdd, peak - eq)
    pf = (sum(wins) / abs(sum(losses))) if losses else (float("inf") if wins else 0.0)
    return {
        "trades": n, "net_abs": round(net), "net_pct": round(net / start_cap * 100, 2),
        "final_cap": round(start_cap + net), "start_cap": start_cap,
        "sharpe": round(sharpe, 3), "maxdd": round(-mdd / start_cap * 100, 3), "maxdd_abs": round(-mdd),
        "annual_return": round(net / yrs / start_cap * 100, 2),
        "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(pf, 3) if math.isfinite(pf) else None,
        "expectancy": round(net / n), "avg_win": round(sum(wins) / len(wins)) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses)) if losses else 0,
        "largest_win": max(nets), "largest_loss": min(nets), "years": round(yrs, 2),
    }


def _all_trades(days):
    """Per-leg rows in RESULTS_SCHEMA all_trades[] shape (calendar buckets by entry_date)."""
    rows = []
    for d in days:
        for l in d["legs"]:
            rows.append({
                "side": "short" if l["side"] == "SELL" else "long",
                "opt_type": l["opt"], "strike": l["strike"],
                "entry_dt": "%s %s" % (d["date"], _entry_hm), "exit_dt": "%s %s" % (d["date"], _exit_hm),
                "entry_prem": l["entry"], "exit_prem": l["exit"],
                "entry_spot": d["spot"], "exit_spot": d["spot"],
                "points": l["points"], "gross": l["gross"], "fee": l["fee"],
                "net": l["pnl"], "pnl": l["pnl"], "qty": l["qty"], "bars": None,
                "reason": l["reason"], "ym": d["date"][:7]})
    return rows


_entry_hm = "09:22"; _exit_hm = "15:15"


def _render_report_html(title, cfg, days, m):
    """Self-contained rich report: KPI strip + monthly breakup grid + equity SVG + trade log."""
    def inr(v):
        v = round(v or 0); return ("-" if v < 0 else "") + "₹" + format(abs(v), ",")
    MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    grid = {}
    for d in days:
        y = int(d["date"][:4]); mo = int(d["date"][5:7])
        grid.setdefault(y, [None] * 12)
        grid[y][mo - 1] = (grid[y][mo - 1] or 0) + d["net"]
    gtot = [0.0] * 12
    rows = ""
    for y in sorted(grid):
        tds = ""
        for i, v in enumerate(grid[y]):
            if v is None:
                tds += '<td class=z></td>'
            else:
                gtot[i] += v
                tds += '<td class="%s">%s</td>' % ("pos" if v >= 0 else "neg", format(round(v), ","))
        yt = sum(v for v in grid[y] if v is not None)
        rows += '<tr><td class=yr>%d</td>%s<td class="tot %s">%s</td></tr>' % (y, tds, "pos" if yt >= 0 else "neg", format(round(yt), ","))
    net = m.get("net_abs", 0)
    rows += '<tr class=grand><td>Total</td>%s<td class="tot %s">%s</td></tr>' % (
        "".join('<td class="%s">%s</td>' % ("pos" if v >= 0 else "neg", format(round(v), ",")) for v in gtot),
        "pos" if net >= 0 else "neg", format(round(net), ","))
    # equity SVG
    eq = []; c = 0.0
    for d in days:
        c += d["net"]; eq.append(c)
    W, H, pad = 900, 220, 4
    if eq:
        mx, mn = max(eq + [0]), min(eq + [0]); sp = (mx - mn) or 1
        pts = " ".join("%.1f,%.1f" % (pad + i / max(1, len(eq) - 1) * (W - 2 * pad), H - 10 - (v - mn) / sp * (H - 20)) for i, v in enumerate(eq))
        zeroY = H - 10 - (0 - mn) / sp * (H - 20)
        svg = ('<svg viewBox="0 0 %d %d" style="width:100%%;height:auto">'
               '<line x1="0" y1="%.1f" x2="%d" y2="%.1f" stroke="#30363d" stroke-dasharray="3 3"/>'
               '<polyline points="%s" fill="none" stroke="#3fb950" stroke-width="1.6"/></svg>' % (W, H, zeroY, W, zeroY, pts))
    else:
        svg = ""
    K = lambda k, v, c="": '<div class="kpi %s"><div class=k>%s</div><div class=v>%s</div></div>' % (c, k, v)
    kpis = "".join([
        K("Net Profit", inr(net), "g" if net >= 0 else "r"),
        K("Expiries", m.get("trades", len(days))),
        K("Win %", "%s%%" % m.get("win_rate"), "g"),
        K("Sharpe", m.get("sharpe")),
        K("Max DD", inr(-abs(m.get("maxdd_abs", 0))), "r"),  # display label only — this is a pure premium-P&L backtest, no risk simulation
        K("Profit Factor", m.get("profit_factor")),
        K("Avg / expiry", inr(m.get("expectancy", 0))),
        K("Avg Win", inr(m.get("avg_win", 0)), "g"),
        K("Avg Loss", inr(m.get("avg_loss", 0)), "r"),
        K("Return / MDD", ("%.2f" % (net / abs(m.get("maxdd_abs", 1)))) if m.get("maxdd_abs") else "—"),
    ])
    log = ""
    for d in reversed(days):
        lg = " · ".join("%s%d %s→%s%s" % (l["opt"], l["lots"], l["entry"], l["exit"], " ⛔" if l["reason"] == "SL" else "") for l in d["legs"])
        log += '<tr><td>%s</td><td>%d</td><td class="%s">%s</td><td class=dim>%s</td></tr>' % (
            d["date"], d["atm"], "pos" if d["net"] >= 0 else "neg", inr(d["net"]), lg)
    return """<!doctype html><meta charset=utf-8><title>{T}</title>
<style>body{{background:#0d1117;color:#e6edf3;font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:0;padding:20px;font-size:13px}}
h1{{font-size:19px;margin:0 0 3px}}.sub{{color:#8b949e;font-size:12px;margin-bottom:16px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px;margin-bottom:16px}}
.h{{font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#8b949e;margin-bottom:10px}}
.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:9px}}
.kpi{{background:#1c2128;border:1px solid #30363d;border-radius:9px;padding:9px 11px}}
.kpi.g{{border-top:2px solid #3fb950}}.kpi.r{{border-top:2px solid #f85149}}
.kpi .k{{font-size:9px;text-transform:uppercase;color:#8b949e}}.kpi .v{{font-size:17px;font-weight:800;margin-top:2px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}th,td{{padding:6px 8px;text-align:center;border:1px solid #21262d}}
th{{background:#1c2128;color:#8b949e;font-size:10px;text-transform:uppercase}}
td.yr,td.dim{{color:#8b949e}}td.dim{{text-align:left;font-size:10px}}.pos{{color:#3fb950}}.neg{{color:#f85149}}.tot{{font-weight:800}}.z{{background:#0d1117}}
tr.grand td{{font-weight:800;background:#1c2128}}.log{{max-height:420px;overflow:auto}}
@media(max-width:800px){{.kpis{{grid-template-columns:repeat(2,1fr)}}}}</style>
<h1>{T}</h1><div class=sub>{DESC} · <b>hamare data {W0} → {W1}</b> · {N} expiries · StockMock-parity (open-entry · held-strike · high-SL · 0.5% slip · Zerodha charges)</div>
<div class=card><div class=h>Result (our data)</div><div class=kpis>{KPIS}</div></div>
<div class=card><div class=h>Equity curve (cumulative net ₹)</div>{SVG}</div>
<div class=card><div class=h>Monthly breakup (₹)</div><table><tr><th>Year</th>{MHEAD}<th>Total</th></tr>{ROWS}</table></div>
<div class=card><div class=h>Per-expiry log ({N} days, latest first)</div><div class=log><table><tr><th>Date</th><th>ATM</th><th>Net</th><th>Legs (entry→exit, ⛔=SL)</th></tr>{LOG}</table></div></div>
<div class=sub style="margin-top:8px">Full day-wise calendar + filters: Stats tab → 🧪 Backtest toggle → is run ko chuno.</div>
""".format(T=title, DESC=smr.describe(cfg), W0=days[0]["date"] if days else "", W1=days[-1]["date"] if days else "",
           N=len(days), KPIS=kpis, SVG=svg, MHEAD="".join("<th>%s</th>" % x for x in MON), ROWS=rows, LOG=log)


def emit_run(slug, title, cfg, days, start_cap=331000):
    """Write runs/<slug>/{results.js,meta.json,index.html} + append runs/index.json."""
    global _entry_hm, _exit_hm
    _entry_hm, _exit_hm = cfg["entry_hm"], cfg["exit_hm"]
    runs = os.path.join(_HERE, "runs")
    d = os.path.join(runs, slug); os.makedirs(d, exist_ok=True)
    # train/oos split by date (65/35)
    cut = int(len(days) * 0.65)
    splits = {"full": days, "train": days[:cut], "oos": days[cut:]}
    lot = days[-1]["lot"] if days else 0
    meta = {"window": [days[0]["date"], days[-1]["date"]] if days else [], "days": len(days),
            "start_cap": start_cap, "design": title, "tf": "1m", "instrument": cfg["instrument"] + " options",
            "lot_size": lot, "lots": 1, "intraday": True, "periods": ["full", "train", "oos"],
            "sm_config": cfg}
    combos = {}
    for period, dd in splits.items():
        if not dd:
            continue
        m = _metrics(dd, start_cap)
        at = _all_trades(dd)
        # cumulative equity for the chart
        eq = []; c = 0.0
        for x in dd:
            c += x["net"]; eq.append(round(c))
        combos["bs|%s" % period] = {"metrics": m, "all_trades": at, "trades": at,
                                    "equity": eq, "labels": [x["date"] for x in dd]}
    results = {"meta": meta, "combos": combos}
    with open(os.path.join(d, "results.js"), "w", encoding="utf-8") as f:
        f.write("window.RESULTS = " + json.dumps(results, ensure_ascii=False) + ";")
    mfull = combos.get("bs|full", {}).get("metrics", {})
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    # index.html = a self-contained rich report (KPIs + monthly breakup + equity curve +
    # trade log). NOT the run_hunt dashboard_intraday template — that needs a richer results.js
    # (meta.passes/dna/benchmark/…) and renders empty on our minimal schema. The full day-wise
    # calendar lives in the Stats "Backtest" toggle (backtest_calendar reads the same combos).
    with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
        f.write(_render_report_html(title, cfg, days, combos.get("bs|full", {}).get("metrics", {})))
    # append/update runs/index.json
    idx_path = os.path.join(runs, "index.json")
    try:
        idx = json.load(open(idx_path, encoding="utf-8"))
    except Exception:
        idx = []
    idx = [e for e in idx if e.get("slug") != slug]
    bs = combos.get("bs|full", {}).get("metrics", {})
    idx.append({"slug": slug, "design": "sm_expiry", "title": title, "tf": "1m",
                "params": {"legs": cfg["legs"], "entry": cfg["entry_hm"], "exit": cfg["exit_hm"],
                           "day_filter": cfg["day_filter"]},
                "exit": "per-leg SL%% + %s EOD" % cfg["exit_hm"], "instrument": cfg["instrument"] + " options",
                "lot_size": lot, "window": meta["window"], "days": len(days),
                "significant": False,
                "bs_full": {"sharpe": bs.get("sharpe"), "net_pct": bs.get("net_pct"),
                            "maxdd": bs.get("maxdd"), "win_rate": bs.get("win_rate"),
                            "trades": bs.get("trades"), "profit_factor": bs.get("profit_factor")},
                "p_value": None, "deploy_key": cfg["id"], "deployed": cfg["id"],
                "real_cost": {"method": "REAL OptChainLake_1m premium (held-strike, open-entry, high-SL) + 0.5% slip + Zerodha charges",
                              "note": "StockMock-parity — validated vs StockMock PDF (win 56%, loss-days ~1%)"}})
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    return results, mfull


if __name__ == "__main__":
    sid = sys.argv[1] if len(sys.argv) > 1 else "sm_nifty_expiry_v1"
    ncfg = json.load(open(os.path.join(_ROOT, "nifty_config.json"), encoding="utf-8"))
    cfg = smr.parse_cfg(sid, ncfg.get(sid, {}))
    if not cfg or not cfg["valid"]:
        print("no valid _sm config for", sid); sys.exit(1)
    reg = json.load(open(os.path.join(_ROOT, "strategy_registry.json"), encoding="utf-8"))
    title = next(("%s - %s" % (k, v.get("name")) for k, v in reg["strategies"].items()
                  if v.get("config_key") == sid), sid)
    print("simulating", title, "…")
    days = simulate(cfg, "2021-01-01", "2026-12-31")
    if not days:
        print("no trades"); sys.exit(1)
    results, m = emit_run(sid, title, cfg, days)
    print("emitted runs/%s/ — %d expiries · net ₹%s · Sharpe %s · win %s%% · maxDD ₹%s"
          % (sid, len(days), format(m["net_abs"], ","), m["sharpe"], m["win_rate"], format(-m["maxdd_abs"], ",")))
