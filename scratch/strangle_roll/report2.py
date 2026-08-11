"""report2.py — hedge + significance report from hedge_results.json."""
import json, os, datetime
from collections import defaultdict
HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "hedge_results.json")))
rows_by = d["rows"]; sig = d["sig"]


def stats(rows):
    if not rows:
        return dict(n=0, net=0, avg=0, win=0, pf=0, dd=0, best=0, worst=0, rolls=0)
    net = [r["net"] for r in rows]; tot = sum(net); n = len(net)
    gp = sum(x for x in net if x > 0); gl = -sum(x for x in net if x < 0)
    eq = 0; pk = 0; dd = 0
    for x in net:
        eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
    return dict(n=n, net=round(tot), avg=round(tot / n, 1), win=round(100 * len([x for x in net if x > 0]) / n, 1),
                pf=round(gp / gl, 2) if gl else 99, dd=round(dd), best=round(max(net)), worst=round(min(net)),
                rolls=round(sum(r["rolls"] for r in rows) / n, 2))


def eqsvg(rows, w=640, h=120):
    if not rows:
        return ""
    net = [r["net"] for r in sorted(rows, key=lambda x: x["date"])]
    eq = []; s = 0
    for x in net:
        s += x; eq.append(s)
    lo, hi = min(eq + [0]), max(eq + [0]); rng = (hi - lo) or 1
    pts = " ".join(f"{w*i/(len(eq)-1 if len(eq)>1 else 1):.1f},{h-(y-lo)/rng*(h-10)-5:.1f}" for i, y in enumerate(eq))
    zy = h - (0 - lo) / rng * (h - 10) - 5
    col = "#3fb950" if eq[-1] >= 0 else "#f85149"
    return (f'<svg viewBox="0 0 {w} {h}" style="width:100%;height:{h}px">'
            f'<line x1=0 y1={zy:.1f} x2={w} y2={zy:.1f} stroke=#30363d stroke-dasharray=3 />'
            f'<polyline points="{pts}" fill=none stroke="{col}" stroke-width=1.6 /></svg>')


def fmt(n):
    return f"{n:,.0f}"


def rowh(label, s):
    nc = "#3fb950" if s["net"] >= 0 else "#f85149"
    return (f"<tr><td class=l>{label}</td><td>{s['n']}</td>"
            f"<td style='color:{nc};font-weight:600'>{fmt(s['net'])}</td><td>{fmt(s['avg'])}</td>"
            f"<td>{s['win']}%</td><td>{s['pf']}</td><td style='color:#f85149'>{fmt(s['dd'])}</td>"
            f"<td>{fmt(s['worst'])}</td><td>{s['rolls']}</td></tr>")


def table(keys):
    h = ["<table><thead><tr><th class=l>Variant</th><th>Trades</th><th>Net ₹</th><th>Avg/trade</th>"
         "<th>Win%</th><th>PF</th><th>MaxDD ₹</th><th>Worst</th><th>Rolls</th></tr></thead><tbody>"]
    for k in keys:
        h.append(rowh(k.split("|", 1)[1].strip(), stats(rows_by[k])))
    h.append("</tbody></table>")
    return "\n".join(h)


intr = [k for k in rows_by if k.startswith("intraday")]
pos_naked = [k for k in rows_by if k.startswith("positional") and k.endswith("wing0")]
pos_hedge = [k for k in rows_by if k.startswith("positional") and k.endswith("wing250")]

# significance table
sigrows = []
for k, z in sig.items():
    g = "PASS" if z["gate_pass"] else "fail"
    gc = "#3fb950" if z["gate_pass"] else "#f85149"
    sigrows.append(
        f"<tr><td class=l>{k.split('|',1)[1].strip()}</td>"
        f"<td>{fmt(z['train']['avg'])}<br><span class=sub>n={z['train']['n']}</span></td>"
        f"<td>{fmt(z['oos']['avg'])}<br><span class=sub>n={z['oos']['n']}</span></td>"
        f"<td>{z['p_full']}</td><td>{z['sharpe_ann']}</td>"
        f"<td style='color:{gc};font-weight:700'>{g}</td></tr>")

# equity cards for the two headline hedged configs
cards = ""
for k in ["positional | rec t100 | wing250", "positional | thr t100 | wing250",
          "positional | baseline | wing250", "positional | thr t100 | wing0"]:
    s = stats(rows_by[k])
    cards += (f"<div class=card><div class=cap>{k.split('|',1)[1].strip()} "
              f"<b style='color:{'#3fb950' if s['net']>=0 else '#f85149'}'>₹{fmt(s['net'])}</b> "
              f"· PF {s['pf']} · DD {fmt(s['dd'])}</div>{eqsvg(rows_by[k])}</div>")

