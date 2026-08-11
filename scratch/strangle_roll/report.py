"""report.py — render full_results.json into a self-contained HTML report."""
import json, sys, os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
IN = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "full_results.json")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "strangle_report.html")

data = json.load(open(IN))
rows_by = data["rows"]


def stats(rows):
    if not rows:
        return dict(n=0, net=0, avg=0, win=0, pf=0, dd=0, best=0, worst=0, rolls=0)
    net = [r["net"] for r in rows]
    tot = sum(net); n = len(net)
    wins = [x for x in net if x > 0]
    gp = sum(wins); gl = -sum(x for x in net if x < 0)
    eq = 0; peak = 0; dd = 0
    for x in net:
        eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
    return dict(n=n, net=round(tot), avg=round(tot / n, 1),
                win=round(100 * len(wins) / n, 1),
                pf=round(gp / gl, 2) if gl else 99.0,
                dd=round(dd), best=round(max(net)), worst=round(min(net)),
                rolls=round(sum(r["rolls"] for r in rows) / n, 2))


def yearly(rows):
    y = defaultdict(list)
    for r in rows:
        y[r["date"][:4]].append(r["net"])
    out = {}
    for k, v in sorted(y.items()):
        wins = len([x for x in v if x > 0])
        out[k] = dict(net=round(sum(v)), n=len(v), win=round(100 * wins / len(v)))
    return out


def weekly_filter(rows):
    """one entry per ISO week (reduce positional overlap)."""
    import datetime
    seen = set(); out = []
    for r in sorted(rows, key=lambda x: x["date"]):
        d = datetime.date.fromisoformat(r["date"])
        wk = (d.isocalendar().year, d.isocalendar().week)
        if wk in seen:
            continue
        seen.add(wk); out.append(r)
    return out


def equity_svg(rows, w=680, h=140):
    if not rows:
        return ""
    net = [r["net"] for r in sorted(rows, key=lambda x: x["date"])]
    eq = []; s = 0
    for x in net:
        s += x; eq.append(s)
    lo, hi = min(eq + [0]), max(eq + [0])
    rng = (hi - lo) or 1
    pts = []
    for i, y in enumerate(eq):
        px = w * i / (len(eq) - 1 if len(eq) > 1 else 1)
        py = h - (y - lo) / rng * (h - 10) - 5
        pts.append(f"{px:.1f},{py:.1f}")
    zy = h - (0 - lo) / rng * (h - 10) - 5
    col = "#3fb950" if eq[-1] >= 0 else "#f85149"
    return (f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px">'
            f'<line x1="0" y1="{zy:.1f}" x2="{w}" y2="{zy:.1f}" stroke="#30363d" stroke-dasharray="3"/>'
            f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" stroke-width="1.6"/>'
            f'</svg>')


# order variants nicely
order = list(rows_by.keys())
intraday = [k for k in order if k.startswith("intraday")]
positional = [k for k in order if k.startswith("positional")]

def fmt(n):
    return f"{n:,.0f}"

def row_html(label, s, hl=False):
    netc = "#3fb950" if s["net"] >= 0 else "#f85149"
    cls = ' style="background:#132a1a"' if hl else ""
    return (f"<tr{cls}><td class=l>{label}</td><td>{s['n']}</td>"
            f"<td style='color:{netc};font-weight:600'>{fmt(s['net'])}</td>"
            f"<td>{fmt(s['avg'])}</td><td>{s['win']}%</td><td>{s['pf']}</td>"
            f"<td style='color:#f85149'>{fmt(s['dd'])}</td>"
            f"<td>{fmt(s['best'])}</td><td>{fmt(s['worst'])}</td><td>{s['rolls']}</td></tr>")

def block(title, keys, use_weekly=False):
    html = [f"<h2>{title}</h2>"]
    html.append("<table><thead><tr><th class=l>Variant</th><th>Trades</th><th>Net ₹</th>"
                "<th>Avg/trade</th><th>Win%</th><th>PF</th><th>MaxDD ₹</th>"
                "<th>Best</th><th>Worst</th><th>Rolls</th></tr></thead><tbody>")
    best_net = max((stats(weekly_filter(rows_by[k]) if use_weekly else rows_by[k])["net"] for k in keys), default=0)
    for k in keys:
        rr = weekly_filter(rows_by[k]) if use_weekly else rows_by[k]
        s = stats(rr)
        html.append(row_html(k.split("|", 1)[1].strip(), s, hl=(s["net"] == best_net)))
    html.append("</tbody></table>")
    return "\n".join(html)

