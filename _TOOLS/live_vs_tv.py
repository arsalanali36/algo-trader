"""live_vs_tv.py — the LIVE engine vs TradingView, on one chart, on real data.

Why not validate_strategy.py: that harness scores `backtest_day`, its own
hand-copy of the engine ("Mirror of run_signal_engine but COLLECTS every
trade"). The copy is not the thing that trades. On 2026-07-17 the two were
found to disagree on where max_candle_size even applies — the copy gated the
entry (like the Pine), the live engine gated zone formation at a hardcoded 25.
So a score from the copy says nothing about the strategy that places orders.

This drives `range_trader.run_signal_engine` itself, via its `trades_out`
collector, and puts its trades next to TradingView's own exported ones:
per-side stats (net, win%, PF, Sharpe, max DD) and one chart with every trade
on it — TV orange, engine blue — so a human can just look.

Data the same way the live loop gets it, deliberately:
  * 5m bars resampled from the per-day 1-min CSVs
  * ATR continuous across days (warm-up) — the engine resets day state itself
  * key levels rebuilt per day from daily bars up to THAT day (never later —
    that is TRAP #31, stale/lookahead pivots)

Usage:
    python -X utf8 _TOOLS/live_vs_tv.py --csv <tv_list_of_trades.csv> [--from D] [--to D]
"""

import argparse
import glob
import os
import sys
from datetime import time as dtime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _paths  # noqa: F401,E402

import range_trader as rt  # noqa: E402
import validate_strategy as vs  # noqa: E402  (data loading + TV csv parsing — Rule 6B)

EXIT_HM = dtime(15, 15)


def cont5_bars():
    """ONE continuous 5m series across every available day — same construction
    run() uses, so ATR(14) is warm/continuous like TradingView rather than reset
    each morning."""
    frames = []
    for path in sorted(glob.glob(os.path.join(vs.DATA_DIR, "NIFTY_2026-*.csv"))):
        d5 = vs.resample_5m(vs.load_1m(path))
        d5["date"] = d5["time"].dt.date
        frames.append(d5)
    return pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True)


def live_trades(cont5, daily, date_from, date_to):
    """Run the LIVE engine day by day. Returns list of closed trades."""
    days = sorted(cont5["date"].unique())
    if date_from:
        days = [d for d in days if str(d) >= date_from]
    if date_to:
        days = [d for d in days if str(d) <= date_to]

    cfg = dict(vs.CFG)
    out = []
    for d in days:
        sub = daily[daily["date"] <= d].reset_index(drop=True)
        if len(sub) < 2:
            continue
        levels = rt.build_key_levels(sub, is_index=True)

        # Warm-up: the engine needs >=20 bars and a warm ATR. Feed it the
        # previous days too — it resets its own day-scoped state on each date
        # change, so earlier days can only warm indicators, never leak trades.
        upto = cont5[cont5["time"].dt.date <= d].reset_index(drop=True)
        if len(upto) < 21:
            continue
        raw = []
        rt.run_signal_engine(upto, levels, cfg, trades_out=raw)
        raw = [r for r in raw if r["time"].date() == d]

        # pair entries with the next exit; force a 3:15 close like the Pine
        pos = None
        for r in raw:
            if r["kind"].startswith("ENTRY"):
                if pos is None:
                    pos = dict(entry_time=r["time"], entry_price=r["price"],
                               side="Long" if r["kind"] == "ENTRY_LONG" else "Short")
            elif pos is not None:
                pos.update(exit_time=r["time"], exit_price=r["price"], exit_reason=r["reason"])
                out.append(pos); pos = None
        if pos is not None:
            eod = upto[(upto["time"].dt.date == d) & (upto["time"].dt.time >= EXIT_HM)]
            last = eod.iloc[0] if not eod.empty else upto.iloc[-1]
            pos.update(exit_time=last["time"], exit_price=float(last["close"]),
                       exit_reason="3:15 Daily Exit")
            out.append(pos)
    return out


