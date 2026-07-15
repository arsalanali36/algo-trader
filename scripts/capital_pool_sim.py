#!/usr/bin/env python3
"""Shared-capital-pool backtest — 2×2 matrix (naked/hedged × ATR-SL/ride-to-EOD)
under a REALISTIC shared ₹ pool, with the aggressive-trail params OPTIMISED
under the pool constraint. 3-pass (Instrument → +RMS/pool → +Black-Scholes charges).

WHY: path_aware_sl_sim --hold-eod --ride said ride-to-EOD ≈ 9× the ATR SL, but it
assumed INFINITE capital and a quick in-sample trail pick. Reality: many strategies
share ONE pool; a naked NIFTY short locks ~₹1.6L/lot so ₹11L ≈ 6 concurrent — ride-to
-EOD holds margin all day → later signals get CAPITAL_BLOCKED. This models that
properly, tests hedging (cuts margin → more fit), and optimises the trail UNDER the pool.

Rule 6B reuse:
  • per-trade premium bars + windowing  → path_aware_sl_sim.load_bars/_window
  • aggressive trail curve               → risk_gate.target_sl_level (the LIVE engine)
  • real per-position margin             → scripts/capital_margin_table.py (real Kite
                                           order_margins / basket_order_margins), Rule 6B
                                           calls risk_gate.broker_real_margin under the hood
  • real Zerodha charges + DOM slippage  → bs_option.calc_charges / slip_cost_leg

Faithfulness caveats (surfaced, never hidden):
  • Historical option contracts are EXPIRED → the live margin API can't price them, so
    per-position margin = a current-equivalent real Kite number per underlying (SPAN barely
    depends on the exact strike). Un-probed underlyings use a SPAN% calibrated from the real
    stock probes. See capital_sim_margins.json.
  • Hedge legs were never traded → no cached bars. Hedge P&L is modelled: premium drag
    (HEDGE_DECAY of the real config-resolved hedge premium) + a defined-risk tail cap
    (strike_width×qty). Sensitivity on HEDGE_DECAY reported.
  • Pool constraint is PER-DAY (every strategy here is intraday, flat by 15:15).

Run ON THE VPS (needs trades.db + trade_ohlc + capital_sim_margins.json):
    venv/bin/python scripts/capital_pool_sim.py [--from 2026-06-22] [--to 2026-07-15]
                                                [--full] [--optimize] [--json out.json]
"""
import sys, os, json, argparse, math, statistics
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)               # path_aware_sl_sim lives in scripts/
import _paths  # noqa
import order_store, risk_gate
import path_aware_sl_sim as pas        # reuse load_bars/_window/_mtm/replay_* (Rule 6B)
try:
    import dhan_master
except Exception:
    dhan_master = None

MARGIN_TABLE = os.path.join(ROOT, "data", "capital_sim_margins.json")
INDEX = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX"}
EOD = "15:15"
POOLS = [500000, 800000, 1100000]      # ₹5L / ₹8L / ₹11L (user hard max 11L)
INF = 10**12                           # unconstrained reference (reproduces path_aware)

# Deployed aggressive-trail fixed params (risk_gate default_target_sl_config, VPS config)
AGG_PCT, AGG_MULT = 30, 2.0
# Hedge premium intraday decay used for the modelled hedge cost (sensitivity reported).
# Overridable via --hedge-decay for the sensitivity sweep.
HEDGE_DECAY = 0.70


# ───────────────────────── margin table ─────────────────────────
_MT = None
def margin_table():
    global _MT
    if _MT is None:
        _MT = json.load(open(MARGIN_TABLE))
    return _MT


def position_margin(underlying, strike, qty, lots, side, hedged):
    """Real ₹ margin locked by this position. SELL → per-underlying real Kite margin
    (naked or netted-basket hedged) × lots, or SPAN% fallback × strike×qty. BUY →
    premium paid (qty×premium; the caller passes strike≈entry premium for BUY)."""
    mt = margin_table()
    if side != "SELL":
        return abs(strike) * qty          # BUY leg: premium is the capital (strike carries premium here)
    rec = mt.get(underlying)
    if rec and rec.get("naked_per_lot"):
        if hedged and rec.get("hedged_per_lot"):
            return rec["hedged_per_lot"] * max(1, lots)
        if hedged:
            return rec["naked_per_lot"] * mt["_fallback"]["hedged_naked_ratio"] * max(1, lots)
        return rec["naked_per_lot"] * max(1, lots)
    # un-probed underlying → SPAN% of notional
    fb = mt["_fallback"]
    naked = fb["span_pct_naked"] * abs(strike) * qty
    return naked * (fb["hedged_naked_ratio"] if hedged else 1.0)


