"""
Roadmap Monte-Carlo for 02.10.01 (BNF 9:20 hedged strangle). Per-1-lot real net
(from bnf_hedged_backtest at 5 lots / Rs4k basket = Rs800/lot) -> block-bootstrap
paths -> self-protecting sizing on the strategy's ~Rs5L tier -> per-month corridor.

Output: data/roadmap/bnf_strangle_hedged.json (same shape as weekly_ironfly_v1.json).
Display/planning only. Exit tuned to SL Rs4k / Target Rs8k (asymmetric) — Sharpe 3.26,
OOS 3.96. Median = optimistic-if-edge-holds (short-vol seller); forward-paper first.
"""
import os, sys, json, random, statistics as st
import pandas as pd

random.seed(21)
HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "..", "nifty_trend", "bnf_hedged_trades.csv")
df = pd.read_csv(CSV)
LOTS_BT = 5                                   # backtest ran at 5 lots / Rs4k basket
nets_1lot = [v / LOTS_BT for v in df["net"].tolist()]   # per-1-lot (Rs800/lot basket)
N = len(nets_1lot)
days = pd.to_datetime(df["day"])
span_years = (days.max() - days.min()).days / 365.25
trades_per_year = N / span_years
trades_per_month = trades_per_year / 12.0

worst_1lot = abs(min(nets_1lot))
avg_1lot = st.mean(nets_1lot)

BOOK0 = 500_000                              # 02.10.01 tier (~Rs4-5L reserved)
MONTHS = 24
SIP = 25_000
PATHS = 3000
BLOCK = 20                                    # ~1 trading-month block (daily strategy)
MARGIN_PER_LOT = 58_423                       # confirmed real Zerodha basket SPAN
CAP_LOTS = 40
START_LOTS = 5
UTIL = {0.12: 0.50, 0.08: 0.30}

def lot_step(risk):
    return MARGIN_PER_LOT / UTIL[risk]

def lots_for(eq, risk):
    by_step = START_LOTS + int((eq - BOOK0) // lot_step(risk))
    by_risk = int(eq * risk / worst_1lot) if worst_1lot else CAP_LOTS
    return max(1, min(by_step, by_risk, CAP_LOTS))

def block_seq(n):
    seq = []
    while len(seq) < n:
        s = random.randrange(0, max(1, N - BLOCK))
        seq.extend(nets_1lot[s:s + BLOCK])
    return seq[:n]

def run(risk, sip):
    n_tr = int(round(trades_per_month * MONTHS)) + 30
    monthly = [[] for _ in range(MONTHS + 1)]
    dd_paths = []
    for _ in range(PATHS):
        seq = block_seq(n_tr)
        eq = BOOK0; cur_m = 0; acc = 0.0; snaps = [BOOK0]
        for pnl1 in seq:
            eq += lots_for(eq, risk) * pnl1
            acc += 1.0 / trades_per_month
            while cur_m < int(acc) and len(snaps) <= MONTHS:
                cur_m += 1
                if sip: eq += SIP
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
    corr = {"p5": [], "p50": [], "p95": [], "lots50": []}
    for m in range(MONTHS + 1):
        col = monthly[m]
        corr["p5"].append(round(pct(col, .05) / 1e5, 3))
        corr["p50"].append(round(pct(col, .50) / 1e5, 3))
        corr["p95"].append(round(pct(col, .95) / 1e5, 3))
        corr["lots50"].append(lots_for(pct(col, .50), risk))
    med = corr["p50"][-1] * 1e5
    contributed = BOOK0 + (SIP * MONTHS if sip else 0)
    cagr = round(100 * ((med / contributed) ** 0.5 - 1), 1)
    corr["cagr"] = cagr
    corr["lot_step"] = round(lot_step(risk) / 1e5, 3)
    corr["maxdd_median"] = round(st.median(dd_paths) / 1e5, 3)
    corr["maxdd_p5"] = round(sorted(dd_paths)[int(.05 * len(dd_paths))] / 1e5, 3)
    return corr

out = {
    "strategy": "02.10.01 BNF 9:20 Hedged Strangle (POSITIONAL, 1-night hold)",
    "source": {"n": N, "sharpe": 4.56, "pf": 2.60, "p_value": None, "win": 55,
               "train_sharpe": 4.57, "oos_sharpe": 4.55,
               "span": f"{days.min().date()} to {days.max().date()}",
               "caveat": "POSITIONAL: SL Rs4k / Target Rs8k, hold max 1 overnight (enter 09:20, most exit same day on target/SL, else next-day). vs intraday: +44% net, ~Rs17k/yr less tax. Overnight-gap tail = Rs4k SL bypassed ~3x in 5yr (worst -Rs10.4k, wing-BOUNDED not capped at 10k). Sharpe>4 red-flag zone, wings BS-modeled (>lake +-10). Forward-paper vs live."},
    "per_lot": {"avg": round(avg_1lot), "worst": round(-worst_1lot),
                "best": round(max(nets_1lot)), "std": round(st.pstdev(nets_1lot)),
                "trades_per_year": round(trades_per_year, 1)},
    "book0": BOOK0, "start_lots": START_LOTS, "months": MONTHS,
    "governors": {"margin_per_lot": MARGIN_PER_LOT, "cap_lots": CAP_LOTS, "util": UTIL},
    "runs": {},
}
for risk in (0.12, 0.08):
    for sip in (False, True):
        out["runs"][f"r{int(risk*100)}_{'sip' if sip else 'profit'}"] = run(risk, sip)

dest = os.path.join(HERE, "..", "..", "data", "roadmap", "bnf_strangle_hedged.json")
json.dump(out, open(dest, "w"), indent=1)
print(f"per-1-lot: avg Rs{out['per_lot']['avg']:,}  worst -Rs{worst_1lot:,.0f}  ~{trades_per_year:.0f}/yr  ({N} trades)")
for k, v in out["runs"].items():
    print(f"{k}: start {START_LOTS} lots  m24 p5 Rs{v['p5'][24]:.2f}L / median Rs{v['p50'][24]:.2f}L / p95 Rs{v['p95'][24]:.2f}L  "
          f"(~{v['lots50'][24]} lots)  CAGR {v['cagr']}%  maxDD med Rs{v['maxdd_median']:.2f}L")
print("wrote", dest)
