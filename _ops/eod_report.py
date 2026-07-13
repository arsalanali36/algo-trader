#!/usr/bin/env python3
"""
eod_report.py — EK LINK me poora din: "aaj algo ki health kaisi thi?"

Har trading day ke close ke baad ek self-contained HTML report banata hai:
  - Overall health banner (RED/YELLOW/GREEN) + scoreboard (P&L, trades, entries)
  - P&L bar chart (inline SVG, koi external lib nahi)
  - Summary table: har strategy ka status/mode/entries/trades/P&L/win% + backtest
    expectation se comparison + replay (TRAP #108) verdict
  - Positives / Negatives — auto-generated bullets, ek nazar me kya achha kya toota
  - Per-strategy detail: entries/skips/exits/errors + replay signal table + trades

Data source = eod_digest (log+order_store analysis) + signal_replay.run_for
(TRAP #108 drift check) — wahi engines, teesri copy nahi (Rule 6B).
Strategy coverage = auto-discovered (current + future sab, koi hardcoded list nahi).

Output:  data/reports/eod_<date>.html  +  data/reports/index.json (date list)
Dashboard: /reports (list) -> /reports/<date> — login-gated.

Usage:
    python -X utf8 _ops/eod_report.py                 # today
    python -X utf8 _ops/eod_report.py --date 2026-07-13
    python -X utf8 _ops/eod_report.py --no-replay     # sirf digest (fast)

Systemd: _DEPLOY/algo-eodreport.timer (Mon..Fri 15:45 IST) — USI SHAAM zaroori
(Dhan intraday history chhoti hai, replay baad me nahi ho sakta).
"""
import argparse
import html as html_mod
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
import _paths  # noqa: F401
import eod_digest as dg
import signal_replay as sr
try:
    import strategy_registry as _sr_reg  # canonical NN.MM - Name labels
except Exception:
    _sr_reg = None


def _sid_id(sid):
    """Compact registry ID (NN.MM) for a strategy sid, else the raw sid."""
    try:
        return _sr_reg.resolve(sid) or sid
    except Exception:
        return sid


def _sid_lbl(sid):
    """'NN.MM - Name' for a strategy sid, else the raw sid."""
    try:
        return _sr_reg.label(sid)
    except Exception:
        return sid

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

REPORTS_DIR = BASE_DIR / "data" / "reports"


def esc(x):
    return html_mod.escape(str(x))


def ist_today():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


# ──────────────────────────────────────────────────────────────────────────
#  Collect
# ──────────────────────────────────────────────────────────────────────────
def collect(date, do_replay=True):
    logs_dir = BASE_DIR / "logs"
    ids = dg.discover_ids(date, logs_dir)
    expectations = dg.load_expectations()
    parsed = {sid: dg.parse_log(sid, date, logs_dir) for sid in ids}
    market_day = any(p["lines"] > p["closed_loops"] for p in parsed.values())

    rows = []
    for sid in ids:
        lg = parsed[sid]
        st = dg.store_section(sid, date)
        mode_exp = dg.expected_mode_for(sid)
        colour, reasons = dg.judge(sid, lg, st, mode_exp, market_day,
                                   date, dg.mode_is_explicit(sid))
        # GREY (chali hi nahi) strategy pe replay chalane ka koi matlab nahi
        rp = (sr.run_for(sid, date) if (do_replay and colour != "GREY")
              else dict(status="skip", note="chali hi nahi / replay skipped",
                        signals=[], extra=[], miss=0, match=0, gated=0, err=0, entries=0))
        wins = sum(1 for tr in st["completed"] if (tr.get("pnl") or 0) > 0)
        rows.append(dict(sid=sid, lg=lg, st=st, colour=colour, reasons=reasons,
                         mode_exp=mode_exp, replay=rp, wins=wins,
                         exp=expectations.get(sid) or {}))
    return dict(date=date, rows=rows, market_day=market_day)