def hedge_econ(underlying, qty):
    """(tail_cap ₹, hedge_cost ₹) for a hedged SELL leg. tail_cap = spread max loss
    (strike_width×qty); hedge_cost = real config-resolved hedge premium × qty × decay."""
    mt = margin_table()
    rec = mt.get(underlying)
    if rec and rec.get("strike_width"):
        tail = rec["strike_width"] * qty
        cost = (rec.get("hedge_prem") or 0) * qty * HEDGE_DECAY
        return tail, cost
    # fallback: unknown width → generous tail (rarely binds intraday), ~₹2/unit premium
    return 999999 * qty, 2.0 * qty * HEDGE_DECAY


# ───────────────────── exit-time-aware replays ─────────────────────
# Mirror pas.replay_legacy / replay_aggr but ALSO return the exit bar time (needed
# for the capital-release event loop). An assertion vs pas.* guards against drift.
def replay_ride(bars, side, ep, qty, cfg, lots, fallback, ride=True):
    """Aggressive trail (risk_gate.target_sl_level from the confirmed peak). Returns
    (status, rs, exit_hhmm). ride=True → no target cap, trail is the only exit."""
    target_rs = cfg["target_per_lot"] * max(1, int(lots))
    peak = 0.0
    for (hhmm, o, h, l, c) in bars:
        if side == "SELL":
            adv = pas._mtm(side, ep, h, qty); fav = pas._mtm(side, ep, l, qty)
        else:
            adv = pas._mtm(side, ep, l, qty); fav = pas._mtm(side, ep, h, qty)
        sl_level = risk_gate.target_sl_level(peak, cfg, lots)
        if adv <= sl_level:
            return ("TRAIL_SL" if sl_level >= 0 else "SL"), round(sl_level, 2), hhmm
        if not ride and target_rs > 0 and fav >= target_rs:
            return "TARGET", target_rs, hhmm
        if fav > peak:
            peak = fav
    xt = bars[-1][0] if bars else EOD
    return "OPEN(EOD)", fallback, xt


def _assert_equiv(cov_sample):
    """Rule 6B safeguard: my replay_ride must match pas.replay_aggr to the rupee on
    the same inputs (only the exit-time is new). Abort if it drifts."""
    cfg = pas._agg_cfg(2000, 1000, 100)
    bad = 0
    for c in cov_sample:
        mine = replay_ride(c["win"], c["side"], c["ep"], c["qty"], cfg, c["lots"], c["fb"], ride=True)[1]
        theirs = pas.replay_aggr(c["win"], c["side"], c["ep"], c["qty"], cfg, c["lots"], c["fb"], ride=True)[1]
        if abs(mine - theirs) > 0.01:
            bad += 1
    if bad:
        raise SystemExit(f"REPLAY DRIFT vs pas.replay_aggr on {bad}/{len(cov_sample)} — abort")
    print(f"  ✓ replay_ride == pas.replay_aggr on {len(cov_sample)} covered (rupee-exact)", flush=True)


# ───────────────────── per-trade prep ─────────────────────
def _underlying(sym):
    root = str(sym or "").split("-")[0].upper()
    for ix in INDEX:
        if root.startswith(ix):
            return ix
    return root


