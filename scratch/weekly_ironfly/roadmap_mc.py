"""
Institutional roadmap Monte-Carlo for 02.17 (weekly iron-fly, live variant
wing250 | TARGET 50% of credit). Real per-trade P&L -> block-bootstrap paths ->
self-protecting lot sizing on a Rs.14L book -> per-month corridor (p5..p95).

Two risk levels (12% / 8% worst-trade) x two capital modes (profit-only / +SIP).
Output: roadmap_data.json  (consumed by the artifact).
Display/planning only — no order path. Assumes the 2021-26 edge persists (it may not).
"""
import json, random, statistics as st

random.seed(11)
R = json.load(open("results.json", encoding="utf-8"))
V = R["variants"]["wing250 | TARGET 50% of credit"]
LOTS_META = R["lots"]                       # stats/rows were computed at this many lots
rows = V["rows"]
nets_1lot = [r["net"] / LOTS_META for r in rows]     # per-1-lot real net (Rs)
N = len(nets_1lot)

worst_1lot = abs(min(nets_1lot))            # Rs.7,129
avg_1lot = st.mean(nets_1lot)
trades_per_year = N / 5.04                   # 2021-07 -> 2026-07 span
trades_per_month = trades_per_year / 12.0

BOOK0 = 1_400_000
MONTHS = 24
SIP = 50_000                                 # Rs/month accelerator (assumption; editable)
PATHS = 3000
BLOCK = 10

# Real-world governors (the reason a naive risk-only MC prints crores):
MARGIN_PER_LOT = 64_400     # CONFIRMED via risk_gate.kite_basket_margin (5-lot=Rs3,22,024), 2026-08-26
CAP_LOTS = 40               # capacity ceiling — beyond this, slippage eats the edge (untested)
START_LOTS = 5              # user starts here (conservative; rule ramps from this)
UTIL = {0.12: 0.50, 0.08: 0.30}   # max fraction of equity tied up as margin per risk track

def K_for(risk):                             # capital that must back one lot (risk rule)
    return worst_1lot / risk                 # so one worst trade = risk * that capital

def lot_step(risk):
    """Equity gain needed for +1 lot (the self-protecting ramp). Margin-util binds
    here (bigger than the risk step), so add a lot each time margin room grows by one."""
    return MARGIN_PER_LOT / UTIL[risk]