# ──────────────────────────────────────────────────────────────────────────
#  Positives / Negatives (auto)
# ──────────────────────────────────────────────────────────────────────────
def pos_neg(data):
    rows = data["rows"]
    pos, neg = [], []
    grey = [r for r in rows if r["colour"] == "GREY"]           # chali hi nahi — count se bahar
    active = [r for r in rows if r["colour"] != "GREY"]         # jo relevant hain
    ran = [r for r in rows if r["lg"]["exists"] and r["lg"]["lines"]]
    clean = [r for r in rows if r["colour"] == "GREEN"]
    reds = [r for r in rows if r["colour"] == "RED"]

    if ran:
        pos.append(f"{len(ran)}/{len(active)} chalti strategies ka process din bhar log karta raha")
    if grey:
        pos.append(f"{len(grey)} strategy is date ko chali hi nahi (deploy baad me / inactive) — "
                   f"ye ERROR nahi: {', '.join(r['sid'] for r in grey[:6])}"
                   + (" …" if len(grey) > 6 else ""))
    if clean:
        pos.append(f"{len(clean)} strategy bilkul clean (GREEN) — koi issue nahi")
    if not any(r["st"]["zero_price"] for r in rows):
        pos.append("koi ₹0-price phantom fill nahi (TRAP #1 clear)")
    if not any(r["st"]["open_rows"] for r in rows):
        pos.append("EOD pe sab flat — koi position store me OPEN nahi bachi (TRAP #28/#36 clear)")
    rep_ok = [r for r in rows if r["replay"]["status"] == "ok"
              and not (r["replay"]["miss"] or r["replay"]["extra"] or r["replay"]["err"])]
    if rep_ok:
        pos.append(f"replay: {len(rep_ok)} strategy me live loop == signal code (TRAP #108 clear)")
    tot = sum(r["st"]["pnl"] for r in rows)
    n_tr = sum(len(r["st"]["completed"]) for r in rows)
    if n_tr and tot >= 0:
        pos.append(f"din ka net P&L positive: ₹{tot:+,.0f} ({n_tr} trades)")

    for r in reds:
        for reason in r["reasons"][:2]:
            neg.append(f"{r['sid']}: {reason}")
    for r in rows:
        if r["colour"] == "GREY":
            continue                              # chali hi nahi — replay bhi meaningless
        rp = r["replay"]
        if rp["miss"] or rp["extra"] or rp["err"]:
            neg.append(f"{r['sid']}: replay drift — {rp['miss']} MISS, "
                       f"{len(rp['extra'])} EXTRA, {rp['err']} ERROR (TRAP #108!)")
    for r in rows:
        if r["colour"] == "YELLOW":
            neg.append(f"{r['sid']} (yellow): {'; '.join(r['reasons'][:2])}")
    for r in rows:
        exp_t = (r["exp"] or {}).get("trades_per_day")
        got = len(r["st"]["completed"])
        if exp_t and got > max(3 * exp_t, exp_t + 2):
            neg.append(f"{r['sid']}: trades {got} vs expected ~{exp_t}/day — overtrading? dekho")
    if n_tr and tot < 0:
        neg.append(f"din ka net P&L negative: ₹{tot:+,.0f} — trades detail me dekho kaunsa duba")
    if not neg:
        neg.append("kuch bhi nahi toota — sab checks pass 🎉")
    return pos, neg


# ──────────────────────────────────────────────────────────────────────────
#  SVG bar chart (self-contained, no libs)
# ──────────────────────────────────────────────────────────────────────────
def pnl_bar_svg(rows):
    rows = [r for r in rows if r["st"]["completed"] or r["st"]["pnl"]]
    if not rows:
        return "<p class='dim'>aaj koi completed trade nahi — chart ke liye kuch nahi</p>"
    vals = [r["st"]["pnl"] for r in rows]
    mx = max(max(abs(v) for v in vals), 1)
    bar_h, gap, label_w, chart_w = 26, 8, 130, 420
    H = len(rows) * (bar_h + gap) + 10
    zero_x = label_w + chart_w / 2
    parts = [f"<svg viewBox='0 0 {label_w + chart_w + 110} {H}' style='max-width:680px;width:100%'>"]
    parts.append(f"<line x1='{zero_x}' y1='0' x2='{zero_x}' y2='{H}' stroke='#30363d'/>")
    for i, r in enumerate(rows):
        y = i * (bar_h + gap) + 5
        v = r["st"]["pnl"]
        w = abs(v) / mx * (chart_w / 2 - 4)
        x = zero_x - w if v < 0 else zero_x
        col = "#f85149" if v < 0 else "#3fb950"
        parts.append(f"<text x='{label_w - 8}' y='{y + bar_h * 0.7}' text-anchor='end' "
                     f"fill='#e6edf3' font-size='13'>{esc(_sid_id(r['sid']))}</text>")
        parts.append(f"<rect x='{x:.1f}' y='{y}' width='{max(w, 1):.1f}' height='{bar_h}' "
                     f"rx='4' fill='{col}' opacity='0.85'/>")
        tx = zero_x + w + 8 if v >= 0 else zero_x - w - 8
        anch = "start" if v >= 0 else "end"
        parts.append(f"<text x='{tx:.1f}' y='{y + bar_h * 0.7}' text-anchor='{anch}' "
                     f"fill='{col}' font-size='12' font-weight='600'>₹{v:+,.0f}</text>")
    parts.append("</svg>")
    return "".join(parts)


