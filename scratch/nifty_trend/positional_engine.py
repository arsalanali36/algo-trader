"""Positional (multi-day) backtest for the Chain-Zone signal — NIFTY, 1x, long+short.

SAME entry signal as the intraday chain_zone (ie.design_signals 'chain_zone'), but
POSITIONAL rules instead of the house intraday ones:
  • NO 15:15 force-exit, NO max-2/day — a position is HELD across days/overnight.
  • Exit styles: 'atr_rr' (stop + RR target), 'trail' (ATR trailing), 'stop_only'
    (stop, else ride to max_hold), 'flip' (exit on the opposite chain-zone signal).
  • max_hold_days caps the hold; overnight GAPS are filled realistically (a gap
    through the stop fills at the next bar's OPEN, worse than the stop — honest).
  • 1x / no leverage (notional ≤ capital), risk 1.5%/trade.

Produces the SAME res dict shape as intraday_engine.backtest() so engine.metrics /
montecarlo / run_hunt._combo_from_res all work unchanged. Options pass uses
bs_option.reprice_positional (MONTHLY ATM — multi-day holds bleed weekly theta).
"""
import numpy as np
import pandas as pd
import engine
import intraday_engine as ie

RISK_PCT = 0.015
START_CAP = 1_000_000.0
LEV_CAP = 1.0

EXIT_STYLES = ["atr_rr", "trail", "stop_only", "flip"]


def backtest_positional(d, name, params=None, exit_style="atr_rr", rms_caps=None):
    p = dict(ie.DEFAULTS.get(name, {})); p.update(params or {})
    long_e, short_e = ie.design_signals(d, name, p)
    a = engine.atr(d, int(p.get("atr_period", 14))).values
    O, H, L, C = d.Open.values, d.High.values, d.Low.values, d.Close.values
    DT = d.Datetime.values
    n = len(d)
    bars_per_day = max(1, round(n / max(1, d.day.nunique())))
    max_hold = int(p.get("max_hold_days", 5)) * bars_per_day
    atr_sl = p.get("atr_sl", 2.0); rr = p.get("rr", 2.0); trail_k = p.get("trail_atr", p.get("atr_sl", 2.0))
    warm = 60

    equity = START_CAP; pos = None; trades = []; eq_curve = []
    LOSS_CAP = (rms_caps or {}).get("loss_cap"); PROFIT_TGT = (rms_caps or {}).get("profit_target")
    # positional RMS overlay = MONTHLY loss-cap / profit-target (the intraday overlay's
    # daily caps at positional cadence): breach → square off + no new entry till next month.
    months = pd.to_datetime(d.Datetime).dt.to_period("M").astype(str).values
    cur_month = None; month_anchor_eq = START_CAP; locked = False

    def close_pos(i, price, reason):
        nonlocal equity, pos
        sgn = 1 if pos["side"] == "long" else -1
        pnl = (price - pos["entry"]) * pos["qty"] * sgn
        fee = 40.0 + abs(price * pos["qty"]) * 0.0002
        equity += pnl - fee
        trades.append(dict(side=pos["side"], entry=pos["entry"], exit=price, qty=pos["qty"],
                           pnl=pnl - fee, points=(price - pos["entry"]) * sgn,
                           entry_i=pos["entry_i"], exit_i=i, entry_dt=pos["entry_dt"],
                           exit_dt=DT[i], reason=reason, bars=i - pos["entry_i"]))
        pos = None

    for i in range(warm, n):
        if pos is not None:
            side = pos["side"]
            held = i - pos["entry_i"]
            if exit_style == "trail":
                if side == "long":
                    pos["stop"] = max(pos["stop"], C[i] - trail_k * a[i])
                else:
                    pos["stop"] = min(pos["stop"], C[i] + trail_k * a[i])
            # gap-aware stop/target check (overnight gap fills at OPEN)
            if side == "long":
                if O[i] <= pos["stop"]:
                    close_pos(i, O[i], "Gap SL")
                elif L[i] <= pos["stop"]:
                    close_pos(i, pos["stop"], "ATR SL")
                elif pos.get("target") and H[i] >= pos["target"]:
                    close_pos(i, pos["target"], "Target")
            else:
                if O[i] >= pos["stop"]:
                    close_pos(i, O[i], "Gap SL")
                elif H[i] >= pos["stop"]:
                    close_pos(i, pos["stop"], "ATR SL")
                elif pos.get("target") and L[i] <= pos["target"]:
                    close_pos(i, pos["target"], "Target")
            # opposite-signal flip exit
            if pos is not None and exit_style == "flip":
                if (side == "long" and short_e[i]) or (side == "short" and long_e[i]):
                    close_pos(i, C[i], "Signal flip")
            # max-hold cap
            if pos is not None and held >= max_hold:
                close_pos(i, C[i], "Max hold")

        # monthly RMS overlay (loss-cap / profit-target vs month-start equity)
        m = equity
        if pos is not None:
            m += (C[i] - pos["entry"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
        if months[i] != cur_month:
            cur_month = months[i]; month_anchor_eq = m; locked = False
        if rms_caps and not locked:
            month_pnl = m - month_anchor_eq
            if (LOSS_CAP and month_pnl <= -LOSS_CAP) or (PROFIT_TGT and month_pnl >= PROFIT_TGT):
                if pos is not None:
                    close_pos(i, C[i], "RMS max-loss" if month_pnl < 0 else "RMS profit-target")
                    m = equity
                locked = True

        if pos is None and not locked and i + 1 < n:
            lo, sh = long_e[i], short_e[i]
            if lo or sh:
                side = "long" if lo else "short"
                entry = O[i + 1]
                av = a[i] if a[i] > 0 else max(1.0, entry * 0.001)
                stop_dist = atr_sl * av
                qty = (RISK_PCT * equity) / max(stop_dist, 1e-6)
                qty = min(qty, LEV_CAP * equity / entry)          # 1x cap
                stop = entry - stop_dist if side == "long" else entry + stop_dist
                target = None
                if exit_style == "atr_rr":
                    tp = rr * stop_dist
                    target = entry + tp if side == "long" else entry - tp
                pos = dict(side=side, entry=entry, qty=qty, stop=stop, target=target,
                           entry_i=i + 1, entry_dt=DT[i + 1])

        eq_curve.append((DT[i], m))

    if pos is not None:
        close_pos(n - 1, C[n - 1], "End")
    eqdf = pd.DataFrame(eq_curve, columns=["Datetime", "equity"])
    return dict(trades=trades, equity=eqdf, final=equity, params=p,
                variant=name + "_pos", mode="positional")