def lots_for(eq, risk):
    """Start at START_LOTS on the book; +1 lot per lot_step of equity gain, −1 per step
    of drawdown (self-protecting). Never past capacity, never past the risk cap, min 1."""
    by_step = START_LOTS + int((eq - BOOK0) // lot_step(risk))
    by_risk = int(eq * risk / worst_1lot)
    return max(1, min(by_step, by_risk, CAP_LOTS))

def block_seq(n_trades):
    seq = []
    while len(seq) < n_trades:
        s = random.randrange(0, N - BLOCK)
        seq.extend(nets_1lot[s:s + BLOCK])
    return seq[:n_trades]

def run(risk, sip):
    K = K_for(risk)
    n_tr = int(round(trades_per_month * MONTHS)) + 8
    monthly = [[] for _ in range(MONTHS + 1)]     # equity samples per month across paths
    dd_paths = []
    for _ in range(PATHS):
        seq = block_seq(n_tr)
        eq = BOOK0
        cur_m = 0
        acc = 0.0
        snaps = [BOOK0]
        for pnl1 in seq:
            lots = lots_for(eq, risk)
            eq += lots * pnl1
            acc += 1.0 / trades_per_month
            while cur_m < int(acc) and len(snaps) <= MONTHS:
                cur_m += 1
                if sip:
                    eq += SIP
                snaps.append(eq)
        while len(snaps) <= MONTHS:
            snaps.append(eq)
        peak = BOOK0; mdd = 0.0
        for s in snaps:
            peak = max(peak, s); mdd = min(mdd, s - peak)
        dd_paths.append(mdd)
        for m in range(MONTHS + 1):
            monthly[m].append(snaps[m])
    def pct(a, q):
        a = sorted(a); return a[min(len(a) - 1, int(q * len(a)))]
    corridor = {"p5": [], "p25": [], "p50": [], "p75": [], "p95": [], "lots50": []}
    for m in range(MONTHS + 1):
        col = monthly[m]
        corridor["p5"].append(round(pct(col, .05)))
        corridor["p25"].append(round(pct(col, .25)))
        corridor["p50"].append(round(pct(col, .50)))
        corridor["p75"].append(round(pct(col, .75)))
        corridor["p95"].append(round(pct(col, .95)))
        corridor["lots50"].append(lots_for(pct(col, .50), risk))
    med_final = corridor["p50"][-1]
    contributed_total = BOOK0 + (SIP * MONTHS if sip else 0)
    # median 1-yr and 2-yr CAGR on contributed capital
    def cagr(v, yrs, base): return (v / base) ** (1 / yrs) - 1
    return {
        "risk": risk, "sip": sip, "K": round(K), "corridor": corridor,
        "lot_step": round(lot_step(risk)), "start_lots": lots_for(BOOK0, risk),
        "final": {"p5": corridor["p5"][-1], "p50": corridor["p50"][-1], "p95": corridor["p95"][-1]},
        "y1": {"p5": corridor["p5"][12], "p50": corridor["p50"][12], "p95": corridor["p95"][12]},
        "contributed": contributed_total,
        "maxdd_median": round(st.median(dd_paths)),
        "maxdd_p5": round(sorted(dd_paths)[int(.05 * len(dd_paths))]),
        "cagr_med_2y": round(100 * cagr(med_final, 2, contributed_total), 1),
    }

out = {
    "strategy": "02.17 Weekly Iron-Fly (day-after-expiry ATM + wings)",
    "source": {"n": N, "sharpe": V["stats"]["sharpe"], "pf": V["stats"]["pf"],
               "p_value": V["stats"]["p_value"], "win": V["stats"]["win"],
               "train_sharpe": V["stats"]["train"]["sharpe"], "oos_sharpe": V["stats"]["oos"]["sharpe"],
               "span": "2021-07 to 2026-07"},
    "per_lot": {"avg": round(avg_1lot), "worst": round(worst_1lot),
                "best": round(max(nets_1lot)), "std": round(st.pstdev(nets_1lot)),
                "trades_per_year": round(trades_per_year, 1)},
    "book0": BOOK0, "sip": SIP, "months": MONTHS, "start_lots": START_LOTS,
    "governors": {"margin_per_lot": MARGIN_PER_LOT, "cap_lots": CAP_LOTS, "util": UTIL},
    "runs": {}
}
for risk in (0.12, 0.08):
    for sip in (False, True):
        key = f"r{int(risk*100)}_{'sip' if sip else 'profit'}"
        out["runs"][key] = run(risk, sip)

json.dump(out, open("roadmap_data.json", "w"), indent=1)
# console summary
print(f"02.17 per-lot: avg Rs{out['per_lot']['avg']:,}  worst -Rs{out['per_lot']['worst']:,}  "
      f"~{out['per_lot']['trades_per_year']}/yr")
for k, v in out["runs"].items():
    c = v["corridor"]
    print(f"\n{k}: start {v['start_lots']} lots (Rs{v['K']:,}/lot budget)  contributed Rs{v['contributed']:,}")
    for m in (3, 6, 12, 24):
        print(f"   m{m:>2}: p5 Rs{c['p5'][m]:>10,}  median Rs{c['p50'][m]:>10,}  "
              f"p95 Rs{c['p95'][m]:>11,}  (~{c['lots50'][m]} lots)")
    print(f"   median 2y-CAGR on contributed: {v['cagr_med_2y']}%   "
          f"median maxDD Rs{v['maxdd_median']:,}  p5 maxDD Rs{v['maxdd_p5']:,}")