def equity_block(title, keys):
    html = [f"<h3>{title} — equity curves (1 lot, cumulative ₹)</h3><div class=grid>"]
    for k in keys:
        s = stats(rows_by[k])
        html.append(f"<div class=card><div class=cap>{k.split('|',1)[1].strip()} "
                    f"<span style='color:{'#3fb950' if s['net']>=0 else '#f85149'}'>₹{fmt(s['net'])}</span></div>"
                    f"{equity_svg(rows_by[k])}</div>")
    html.append("</div>")
    return "\n".join(html)

def yearly_block(keys):
    years = sorted({y for k in keys for y in yearly(rows_by[k])})
    html = ["<h3>Yearly net ₹ (1 lot)</h3><table><thead><tr><th class=l>Variant</th>"
            + "".join(f"<th>{y}</th>" for y in years) + "</tr></thead><tbody>"]
    for k in keys:
        yb = yearly(rows_by[k])
        cells = []
        for y in years:
            if y in yb:
                v = yb[y]["net"]; c = "#3fb950" if v >= 0 else "#f85149"
                cells.append(f"<td style='color:{c}'>{fmt(v)}<br><span class=sub>{yb[y]['win']}% · {yb[y]['n']}</span></td>")
            else:
                cells.append("<td>–</td>")
        html.append(f"<tr><td class=l>{k.split('|',1)[1].strip()}</td>" + "".join(cells) + "</tr>")
    html.append("</tbody></table>")
    return "\n".join(html)


HTML = f"""<!doctype html><html><head><meta charset=utf-8><title>Strangle Roll Backtest</title>
<style>
body{{background:#0d1117;color:#e6edf3;font:13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px;max-width:1100px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:26px 0 8px;color:#58a6ff}}
h3{{font-size:13px;margin:18px 0 8px;color:#8b949e;font-weight:600}}
.sub{{color:#6e7681;font-size:10px}}
table{{border-collapse:collapse;width:100%;margin:6px 0;font-size:12px}}
th,td{{border:1px solid #21262d;padding:5px 8px;text-align:right}}
th{{background:#161b22;color:#8b949e;font-weight:600}}
td.l,th.l{{text-align:left}}
.note{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;margin:14px 0;color:#c9d1d9}}
.note b{{color:#e6edf3}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px}}
.cap{{font-size:11px;color:#8b949e;margin-bottom:4px}}
.tag{{display:inline-block;background:#21262d;border-radius:5px;padding:2px 8px;font-size:11px;color:#8b949e;margin-right:6px}}
</style></head><body>
<h1>9:20 NIFTY Short-Strangle + Roll-Away — Backtest</h1>
<div class=sub>Real 1-min premium lake · {data['summary'][0].get('n','?')} entry-days · 2021-07 → 2026-07 · 1 lot (65) · real date-aware Zerodha charges</div>

<div class=note>
<span class=tag>Entry</span> 09:20, sell CE @ spot+250, PE @ spot−250 (naked)
<span class=tag>Roll</span> spot within trigger of a leg → close it, resell 250 from spot
<span class=tag>Exit</span> P&amp;L ≥ 50% of entry credit, else 15:10 (intraday) / weekly expiry (positional)<br><br>
<b>How to read:</b> Net/DD/Best/Worst are ₹ on <b>1 lot</b>. "Rolls" = avg adjustments/trade.
<b>BASELINE no-roll</b> = same strangle, adjustment off (so you can see if rolling actually helps).
</div>

{block("INTRADAY — 9:20 entry, 15:10 forced exit", intraday)}
{yearly_block(intraday)}
{equity_block("Intraday", intraday)}

{block("POSITIONAL — hold to weekly expiry (every-day entry, OVERLAPPING)", positional)}
<div class=note><b>⚠️ Overlap caveat:</b> "every trading day" entry means many strangles run at once, so the
positional Net below is a <b>sum of overlapping trades</b> and the Dp/equity overstates a single-lot account.
The <b>weekly view</b> next (one entry per week, non-overlapping) is the honest single-position picture.</div>
{yearly_block(positional)}
{equity_block("Positional (overlapping)", positional)}

{block("POSITIONAL — 1 entry / week (non-overlapping, honest single-lot)", positional, use_weekly=True)}

</body></html>"""

open(OUT, "w", encoding="utf-8").write(HTML)
print("wrote", OUT)