def stats(trades):
    """Per-trade point P&L — no lots, no charges. Both sides measured the same
    way, so the comparison is honest even though the rupees are not."""
    if not trades:
        return dict(n=0, net=0, win=0, pf=0, sharpe=0, maxdd=0, avg=0)
    pnl = [(t["exit_price"] - t["entry_price"]) * (1 if t["side"] == "Long" else -1)
           for t in trades]
    wins = [p for p in pnl if p > 0]
    loss = [p for p in pnl if p <= 0]
    eq, peak, dd = 0.0, 0.0, 0.0
    for p in pnl:
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    mean = sum(pnl) / len(pnl)
    var = sum((p - mean) ** 2 for p in pnl) / len(pnl)
    sd = var ** 0.5
    return dict(n=len(pnl), net=sum(pnl), win=100.0 * len(wins) / len(pnl),
                pf=(sum(wins) / abs(sum(loss))) if loss and sum(loss) else float("inf"),
                sharpe=(mean / sd * (len(pnl) ** 0.5)) if sd else 0.0,
                maxdd=dd, avg=mean)


def match(tv, eng, tol_bars=1):
    """Pair by entry time +-tol bars, same side. TV fills at the NEXT bar's open,
    the engine stamps the signal bar — so a genuine match is naturally 1 bar
    apart. Nothing here is scored on price."""
    left = list(eng)
    pairs = []
    for t in tv:
        hit = None
        for k, e in enumerate(left):
            if e["side"].lower() == t["side"].lower() and \
               abs((e["entry_time"] - t["entry_time"]).total_seconds()) <= tol_bars * 300:
                hit = left.pop(k); break
        pairs.append((t, hit))
    return pairs, left