# ──────────────────────────────────────────────────────────────────────────
#  HTML render
# ──────────────────────────────────────────────────────────────────────────
CSS = """
body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;
     margin:0;padding:18px;max-width:1080px;margin:auto}
h1{font-size:22px;margin:8px 0} h2{font-size:16px;margin:22px 0 8px;color:#adbac7}
.banner{padding:14px 18px;border-radius:10px;font-size:16px;font-weight:600;margin:12px 0}
.b-green{background:#0f2e1b;border:1px solid #238636;color:#3fb950}
.b-yellow{background:#2e2a0f;border:1px solid #9e6a03;color:#d29922}
.b-red{background:#2e0f12;border:1px solid #da3633;color:#f85149}
.score{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.pill{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:10px 16px;min-width:110px}
.pill .v{font-size:19px;font-weight:700}.pill .k{font-size:11px;color:#8b949e}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
th{background:#161b22;color:#8b949e;text-align:left;padding:7px 9px;font-size:11px;
   text-transform:uppercase;letter-spacing:.4px}
td{padding:7px 9px;border-top:1px solid #21262d}
tr:hover td{background:#161b22}
.g{color:#3fb950}.y{color:#d29922}.r{color:#f85149}.dim{color:#8b949e}
.tag{display:inline-block;padding:1px 8px;border-radius:20px;font-size:11px;font-weight:600}
.t-g{background:#0f2e1b;color:#3fb950}.t-y{background:#2e2a0f;color:#d29922}
.t-r{background:#2e0f12;color:#f85149}.t-d{background:#21262d;color:#8b949e}
ul{margin:6px 0;padding-left:22px}li{margin:4px 0;font-size:14px}
details{background:#161b22;border:1px solid #30363d;border-radius:10px;
        padding:10px 14px;margin:8px 0}
summary{cursor:pointer;font-weight:600;font-size:14px}
.mono{font-family:Consolas,monospace;font-size:12px}
.note{font-size:12px;color:#8b949e;margin-top:18px;border-top:1px solid #21262d;padding-top:10px}
.overflow{overflow-x:auto}
"""


