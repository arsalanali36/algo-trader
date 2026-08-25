"""Naked vs Hedged comparison for StockMock short-strangle family (02.11 / 02.13 / 02.14).

Reuses the exact StockMock-parity engine (scratch/nifty_trend/sm_backtest.simulate) so the
naked numbers are byte-identical to what the registry shows. Hedged variants = same config
with BUY wing legs appended at `off` strike-steps from ATM (each leg resolves its own strike,
so shorts stay on their atm_pct/sw_mult/ATM selector; wings sit further OTM).

P&L / Sharpe / MaxDD / win% come straight from the engine (100% real premium, held-strike,
open-entry, high-SL, 0.5% slip, date-aware Zerodha charges). Margin is estimated separately:
  hedged = defined risk (max loss of the wider vertical, computed from actual strikes/premiums)
  naked  = notional * SPAN% (labelled estimate; SPAN varies intraday, this is a fair proxy)
"""
import os, sys, json, copy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"D:/KHAZANA/KHAZANA/PYTHON/CODE3B- TV BACKTEST ENGINE"
NT = os.path.join(ROOT, "scratch", "nifty_trend")
for p in (ROOT, os.path.join(ROOT, "_ops"), os.path.join(ROOT, "_core"),
          os.path.join(ROOT, "_data"), NT):
    if p not in sys.path:
        sys.path.insert(0, p)
import _paths  # noqa
import sm_backtest as smb
import sm_runner as smr

FROM, TO = "2021-01-01", "2026-12-31"
SPAN_PCT = 0.11          # naked short-option SPAN as % of notional (fair NIFTY proxy)

# Per-strategy hedged variants. wings = list of (opt, off_from_ATM) BUY legs.
# NIFTY step=50, lot=75(recent). Shorts: 02.11 at ATM(0); 02.13 at ~+-5 steps (1%);
# 02.14 at ~+-4 steps (1x straddle premium). Wings placed to sit clearly beyond the short.
TARGETS = {
    "sm_nifty_expiry_v1": {   # 02.11 expiry straddle, short CE/PE at ATM (lots 5 CE / 6 PE)
        "name": "02.11 Expiry-Day Straddle",
        "variants": {
            "Hedged +-250 (5 wing)":  [("CE", +5), ("PE", -5)],
            "Hedged +-500 (10 wing)": [("CE", +10), ("PE", -10)],
        },
    },
    "sm_nifty_atm1pct_v1": {  # 02.13 short strangle ATM+-1% (~+-5 steps), lots 5
        "name": "02.13 Short Strangle ATM+-1%",
        "variants": {
            "Hedged wings off +-8":  [("CE", +8), ("PE", -8)],
            "Hedged wings off +-10": [("CE", +10), ("PE", -10)],
        },
    },
    "sm_nifty_swidth_v1": {   # 02.14 straddle-width strangle (~+-4 steps), lots 5
        "name": "02.14 Straddle-Width Strangle",
        "variants": {
            "Hedged wings off +-8":  [("CE", +8), ("PE", -8)],
            "Hedged wings off +-10": [("CE", +10), ("PE", -10)],
        },
    },
}
STEP = 50


def make_hedged(cfg, wings):
    """Return a deep-copied cfg with BUY wing legs appended (off-based, no SL, held to EOD)."""
    c = copy.deepcopy(cfg)
    # wing lots = match the short leg count of that opt-type (balanced defined-risk)
    short_lots = {"CE": 0, "PE": 0}
    for lg in c["legs"]:
        if lg["side"] == "SELL":
            short_lots[lg["opt"]] = max(short_lots[lg["opt"]], lg["lots"])
    for opt, off in wings:
        c["legs"].append({
            "opt": opt, "side": "BUY", "off": off,
            "sp_pct": None, "cp_rs": None, "atm_pct": None, "sw_mult": None,
            "strike_mode": "atm",              # off-based ATM offset
            "lots": short_lots.get(opt, 1) or 1,
            "sl_pct": None, "tp_pct": None,     # wings are protection: held to EOD, no SL
        })
    return c