def prep_trades(dfrom, dto, allow_fetch):
    """One record per completed trade with: underlying, side, lots, entry/exit time,
    ATR-rule (pnl,exit), RIDE-window bars, actual pnl, date. Bars loaded once."""
    raw = order_store.trades_for_range(dfrom, dto)["details"]
    raw = [t for t in raw if t.get("pnl") is not None]
    out = []
    n_cov = 0
    for i, t in enumerate(raw):
        side = str(t["entry"]).upper()
        ep = float(t["entry_price"]); qty = int(t["qty"]); pnl = float(t["pnl"])
        sym = t.get("sym") or ""; sec_id = t.get("sec_id")
        date = t.get("entry_date") or ""
        et = (t.get("entry_time") or "09:15")[:5]
        xt = (t.get("exit_time") or EOD)[:5]
        if t.get("exit_date") and t.get("exit_date") != date:
            xt = EOD                         # overnight → capital held to EOD (rare)
        und = _underlying(sym)
        try:
            strike = float(str(sym).split("-")[2])
        except Exception:
            strike = ep                       # fallback (BUY uses premium as 'strike')
        lot = 1
        if dhan_master and sec_id:
            try:
                ls = dhan_master.get_lot_size_by_sec_id(sec_id)
                if ls and qty:
                    lot = max(1, round(qty / float(ls)))
            except Exception:
                pass
        bars = pas.load_bars(sec_id, sym, date, allow_fetch=allow_fetch)
        win = pas._window(bars, et, EOD, hold_eod=True)     # ride needs entry..EOD
        covered = len(win) >= 1
        if covered:
            n_cov += 1
        fb = pnl
        if win:
            fb = pas._mtm(side, ep, win[-1][4], qty)         # EOD-close fallback
        out.append({
            "und": und, "side": side, "ep": ep, "qty": qty, "lots": lot,
            "strike": strike, "date": date, "et": et, "xt_atr": xt,
            "actual": pnl, "win": win, "fb": fb, "covered": covered,
            "sym": sym,
        })
        if (i + 1) % 100 == 0:
            print(f"  prep {i+1}/{len(raw)} covered={n_cov}", flush=True)
    print(f"  prepared {len(out)} trades, {n_cov} covered ({100*n_cov//max(1,len(out))}%)", flush=True)
    # ── covered-subset reconciliation (matches the path_aware_sl_sim 'known numbers') ──
    cov = [t for t in out if t["covered"]]
    act_cov = sum(t["actual"] for t in cov)
    cfg = pas._agg_cfg(2000, 1000, 100)
    ride_cov = 0.0
    for t in cov:
        st, rs, _ = replay_ride(t["win"], t["side"], t["ep"], t["qty"], cfg, t["lots"], t["fb"], ride=True)
        ride_cov += rs
    print(f"  [reconcile covered {len(cov)}] actual-exit net ₹{act_cov:,.0f} | "
          f"ride-to-EOD net ₹{ride_cov:,.0f}  (∞-capital, no charges — vs path_aware ~+7.8k / ~+64k)",
          flush=True)
    return out


# ───────────────────── per-trade P&L for a variant ─────────────────────
def trade_pnl_exit(tr, exit_rule, cfg, hedged):
    """(pnl ₹, exit_hhmm) for this trade under exit_rule ∈ {'atr','ride'} and
    naked/hedged. NO-DATA ride trades fall back to actual pnl + actual exit."""
    side, qty = tr["side"], tr["qty"]
    if exit_rule == "atr":
        pnl = tr["actual"]; xt = tr["xt_atr"]
    else:  # ride
        if tr["covered"]:
            st, rs, xt = replay_ride(tr["win"], side, tr["ep"], qty, cfg, tr["lots"], tr["fb"],
                                     ride=cfg.get("_ride", True))
            pnl = rs
        else:
            pnl = tr["actual"]; xt = tr["xt_atr"]
    if hedged and side == "SELL":
        tail, cost = hedge_econ(tr["und"], qty)
        pnl = max(pnl, -tail) - cost           # tail cap + insurance drag
    return pnl, xt


# ───────────────────── charges (BS pass) ─────────────────────
_BS = None
def _bs():
    global _BS
    if _BS is None:
        sys.path.insert(0, os.path.join(ROOT, "scratch", "nifty_trend"))
        import bs_option
        _BS = bs_option
    return _BS


def leg_charges(tr, pnl, hedged, when):
    """Real Zerodha F&O round-trip charge + DOM slippage for the executed leg(s)."""
    bs = _bs()
    side, qty, ep = tr["side"], tr["qty"], tr["ep"]
    # derive exit premium from pnl on the primary leg
    if side == "SELL":
        xp = ep - (pnl / qty if qty else 0)
        entry_side = "SELL"
    else:
        xp = ep + (pnl / qty if qty else 0)
        entry_side = "BUY"
    xp = max(0.05, xp)
    fee = bs.calc_charges(ep, xp, qty, entry_side=entry_side, when=when)
    slip = bs.slip_cost_leg(ep, xp, qty)
    if hedged and side == "SELL":
        rec = margin_table().get(tr["und"], {})
        hp = rec.get("hedge_prem") or 2.0
        fee += bs.calc_charges(hp, hp * 0.4, qty, entry_side="BUY", when=when)   # hedge leg round trip
        slip += bs.slip_cost_leg(hp, hp * 0.4, qty)
    return fee + slip