def html(tv, eng, pairs, extra, bars, out):
    st_tv, st_en = stats(tv), stats(eng)
    matched = sum(1 for _, e in pairs if e)

    def fmt(x, d=1):
        return f"{x:,.{d}f}"

    rows = []
    for t, e in pairs:
        if e:
            de = int((e["entry_time"] - t["entry_time"]).total_seconds() // 300)
            cls, note = ("ok", f"{de:+d} bar") if de else ("ok", "exact")
        else:
            cls, note = "bad", "engine ne nahi liya"
        rows.append(
            f"<tr class={cls}><td>{t['entry_time']:%Y-%m-%d %H:%M}</td><td>{t['side']}</td>"
            f"<td>{fmt(t['entry_price'],2)}</td><td>{t.get('exit_reason','')}</td>"
            f"<td>{e['entry_time']:%H:%M}</td><td>{fmt(e['entry_price'],2)}</td>"
            f"<td>{e.get('exit_reason','')}</td><td>{note}</td></tr>"
            if e else
            f"<tr class={cls}><td>{t['entry_time']:%Y-%m-%d %H:%M}</td><td>{t['side']}</td>"
            f"<td>{fmt(t['entry_price'],2)}</td><td>{t.get('exit_reason','')}</td>"
            f"<td colspan=3 style='color:#f85149'>—</td><td>{note}</td></tr>")
    for e in extra:
        rows.append(
            f"<tr class=bad><td colspan=4 style='color:#8b949e'>— (TV ne nahi liya)</td>"
            f"<td>{e['entry_time']:%Y-%m-%d %H:%M}</td><td>{fmt(e['entry_price'],2)}</td>"
            f"<td>{e.get('exit_reason','')}</td><td>engine-only</td></tr>")

    cd = [dict(t=int(r["time"].timestamp()), o=float(r["open"]), h=float(r["high"]),
               l=float(r["low"]), c=float(r["close"])) for _, r in bars.iterrows()]
    mk = ([dict(t=int(x["entry_time"].timestamp()), p=float(x["entry_price"]),
                s=x["side"], src="TV") for x in tv] +
          [dict(t=int(x["entry_time"].timestamp()), p=float(x["entry_price"]),
                s=x["side"], src="PY") for x in eng])

    def card(title, s, colour):
        return f"""<div class=card><h3 style="color:{colour}">{title}</h3>
        <div class=big>{fmt(s['net'])} <span>pts</span></div>
        <table class=kv>
        <tr><td>Trades</td><td>{s['n']}</td></tr>
        <tr><td>Win %</td><td>{fmt(s['win'])}%</td></tr>
        <tr><td>Profit factor</td><td>{fmt(s['pf'],2)}</td></tr>
        <tr><td>Sharpe (per-trade)</td><td>{fmt(s['sharpe'],2)}</td></tr>
        <tr><td>Max drawdown</td><td>{fmt(s['maxdd'])} pts</td></tr>
        <tr><td>Avg / trade</td><td>{fmt(s['avg'],1)} pts</td></tr>
        </table></div>"""

    doc = f"""<!doctype html><meta charset=utf-8><title>Live engine vs TradingView</title>
<style>
body{{background:#0d1117;color:#e6edf3;font:13px/1.5 system-ui,Segoe UI,sans-serif;margin:0;padding:18px}}
h1{{font-size:17px;margin:0 0 4px}} .sub{{color:#8b949e;margin-bottom:16px}}
.cards{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;min-width:210px}}
.card h3{{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.5px}}
.big{{font-size:24px;font-weight:700;margin-bottom:8px}} .big span{{font-size:12px;color:#8b949e;font-weight:400}}
.kv{{width:100%;border-collapse:collapse}} .kv td{{padding:2px 0;color:#8b949e}} .kv td:last-child{{text-align:right;color:#e6edf3}}
#chart{{height:520px;background:#161b22;border:1px solid #30363d;border-radius:8px;margin-bottom:16px}}
table.t{{width:100%;border-collapse:collapse;font-size:12px}}
table.t th{{text-align:left;color:#8b949e;font-weight:600;border-bottom:1px solid #30363d;padding:6px 8px;position:sticky;top:0;background:#0d1117}}
table.t td{{padding:5px 8px;border-bottom:1px solid #21262d}}
tr.ok td:last-child{{color:#3fb950}} tr.bad td:last-child{{color:#f85149}}
.legend span{{margin-right:14px}} .o{{color:#d29922}} .b{{color:#1f6feb}}
</style>
<h1>Live engine vs TradingView — {vs.CFG['max_candle_size']}pt candle cap, {vs.CFG['max_trades_per_symbol']} trades/day</h1>
<div class=sub>Engine = <b>range_trader.run_signal_engine</b> (the one that places orders), not a backtest copy.
TV = your own exported List of Trades. Entry match = same side within 1 bar (TV fills next bar's open).</div>
<div class=cards>
  {card('TradingView', st_tv, '#d29922')}
  {card('Python (live engine)', st_en, '#1f6feb')}
  <div class=card><h3>Match</h3>
    <div class=big>{100.0*matched/len(tv) if tv else 0:.1f}<span>% entries</span></div>
    <table class=kv>
    <tr><td>TV trades</td><td>{len(tv)}</td></tr>
    <tr><td>Engine trades</td><td>{len(eng)}</td></tr>
    <tr><td>Matched</td><td>{matched}</td></tr>
    <tr><td>TV-only</td><td>{len(tv)-matched}</td></tr>
    <tr><td>Engine-only</td><td>{len(extra)}</td></tr>
    </table></div>
</div>
<div class=legend style="margin-bottom:8px">
  <span class=o>▲▼ TradingView</span><span class=b>▲▼ Python engine</span>
  <span style="color:#8b949e">— dono ek hi bar pe = match</span>
</div>
<div id=chart></div>
<table class=t><thead><tr>
<th>TV entry</th><th>Side</th><th>TV price</th><th>TV exit</th>
<th>PY entry</th><th>PY price</th><th>PY exit</th><th>Status</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<script src="../static/vendor/lightweight-charts.standalone.production.js"></script>
<script>
const candles={cd!r}, marks={mk!r};
const el=document.getElementById('chart');
const ch=LightweightCharts.createChart(el,{{width:el.clientWidth,height:520,
  layout:{{background:{{color:'#161b22'}},textColor:'#8b949e'}},
  grid:{{vertLines:{{color:'#21262d'}},horzLines:{{color:'#21262d'}}}},
  timeScale:{{timeVisible:true}}}});
const s=ch.addCandlestickSeries({{upColor:'#3fb950',downColor:'#f85149',
  borderVisible:false,wickUpColor:'#3fb950',wickDownColor:'#f85149'}});
s.setData(candles.map(c=>({{time:c.t,open:c.o,high:c.h,low:c.l,close:c.c}})));
s.setMarkers(marks.map(m=>({{time:m.t,
  position:m.s==='Long'?'belowBar':'aboveBar',
  color:m.src==='TV'?'#d29922':'#1f6feb',
  shape:m.s==='Long'?'arrowUp':'arrowDown',
  text:m.src+' '+m.s}})).sort((a,b)=>a.time-b.time));
ch.timeScale().fitContent();
addEventListener('resize',()=>ch.applyOptions({{width:el.clientWidth}}));
</script>"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="TV List of Trades export")
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    a = ap.parse_args()

    tv = vs.parse_tv(a.csv)
    if a.dfrom:
        tv = [t for t in tv if str(t["entry_time"].date()) >= a.dfrom]
    if a.dto:
        tv = [t for t in tv if str(t["entry_time"].date()) <= a.dto]

    cont5 = cont5_bars()
    daily = vs.daily_bars()

    # A TV trade on a day we have no 5m data for is a DATA gap, not an engine
    # miss — scoring it would just manufacture fake drift. run() drops these too.
    have = set(cont5["date"].unique())
    dropped = sorted({t["entry_time"].date() for t in tv if t["entry_time"].date() not in have})
    tv = [t for t in tv if t["entry_time"].date() in have]
    if dropped:
        print("  [%d TV din chhode — un dino ka 5m data hi nahi: %s]"
              % (len(dropped), ", ".join(str(d) for d in dropped[:6])))

    eng = live_trades(cont5, daily, a.dfrom, a.dto)
    pairs, extra = match(tv, eng)
    matched = sum(1 for _, e in pairs if e)

    st_tv, st_en = stats(tv), stats(eng)
    print()
    print("  " + "=" * 66)
    print("  LIVE ENGINE vs TRADINGVIEW   %s .. %s" % (a.dfrom or "start", a.dto or "end"))
    print("  " + "=" * 66)
    print("  %-22s %14s %14s" % ("", "TradingView", "Python (live)"))
    for k, lab, d in (("n", "Trades", 0), ("net", "Net (points)", 1), ("win", "Win %", 1),
                      ("pf", "Profit factor", 2), ("sharpe", "Sharpe/trade", 2),
                      ("maxdd", "Max DD (points)", 1), ("avg", "Avg/trade (pts)", 1)):
        print("  %-22s %14s %14s" % (lab, f"{st_tv[k]:,.{d}f}", f"{st_en[k]:,.{d}f}"))
    print()
    print("  Entry match (same side, +-1 bar): %d/%d = %.1f%%"
          % (matched, len(tv), 100.0 * matched / len(tv) if tv else 0))
    print("  TV-only: %d   |   Engine-only: %d" % (len(tv) - matched, len(extra)))

    bars = cont5
    if a.dfrom:
        bars = bars[bars["date"].astype(str) >= a.dfrom]
    if a.dto:
        bars = bars[bars["date"].astype(str) <= a.dto]

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(root, "ACCURACY SCORE CLAUD")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "live_vs_tv.html")
    html(tv, eng, pairs, extra, bars, out)
    print()
    print("  Chart: %s" % out)
    print()


if __name__ == "__main__":
    main()