def margins(days):
    """Per-day (naked_span, hedged_defined_risk) averaged. Hedged = max-loss of wider vertical."""
    naked_l, hedged_l = [], []
    for d in days:
        legs = d["legs"]
        lot = d["lot"]
        # net credit of the whole structure (sell entry - buy entry) * qty
        net_credit = sum((1 if l["side"] == "SELL" else -1) * l["entry"] * l["qty"] for l in legs)
        # naked SPAN = notional of short legs * SPAN%
        short_notional = sum(d["atm"] * l["qty"] for l in legs if l["side"] == "SELL")
        naked_l.append(short_notional * SPAN_PCT)
        # hedged defined risk = max over CE/PE side of (spread_width_pts * side_qty) - net_credit
        side_maxloss = {}
        for opt in ("CE", "PE"):
            s = next((l for l in legs if l["opt"] == opt and l["side"] == "SELL"), None)
            b = next((l for l in legs if l["opt"] == opt and l["side"] == "BUY"), None)
            if s and b:
                width = abs(s["strike"] - b["strike"])
                side_maxloss[opt] = width * min(s["qty"], b["qty"])
        if side_maxloss:
            hedged_l.append(max(side_maxloss.values()) - net_credit)
        else:
            hedged_l.append(None)
    nk = sum(naked_l) / len(naked_l) if naked_l else 0
    hd = [x for x in hedged_l if x is not None]
    hg = sum(hd) / len(hd) if hd else None
    return nk, hg


def run(cfg):
    days = smb.simulate(cfg, FROM, TO)
    if not days:
        return None
    m = smb._metrics(days, 331000)
    nk, hg = margins(days)
    net = m["net_abs"]
    return {
        "days": len(days), "net": net, "sharpe": m["sharpe"], "maxdd": m["maxdd_abs"],
        "win": m["win_rate"], "pf": m.get("profit_factor"), "avg": m["expectancy"],
        "naked_margin": nk, "hedged_margin": hg,
        "ror": (net / nk * 100) if nk else None,          # return on naked SPAN
        "ror_h": (net / hg * 100) if hg else None,         # return on hedged margin
    }


def fmt(v):
    if v is None: return "   -   "
    return f"{v:,.0f}"


for cid, spec in TARGETS.items():
    ncfg = json.load(open(os.path.join(ROOT, "nifty_config.json"), encoding="utf-8"))
    cfg = smr.parse_cfg(cid, ncfg.get(cid, {}))
    print("\n" + "=" * 92)
    print(spec["name"], f"  ({cid})")
    print("=" * 92)
    base = run(cfg)
    rows = [("NAKED (base)", base)]
    for label, wings in spec["variants"].items():
        rows.append((label, run(make_hedged(cfg, wings))))
    hdr = f"{'Variant':<26}{'Net Rs':>11}{'Sharpe':>8}{'MaxDD':>10}{'Win%':>7}{'PF':>6}{'Avg/day':>9}"
    print(hdr)
    print("-" * len(hdr))
    for label, r in rows:
        if not r:
            print(f"{label:<26}  (no trades)"); continue
        print(f"{label:<26}{fmt(r['net']):>11}{r['sharpe']:>8.2f}{('-'+fmt(r['maxdd'])):>10}"
              f"{r['win']:>6.1f}%{(r['pf'] if r['pf'] else 0):>6.2f}{fmt(r['avg']):>9}")
    print()
    print(f"{'Variant':<26}{'~Margin Rs':>13}{'Net/Margin%':>13}{'  vs naked P&L':>16}")
    print("-" * 68)
    b = base
    for label, r in rows:
        if not r: continue
        if label.startswith("NAKED"):
            marg, ror = r["naked_margin"], r["ror"]
        else:
            marg, ror = r["hedged_margin"], r["ror_h"]
        dpnl = "" if label.startswith("NAKED") else f"{(r['net']-b['net'])/b['net']*100:+.0f}% P&L"
        print(f"{label:<26}{fmt(marg):>13}{(ror if ror else 0):>12.0f}%{dpnl:>16}")
print("\n(Net/Margin% = full-period P&L as % of avg capital blocked. Naked SPAN = notional x "
      f"{SPAN_PCT:.0%} estimate; hedged margin = exact defined-risk.)")