# ───────────────────── per-day event-driven pool sim ─────────────────────
def day_pool_sim(day_trades, pool, exit_rule, cfg, hedged):
    """Chronological within a day. Release capital at exits ≤ new entry time; block if
    capital_in_use + margin > pool. Returns dict with net, taken, blocked, peak_util,
    max_concurrent, hedge_cost, per-trade taken flags (for charge pass)."""
    # (entry_hhmm, margin, exit_hhmm, pnl, tr)
    items = []
    for tr in day_trades:
        pnl, xt = trade_pnl_exit(tr, exit_rule, cfg, hedged)
        marg = position_margin(tr["und"], tr["strike"], tr["qty"], tr["lots"], tr["side"], hedged)
        items.append({"et": tr["et"], "xt": xt if xt >= tr["et"] else tr["et"],
                      "marg": marg, "pnl": pnl, "tr": tr})
    items.sort(key=lambda x: (x["et"],))
    open_pos = []   # list of (exit_hhmm, margin)
    in_use = 0.0
    net = 0.0; taken = 0; blocked = 0; peak = 0.0; maxconc = 0; hedge_cost = 0.0
    taken_items = []
    for it in items:
        # release exits that have happened by this entry time
        still = []
        for (xh, m) in open_pos:
            if xh <= it["et"]:
                in_use -= m
            else:
                still.append((xh, m))
        open_pos = still
        if in_use + it["marg"] <= pool + 1e-6:
            net += it["pnl"]; taken += 1
            in_use += it["marg"]
            open_pos.append((it["xt"], it["marg"]))
            peak = max(peak, in_use); maxconc = max(maxconc, len(open_pos))
            taken_items.append(it)
            if hedged and it["tr"]["side"] == "SELL":
                _, c = hedge_econ(it["tr"]["und"], it["tr"]["qty"]); hedge_cost += c
        else:
            blocked += 1
    return {"net": net, "taken": taken, "blocked": blocked, "peak": peak,
            "maxconc": maxconc, "hedge_cost": hedge_cost, "taken_items": taken_items}


def run_variant(trades, pool, exit_rule, cfg, hedged, with_charges=True):
    """Full sim over all days. Returns per-day nets + aggregate metrics."""
    by_day = {}
    for tr in trades:
        by_day.setdefault(tr["date"], []).append(tr)
    day_nets = []; tot_taken = 0; tot_blocked = 0; peak = 0.0; maxconc = 0
    hedge_cost = 0.0; fees = 0.0
    for date in sorted(by_day):
        r = day_pool_sim(by_day[date], pool, exit_rule, cfg, hedged)
        dnet = r["net"]
        if with_charges:
            f = 0.0
            for it in r["taken_items"]:
                f += leg_charges(it["tr"], it["pnl"], hedged, when=date)
            dnet -= f; fees += f
        day_nets.append((date, dnet))
        tot_taken += r["taken"]; tot_blocked += r["blocked"]
        peak = max(peak, r["peak"]); maxconc = max(maxconc, r["maxconc"])
        hedge_cost += r["hedge_cost"]
    return {"day_nets": day_nets, "taken": tot_taken, "blocked": tot_blocked,
            "peak_util": peak, "max_concurrent": maxconc, "hedge_cost": hedge_cost,
            "fees": fees, "pool": pool}


# ───────────────────── metrics ─────────────────────
def metrics(day_nets, pool):
    nets = [d for _, d in day_nets]
    n = len(nets)
    net = sum(nets)
    if n < 2:
        return {"net": net, "sharpe": 0, "maxdd": 0, "p_value": 1.0, "days": n}
    rets = [x / pool for x in nets]                      # daily return on pool
    mu = statistics.mean(rets); sd = statistics.pstdev(rets) or 1e-9
    sharpe = (mu / sd) * math.sqrt(252)
    # equity curve + maxDD (₹)
    eq = []; run = 0.0
    for x in nets:
        run += x; eq.append(run)
    peak = -1e18; maxdd = 0.0
    for v in eq:
        peak = max(peak, v); maxdd = min(maxdd, v - peak)
    # permutation significance: is mean daily net > 0 beyond chance? sign-flip null.
    import random
    random.seed(42)
    obs = statistics.mean(nets)
    ge = 0; N = 2000
    for _ in range(N):
        s = sum(x if random.random() < 0.5 else -x for x in nets) / n
        if s >= obs:
            ge += 1
    p = ge / N
    return {"net": round(net), "sharpe": round(sharpe, 2), "maxdd": round(maxdd),
            "p_value": round(p, 3), "days": n, "significant": p < 0.05}