HTML = f"""<!doctype html><html><head><meta charset=utf-8><title>Strangle Hedge + Significance</title>
<style>
body{{background:#0d1117;color:#e6edf3;font:13px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;padding:24px;max-width:1080px}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:15px;margin:24px 0 8px;color:#58a6ff}}
.sub{{color:#6e7681;font-size:10px}}
table{{border-collapse:collapse;width:100%;margin:6px 0;font-size:12px}}
th,td{{border:1px solid #21262d;padding:5px 8px;text-align:right}}
th{{background:#161b22;color:#8b949e}} td.l,th.l{{text-align:left}}
.note{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;margin:14px 0}}
.win{{background:#0f2a17;border-color:#238636}}
.tag{{display:inline-block;background:#21262d;border-radius:5px;padding:2px 8px;font-size:11px;color:#8b949e;margin-right:6px}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.card{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px}}
.cap{{font-size:11px;color:#8b949e;margin-bottom:4px}} b{{color:#e6edf3}}
</style></head><body>
<h1>9:20 Strangle + Roll + Cheap Hedge — Hedge & Significance</h1>
<div class=sub>Real 1-min premium lake · 2021-07 → 2026-07 · 1 lot (65) · real date-aware Zerodha charges · positional = SEQUENTIAL (no re-entry until exit)</div>

<div class="note win">
<b>Verdict:</b> Hedge intraday ko maar deta hai (8 orders ki charges), par <b>positional pe hedge = jeet</b> —
per-lot edge lagbhag same rehta hai magar drawdown −139k → −17k tak gir jaata hai (defined risk).
<b>Deploy-gate PASS (Sharpe≥1 + p&lt;0.05 + train&amp;OOS dono positive):</b>
<b>positional · recenter · trig100 · hedge(spot±500)</b> → Sharpe 2.10, PF 2.26, DD −17k, p=0.000.
Robust pick (kam rolls): <b>threatened · trig100 · hedge</b> → Sharpe 1.02, DD −22k.
</div>

<h2>INTRADAY (9:20 → 15:10) — hedge kills it</h2>
{table(intr)}
<div class=note>Intraday hedged = 8 orders/round-trip; ek din ki theta 4 extra legs ki charges cover nahi karti →
avg −₹280 se −₹480. <b>Hedge sirf positional ke liye hai</b> (multi-day theta &gt;&gt; extra charges).</div>

<h2>POSITIONAL — naked (wing0)</h2>
{table(pos_naked)}
<h2>POSITIONAL — hedged (wing250 = buy spot±500, cheapest in lake)</h2>
{table(pos_hedge)}

<h2>Significance — train (&lt;2025-01) vs OOS (≥2025-01), bootstrap p</h2>
<table><thead><tr><th class=l>Variant</th><th>Train avg ₹</th><th>OOS avg ₹</th><th>p (full)</th><th>Sharpe (ann)</th><th>Deploy gate</th></tr></thead>
<tbody>{''.join(sigrows)}</tbody></table>

<h2>Equity curves (1 lot, cumulative ₹)</h2>
<div class=grid>{cards}</div>

<div class=note>
<b>Tera asli maqsad — margin/lot-size:</b> naked strangle margin ≈ ₹1.1–1.5L/lot; ye 250-wide hedged (iron)
strangle <b>defined-risk</b> hai → margin ≈ ₹30–45k/lot. Yaani same capital me <b>~3–4× zyada lots</b>.
Per-lot edge hedge ke saath bhi ₹430–610 hai, to <b>return-on-margin naked se kai guna behtar</b> —
bilkul jaisa tune socha tha. (SPAN margin lake se exact nahi nikal sakta; ye industry-standard range hai.)
</div>

<div class=note>
<b>⚠️ Honest flags:</b>
(1) OOS &gt; train — edge 2025-26 ke high-premium regime me zyada; regime-dependent ho sakta hai, pure skill nahi.
(2) recenter = 3.4 rolls/trade → live slippage exposure zyada (yaha sirf charges modeled, extra slippage nahi);
threatened (1.3 rolls) live me zyada robust.
(3) hedge spot±500 = lake ka kinara (ATM±10) → kabhi-kabhi data-gap, honestly handle kiya.
(4) Sample non-overlap ke baad ~470/config — chhota par significant.
</div>
</body></html>"""
open(os.path.join(HERE, "strangle_hedge_report.html"), "w", encoding="utf-8").write(HTML)
print("wrote strangle_hedge_report.html")