def render(data):
    rows, date = data["rows"], data["date"]
    reds = sum(1 for r in rows if r["colour"] == "RED")
    yels = sum(1 for r in rows if r["colour"] == "YELLOW")
    greens = sum(1 for r in rows if r["colour"] == "GREEN")
    greys = sum(1 for r in rows if r["colour"] == "GREY")
    active_n = len(rows) - greys                 # jo is date ko chali (relevant)
    tot = sum(r["st"]["pnl"] for r in rows)
    n_tr = sum(len(r["st"]["completed"]) for r in rows)
    n_ent = sum(len(r["lg"]["entries"]) for r in rows)
    pos, neg = pos_neg(data)
    grey_note = f" · {greys} chali nahi (ignore)" if greys else ""

    if reds:
        banner = f"<div class='banner b-red'>🔴 {reds} strategy RED — neeche Negatives + detail dekho{grey_note}</div>"
    elif yels:
        banner = f"<div class='banner b-yellow'>🟡 sab critical clear, {yels} yellow (minor) — ek nazar maar lo{grey_note}</div>"
    else:
        banner = f"<div class='banner b-green'>🟢 ALL CLEAR — jo strategies chali, sab healthy{grey_note}</div>"

    tone = "g" if tot >= 0 else "r"
    score = f"""<div class='score'>
      <div class='pill'><div class='v {tone}'>₹{tot:+,.0f}</div><div class='k'>Net P&L (paper=gross)</div></div>
      <div class='pill'><div class='v'>{n_tr}</div><div class='k'>Completed trades</div></div>
      <div class='pill'><div class='v'>{n_ent}</div><div class='k'>Entries</div></div>
      <div class='pill'><div class='v'>{active_n}<span class='dim' style='font-size:12px'> / {len(rows)}</span></div><div class='k'>Chali / Total</div></div>
      <div class='pill'><div class='v'><span class='r'>{reds}</span> / <span class='y'>{yels}</span> / <span class='g'>{greens}</span></div><div class='k'>Red / Yellow / Green</div></div>
    </div>"""

    def tagc(c):
        return {"GREEN": "t-g", "YELLOW": "t-y", "RED": "t-r", "GREY": "t-d"}[c]

    def replay_cell(rp):
        if rp["status"] == "skip":
            return f"<span class='tag t-d' title='{esc(rp['note'])}'>N/A</span>"
        if rp["status"] == "fail":
            return f"<span class='tag t-y' title='{esc(rp['note'])}'>no-data</span>"
        if rp["miss"] or rp["extra"] or rp["err"]:
            return (f"<span class='tag t-r'>DRIFT: {rp['miss']}M/{len(rp['extra'])}E</span>")
        return f"<span class='tag t-g'>{rp['match']} match, {rp['gated']} gated ✓</span>"

    trs = []
    for r in rows:
        st, lg, exp = r["st"], r["lg"], r["exp"]
        got_t = len(st["completed"])
        wr = f"{100 * r['wins'] / got_t:.0f}%" if got_t else "—"
        exp_txt = (f"{exp.get('trades_per_day', '—')}/day, win {exp.get('win_rate', '—')}%"
                   if exp else "—")
        pnl_cls = "g" if st["pnl"] >= 0 else "r"
        trs.append(f"""<tr>
          <td><b title="{esc(r['sid'])}">{esc(_sid_lbl(r['sid']))}</b></td>
          <td><span class='tag {tagc(r['colour'])}'>{r['colour']}</span></td>
          <td>{esc(lg['mode_banner'] or '—')}<span class='dim'>/{esc(r['mode_exp'])}</span></td>
          <td>{len(lg['entries'])}</td><td>{got_t}</td>
          <td class='{pnl_cls}'>₹{st['pnl']:+,.0f}</td><td>{wr}</td>
          <td class='dim'>{esc(exp_txt)}</td>
          <td>{replay_cell(r['replay'])}</td>
          <td>{len(r['reasons'])}</td></tr>""")

    details = []
    grey_sids = [r["sid"] for r in rows if r["colour"] == "GREY"]
    for r in rows:
        if r["colour"] == "GREY":
            continue                              # chali hi nahi — detail dikhane ka matlab nahi
        st, lg, rp = r["st"], r["lg"], r["replay"]
        body = []
        if r["reasons"]:
            body.append("<ul>" + "".join(
                f"<li class='{'r' if r['colour'] == 'RED' and i < 3 else 'y'}'>⚠ {esc(x)}</li>"
                for i, x in enumerate(r["reasons"])) + "</ul>")
        sig_txt = ", ".join(f"{esc(k)}×{v}" for k, v in lg["signals"].items()) or "none"
        body.append(f"<p class='dim'>log {esc(lg['first'] or '--')}→{esc(lg['last'] or '--')}"
                    f" ({lg['lines']} lines) | signals: {sig_txt} | skips {len(lg['skips'])}"
                    f" | exits {len(lg['exits'])} | err/warn {len(lg['errors'])}/{lg['warnings']}</p>")
        for t, e in lg["entries"]:
            body.append(f"<div class='mono'>★ {esc(t)} {esc(e[:110])}</div>")
        for t, x in lg["exits"]:
            body.append(f"<div class='mono'>⏹ {esc(t)} {esc(x[:110])}</div>")
        for t, s in lg["skips"][:8]:
            body.append(f"<div class='mono dim'>⤳ {esc(t)} {esc(s[:100])}</div>")
        for e in lg["errors"][:4]:
            body.append(f"<div class='mono r'>✗ {esc(e)}</div>")
        if st["completed"]:
            body.append("<div class='overflow'><table><tr><th>Contract</th><th>In→Out</th>"
                        "<th>Price</th><th>P&L</th><th>Reason</th></tr>")
            for tr in st["completed"]:
                c = "g" if (tr.get("pnl") or 0) >= 0 else "r"
                body.append(f"<tr><td>{esc(tr['sym'])}</td>"
                            f"<td>{esc(tr['entry_time'])}→{esc(tr['exit_time'])}</td>"
                            f"<td>{tr['entry_price']:.2f}→{tr['exit_price']:.2f}</td>"
                            f"<td class='{c}'>₹{tr['pnl']:+,.0f}</td>"
                            f"<td class='dim'>{esc(tr.get('exit_reason') or '?')}</td></tr>")
            body.append("</table></div>")
        if rp["signals"] or rp["extra"]:
            body.append("<p><b>Replay (TRAP #108):</b></p>")
            for s in rp["signals"]:
                t = s["time"].strftime("%H:%M") if s["time"] is not None else "--:--"
                bad = str(s["verdict"]).startswith(("MISS", "ERROR"))
                cls = "r" if bad else "g"
                spot = f" spot={s['spot']:.1f}" if s.get("spot") else ""
                body.append(f"<div class='mono {cls}'>{'🔴' if bad else '🟢'} {t} "
                            f"{esc(str(s['dir']).upper())}{esc(spot)} — {esc(s['verdict'])}</div>")
            for e in rp["extra"]:
                body.append(f"<div class='mono r'>🔴 {esc(e['time'][:5])} EXTRA — "
                            f"live entry, offline signal nahi</div>")
        elif rp["status"] != "ok":
            body.append(f"<p class='dim'>Replay: {esc(rp['note'])}</p>")
        icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴", "GREY": "⚪"}[r["colour"]]
        details.append(f"<details {'open' if r['colour'] == 'RED' else ''}>"
                       f"<summary>{icon} {esc(_sid_lbl(r['sid']))} — ₹{st['pnl']:+,.0f}, "
                       f"{len(st['completed'])} trades</summary>{''.join(body)}</details>")

    gen = (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d %H:%M IST")
    return f"""<!DOCTYPE html><html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>EOD Report {esc(date)}</title><style>{CSS}</style></head><body>
<h1>📋 EOD Report — {esc(date)}</h1>
{banner}{score}
<h2>P&L by strategy</h2>{pnl_bar_svg(rows)}
<h2>Summary</h2><div class='overflow'><table>
<tr><th>Strategy</th><th>Status</th><th>Mode got/exp</th><th>Entries</th><th>Trades</th>
<th>P&L</th><th>Win%</th><th>Backtest exp</th><th>Replay #108</th><th>Issues</th></tr>
{''.join(trs)}</table></div>
<h2 class='g'>✅ Positives</h2><ul>{''.join(f'<li>{esc(p)}</li>' for p in pos)}</ul>
<h2 class='r'>❌ Negatives / dhyaan do</h2><ul>{''.join(f'<li>{esc(n)}</li>' for n in neg)}</ul>
<h2>Per-strategy detail <span class='dim' style='font-size:12px;font-weight:400'>(sirf jo chali)</span></h2>{''.join(details)}
{("<p class='dim' style='font-size:12px'>⚪ Is date ko chali hi nahi (deploy baad me / inactive, koi issue nahi): "
  + esc(', '.join(grey_sids)) + "</p>") if grey_sids else ""}
<div class='note'>paper fill = LTP (na slippage na charges) — backtest me costs the, isliye paper
P&L thoda optimistic dikhega. ⚪ = strategy us din chali hi nahi (error nahi).
| Generated {gen} | eod_report.py (digest + signal_replay engines)</div>
</body></html>"""


# ──────────────────────────────────────────────────────────────────────────
def update_index(date):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    idx_f = REPORTS_DIR / "index.json"
    try:
        idx = json.loads(idx_f.read_text(encoding="utf-8"))
    except Exception:
        idx = []
    if date not in idx:
        idx.append(date)
    idx = sorted(set(idx), reverse=True)
    idx_f.write_text(json.dumps(idx, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="EOD one-link HTML report")
    ap.add_argument("--date", default=ist_today())
    ap.add_argument("--no-replay", action="store_true", help="sirf digest (fast, no Dhan calls)")
    args = ap.parse_args()

    print(f"[eod_report] collecting {args.date} (replay={'off' if args.no_replay else 'on'})...")
    data = collect(args.date, do_replay=not args.no_replay)
    if not data["rows"]:
        print("[eod_report] koi strategy nahi mili — report skip"); sys.exit(0)
    html = render(data)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"eod_{args.date}.html"
    out.write_text(html, encoding="utf-8")
    update_index(args.date)
    reds = sum(1 for r in data["rows"] if r["colour"] == "RED")
    print(f"[eod_report] written {out}  ({len(data['rows'])} strategies, {reds} RED)")
    print(f"[eod_report] dashboard link: /reports/{args.date}")
    sys.exit(0)


if __name__ == "__main__":
    main()