# ───────────────────── trail optimiser (UNDER the pool) ─────────────────────
OPT_INIT_SL = [1000, 1500, 2000, 2500]
OPT_STEP    = [100, 200]
OPT_CAP     = [("ride", None), ("cap6k", 6000)]     # target-cap: none(ride) / high-cap
OPT_CUSHION = [0, 500, 1000]                         # whipsaw guard (ride needs room)


def _cfg(init_sl, step, cap, cushion):
    tgt = cap[1] if cap[1] else 6000                 # target used only for aggressive_pct ref when riding
    c = dict(target_per_lot=tgt, initial_sl_per_lot=init_sl, favour_step=step, sl_move=step,
             aggressive_pct=AGG_PCT, aggressive_mult=AGG_MULT, min_cushion=cushion)
    c["_ride"] = (cap[0] == "ride")
    return c


def optimise_trail(trades, pool, hedged, train_dates, oos_dates):
    """Grid init_sl×step×cap×cushion. Objective = total pool-constrained net (pass-2,
    no charges — fast). Rank MIN(train,OOS) (TRAP #103). Returns (best_cfg, table)."""
    tr_train = [t for t in trades if t["date"] in train_dates]
    tr_oos   = [t for t in trades if t["date"] in oos_dates]
    rows = []
    for isl in OPT_INIT_SL:
        for step in OPT_STEP:
            for cap in OPT_CAP:
                for cush in OPT_CUSHION:
                    cfg = _cfg(isl, step, cap, cush)
                    ntr = sum(d for _, d in run_variant(tr_train, pool, "ride", cfg, hedged,
                                                        with_charges=False)["day_nets"])
                    noos = sum(d for _, d in run_variant(tr_oos, pool, "ride", cfg, hedged,
                                                         with_charges=False)["day_nets"])
                    rows.append({"p": f"SL{isl}/st{step}/{cap[0]}/cu{cush}", "cfg": cfg,
                                 "train": ntr, "oos": noos, "min": min(ntr, noos),
                                 "all": ntr + noos})
    rows.sort(key=lambda r: r["min"], reverse=True)
    return rows[0], rows


# ───────────────────── main ─────────────────────
def _inr(x):
    return f"{round(x):,}"


