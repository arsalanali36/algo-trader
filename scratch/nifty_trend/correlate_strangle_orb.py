"""Range Strangle x ORB — (1) earliest entry, (2) softer ORB filter, (3) CORRELATION + PORTFOLIO.

User's big insight: don't use ORB as a filter INSIDE the strangle — run BOTH together. ORB makes
money on breakout/trend days, the strangle on range-bound days → negatively correlated → the
strangle DIVERSIFIES the ORB book (jis din ORB nahi chala, strangle chalega). A standalone
gate-fail strategy can still add real value to a portfolio if it's negatively correlated.

Part 1: entry 09:15 (first bar) vs 09:20 (earlier = better trend held so far).
Part 2: softer ORB filter (allow a small breakout, keep more trades).
Part 3: daily-P&L correlation of the strangle vs the DEPLOYED Mid-Day ORB (runs/mid_orb_nifty),
        and the combined 1+1 lot portfolio's Sharpe / maxDD / drawdown vs each alone.

Run: python -X utf8 correlate_strangle_orb.py
"""
import os, json, datetime as dt
import numpy as np

import bs_option as bs
import real_struct2 as r2
import probe_range_strangle_positional as P
import probe_range_strangle_orb as O

HERE = os.path.dirname(os.path.abspath(__file__))
LB, DIST = 10, 0.5


def _series_metrics(daily):
    """daily: dict date->pnl. Returns net, sharpe, maxDD (peak-to-trough of cum equity)."""
    if not daily:
        return dict(n=0, net=0, sharpe=0, maxdd=0)
    days = sorted(daily)
    p = np.array([daily[d] for d in days], dtype=float)
    eq = np.cumsum(p); peak = np.maximum.accumulate(eq); dd = eq - peak
    return dict(n=len(p), net=float(p.sum()),
                sharpe=float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) else 0.0,
                maxdd=float(dd.min()))


def strangle_daily(g, ext, lot, entry_hm, tgt, sl, orb_filter=False, orb_buf=0.0):
    trades, _ = O.backtest(g, ext, lot, entry_hm, tgt, sl, orb_filter=orb_filter, orb_buf=orb_buf)
    return {t["day"]: t["pnl"] for t in trades}


def orb_daily():
    """deployed Mid-Day ORB daily P&L from its own backtest run (bs pass, full)."""
    path = os.path.join(HERE, "runs", "mid_orb_nifty", "results.js")
    txt = open(path, encoding="utf-8").read()
    obj = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    at = obj["combos"]["bs|full"]["all_trades"]
    daily = {}
    for t in at:
        d = str(t.get("entry_dt", t.get("exit_dt", "")))[:10]
        try:
            dd = dt.date.fromisoformat(d)
        except ValueError:
            continue
        daily[dd] = daily.get(dd, 0.0) + float(t.get("pnl", 0))
    return daily


def main():
    import warnings; warnings.filterwarnings("ignore")
    lot = bs.get_nifty_lot() or 65
    g = r2.grid("WEEK", "5m")
    ext = P.trailing_extremes(P.daily_hl(), LB)

    print(f"=== 1. EARLIEST ENTRY (strangle, T40/SL40, lb={LB} d={DIST}%) ===")
    for et in (dt.time(9, 15), dt.time(9, 20)):
        O.show(f"entry {et.strftime('%H:%M')} T40/SL40", O.backtest(g, ext, lot, et, 40, 40)[0])
    print("  (lake bars 5-min: pehla bar 9:15 hai; 9:16-17 real-world fill volatile hoga)\n")

    print("=== 2. SOFTER ORB FILTER (entry 9:45, T40/SL40, buffer = x*OR-width each side) ===")
    for buf in (0.0, 0.5, 1.0, 2.0, 3.0):
        tr, sk = O.backtest(g, ext, lot, dt.time(9, 45), 40, 40, orb_filter=True, orb_buf=buf)
        O.show(f"buf={buf} (kept {len(tr)}, skip {sk})", tr)
    O.show("no filter (baseline)", O.backtest(g, ext, lot, dt.time(9, 45), 40, 40)[0])

    print("\n=== 3. CORRELATION + PORTFOLIO — strangle (standalone best) vs deployed Mid-Day ORB ===")
    st = strangle_daily(g, ext, lot, dt.time(9, 20), 40, 40)     # standalone winner
    orb = orb_daily()
    sm, om = _series_metrics(st), _series_metrics(orb)
    print(f"  Strangle:  {sm['n']} days, net Rs {sm['net']:,.0f}, Sharpe {sm['sharpe']:.2f}, maxDD Rs {sm['maxdd']:,.0f}")
    print(f"  Mid-ORB :  {om['n']} days, net Rs {om['net']:,.0f}, Sharpe {om['sharpe']:.2f}, maxDD Rs {om['maxdd']:,.0f}")

    both = sorted(set(st) & set(orb))
    if both:
        a = np.array([st[d] for d in both]); b = np.array([orb[d] for d in both])
        corr = float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 and a.std() and b.std() else 0.0
        print(f"\n  Days BOTH traded: {len(both)}  |  daily-P&L correlation = {corr:+.3f}  "
              f"({'NEGATIVE ✓ (complementary)' if corr < -0.05 else 'positive' if corr > 0.05 else '~zero'})")
    onlyS = len(set(st) - set(orb)); onlyO = len(set(orb) - set(st))
    print(f"  Only strangle traded: {onlyS} days | only ORB: {onlyO} days | overlap: {len(both)} days")

    # combined 1+1 lot portfolio on the UNION of days
    union = sorted(set(st) | set(orb))
    port = {d: st.get(d, 0.0) + orb.get(d, 0.0) for d in union}
    pm = _series_metrics(port)
    print(f"\n  COMBINED (1 lot each, union of days):")
    print(f"    net Rs {pm['net']:,.0f}  Sharpe {pm['sharpe']:.2f}  maxDD Rs {pm['maxdd']:,.0f}  ({pm['n']} days)")
    print(f"    vs Strangle-alone Sharpe {sm['sharpe']:.2f} / ORB-alone Sharpe {om['sharpe']:.2f}")
    print(f"    maxDD: combined Rs {pm['maxdd']:,.0f} vs sum-of-alones Rs {sm['maxdd']+om['maxdd']:,.0f} "
          f"(better={'YES ✓' if pm['maxdd'] > sm['maxdd']+om['maxdd'] else 'no'})")
    # diversification check: does adding the strangle improve ORB's own Sharpe?
    print(f"\n  Diversification verdict: adding strangle to ORB moves Sharpe {om['sharpe']:.2f} -> "
          f"{pm['sharpe']:.2f} ({'IMPROVES ✓' if pm['sharpe'] > om['sharpe'] else 'worsens'})")


if __name__ == "__main__":
    main()