def main():
    ap = argparse.ArgumentParser()
    ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
    ap.add_argument("--from", dest="dfrom", default="2026-06-22")
    ap.add_argument("--to", dest="dto", default=ist.strftime("%Y-%m-%d"))
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--only", default="", help="comma underlyings filter, e.g. NIFTY or NIFTY,BANKNIFTY")
    ap.add_argument("--breakdown", action="store_true",
                    help="per-underlying net (naked ATR vs naked ride), split train/OOS — "
                         "for portfolio pruning; robust = wins in BOTH halves (overfit guard)")
    ap.add_argument("--hedge-decay", type=float, default=None,
                    help="override HEDGE_DECAY (hedge premium fraction lost intraday)")
    ap.add_argument("--json", default=os.path.join(ROOT, "data", "capital_pool_sim.json"))
    args = ap.parse_args()
    global HEDGE_DECAY
    if args.hedge_decay is not None:
        HEDGE_DECAY = args.hedge_decay

    print(f"Loading trades {args.dfrom}..{args.dto} …", flush=True)
    trades = prep_trades(args.dfrom, args.dto, allow_fetch=not args.no_fetch)
    if args.only:
        keep = set(x.strip().upper() for x in args.only.split(",") if x.strip())
        before = len(trades)
        trades = [t for t in trades if t["und"] in keep]
        cov_only = sum(1 for t in trades if t["covered"])
        print(f"  --only {sorted(keep)}: {len(trades)}/{before} trades ({cov_only} covered)", flush=True)
    dates = sorted(set(t["date"] for t in trades))
    cut = int(len(dates) * 0.65)
    train_dates = set(dates[:cut]); oos_dates = set(dates[cut:])
    print(f"Days {len(dates)}  train {len(train_dates)} (..{dates[cut-1]})  "
          f"oos {len(oos_dates)} ({dates[cut]}..)", flush=True)

    cov_sample = [{"win": t["win"], "side": t["side"], "ep": t["ep"], "qty": t["qty"],
                   "lots": t["lots"], "fb": t["fb"]} for t in trades if t["covered"]][:80]
    _assert_equiv(cov_sample)

    if args.breakdown:
        # per-underlying raw edge (∞ capital, no pool), naked ATR vs naked ride,
        # split train/OOS so lucky-in-sample winners are exposed (overfit guard).
        ride_cfg = pas._agg_cfg(1500, 200, 200); ride_cfg["min_cushion"] = 500; ride_cfg["_ride"] = True
        agg = {}
        for t in trades:
            u = t["und"]; half = "train" if t["date"] in train_dates else "oos"
            a = agg.setdefault(u, {"n": 0, "cov": 0,
                                   "atr_train": 0.0, "atr_oos": 0.0,
                                   "ride_train": 0.0, "ride_oos": 0.0})
            a["n"] += 1; a["cov"] += 1 if t["covered"] else 0
            atr, _ = trade_pnl_exit(t, "atr", None, False)
            rid, _ = trade_pnl_exit(t, "ride", ride_cfg, False)
            a[f"atr_{half}"] += atr; a[f"ride_{half}"] += rid
        rows = []
        for u, a in agg.items():
            atr_all = a["atr_train"] + a["atr_oos"]; ride_all = a["ride_train"] + a["ride_oos"]
            robust_atr = min(a["atr_train"], a["atr_oos"])
            rows.append((u, a["n"], a["cov"], a["atr_train"], a["atr_oos"], atr_all,
                         a["ride_train"], a["ride_oos"], ride_all, robust_atr))
        rows.sort(key=lambda r: r[5], reverse=True)   # by ATR all net
        print(f"\n{'='*96}\nPER-UNDERLYING raw edge (∞ capital) — NAKED. "
              f"'robust' = wins BOTH train & OOS\n{'='*96}")
        print(f"  {'under':11s} {'n':>3} {'cov':>3} | {'ATRtr':>8} {'ATRoos':>8} {'ATRall':>8} | "
              f"{'RIDEtr':>8} {'RIDEoos':>8} {'RIDEall':>8} | robust?")
        tot = [0.0]*6
        for (u, n, cov, at, ao, aa, rt, ro, ra, rob) in rows:
            flag = "✓BOTH+" if (at > 0 and ao > 0) else ("~" if aa > 0 else "✗")
            print(f"  {u:11s} {n:>3} {cov:>3} | {at:>8,.0f} {ao:>8,.0f} {aa:>8,.0f} | "
                  f"{rt:>8,.0f} {ro:>8,.0f} {ra:>8,.0f} | {flag}")
            for i, v in enumerate((at, ao, aa, rt, ro, ra)):
                tot[i] += v
        print(f"  {'TOTAL':11s} {'':>3} {'':>3} | {tot[0]:>8,.0f} {tot[1]:>8,.0f} {tot[2]:>8,.0f} | "
              f"{tot[3]:>8,.0f} {tot[4]:>8,.0f} {tot[5]:>8,.0f} |")
        # robust winners = positive in BOTH halves (ATR)
        win = [r[0] for r in rows if r[3] > 0 and r[4] > 0]
        print(f"\n  ROBUST winners (naked ATR, +ve BOTH train & OOS): {win}")
        return

    out = {"meta": {"from": args.dfrom, "to": args.dto, "days": len(dates),
                    "n_trades": len(trades), "train_days": len(train_dates),
                    "oos_days": len(oos_dates), "hedge_decay": HEDGE_DECAY,
                    "agg_pct": AGG_PCT, "agg_mult": AGG_MULT}, "cells": {}}

    # optimise the ride trail per (naked/hedged) UNDER ₹11L (the real constraint)
    opt = {}
    for hedged in (False, True):
        tag = "hedged" if hedged else "naked"
        print(f"\n── optimising ride trail ({tag}) under ₹11L …", flush=True)
        best, rows = optimise_trail(trades, 1100000, hedged, train_dates, oos_dates)
        opt[tag] = best
        print(f"  robust winner: {best['p']}  train ₹{_inr(best['train'])} / "
              f"oos ₹{_inr(best['oos'])} / all ₹{_inr(best['all'])}")
        insample = sorted(rows, key=lambda r: r["all"], reverse=True)[0]
        print(f"  in-sample best (overfit-prone): {insample['p']}  all ₹{_inr(insample['all'])} "
              f"(train ₹{_inr(insample['train'])}/oos ₹{_inr(insample['oos'])})")
        out["meta"].setdefault("opt", {})[tag] = {
            "chosen": best["p"], "train": round(best["train"]), "oos": round(best["oos"]),
            "all": round(best["all"]), "insample_best": insample["p"],
            "insample_all": round(insample["all"]),
            "top8": [{"p": r["p"], "train": round(r["train"]), "oos": round(r["oos"]),
                      "min": round(r["min"])} for r in rows[:8]]}

    # 2×2 matrix × pool sweep (+ ∞ reference), 3-pass metrics
    CELLS = [("A", "naked",  "atr",  False), ("B", "naked",  "ride", False),
             ("C", "hedged", "atr",  True),  ("D", "hedged", "ride", True)]
    for pool in POOLS + [INF]:
        pk = "INF" if pool >= INF else f"{pool//100000}L"
        print(f"\n{'='*74}\nPOOL ₹{pk}\n{'='*74}", flush=True)
        for code, ptype, rule, hedged in CELLS:
            cfg = opt[ptype]["cfg"] if rule == "ride" else pas._agg_cfg(2000, 1000, 100)
            # pass 2 (RMS/pool, with charges = pass 3 combined for the deployable net)
            res = run_variant(trades, pool, rule, cfg, hedged, with_charges=True)
            m = metrics(res["day_nets"], min(pool, 1100000))
            # pass 1 (instrument, no charges, ∞ pool) — the raw signal reference
            key = f"{code}|{pk}"
            out["cells"][key] = {"pool": pk, "type": ptype, "rule": rule,
                                 "net": m["net"], "sharpe": m["sharpe"], "maxdd": m["maxdd"],
                                 "p_value": m["p_value"], "significant": m["significant"],
                                 "taken": res["taken"], "blocked": res["blocked"],
                                 "peak_util": round(res["peak_util"]),
                                 "peak_util_pct": round(100 * res["peak_util"] / min(pool, 1100000)),
                                 "max_concurrent": res["max_concurrent"],
                                 "hedge_cost": round(res["hedge_cost"]),
                                 "fees": round(res["fees"]),
                                 "trail": opt[ptype]["p"] if rule == "ride" else "ATR(actual)"}
            c = out["cells"][key]
            print(f"  {code} {ptype:6s}+{rule:4s}: net ₹{_inr(m['net']):>10}  Sh {m['sharpe']:>5}  "
                  f"DD ₹{_inr(m['maxdd']):>9}  p={m['p_value']:<5} taken {res['taken']:>3}/"
                  f"blk {res['blocked']:>3}  peakUtil {c['peak_util_pct']}%  "
                  f"maxConc {res['max_concurrent']}  hedgeCost ₹{_inr(res['hedge_cost'])}", flush=True)

    # ── hedge-decay sensitivity at ₹11L (is the hedged verdict robust to my cost model?) ──
    print(f"\n{'='*74}\nHEDGE-DECAY SENSITIVITY (₹11L pool) — hedged cells C/D net vs decay\n{'='*74}", flush=True)
    saved = HEDGE_DECAY
    sens = {}
    for dec in (0.5, 0.7, 1.0):
        HEDGE_DECAY = dec
        cD = run_variant(trades, 1100000, "ride", opt["hedged"]["cfg"], True, with_charges=True)
        cC = run_variant(trades, 1100000, "atr", pas._agg_cfg(2000, 1000, 100), True, with_charges=True)
        mD = metrics(cD["day_nets"], 1100000); mC = metrics(cC["day_nets"], 1100000)
        sens[dec] = {"C_net": mC["net"], "D_net": mD["net"], "hedge_cost": round(cD["hedge_cost"])}
        print(f"  decay {dec}: C hedged+atr net ₹{_inr(mC['net']):>10}  |  "
              f"D hedged+ride net ₹{_inr(mD['net']):>10}  (hedge cost ₹{_inr(cD['hedge_cost'])})", flush=True)
    HEDGE_DECAY = saved
    out["hedge_decay_sensitivity"] = sens

    json.dump(out, open(args.json, "w"), indent=1, default=str)
    print(f"\nJSON → {args.json}", flush=True)


if __name__ == "__main__":
    main()
