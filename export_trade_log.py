#!/usr/bin/env python3
"""
export_trade_log.py — read-only human-readable export of the live trade log.

Reuses order_store's OWN netting (`trades_for_range` / `stats_summary`) so the
export can never drift from what the dashboard shows (Rule 6B). Produces:
  TRADE_LOG.md   — day-by-day tables + per-strategy/per-mode/overall summaries
  trades.csv     — flat dump of every completed trade (programmatic use)

Meant to be committed to a PRIVATE repo (real ₹ P&L). Run it in the CODE3B
project dir (VPS has the live data/trades.db):

    python export_trade_log.py --out _export

Nothing is written to the DB — SELECT-only.
"""
import argparse
import csv
import datetime as _dt
from pathlib import Path

import order_store


def _inr(v):
    """Indian lakh-style grouping: 1,23,456 (not 123,456)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "—"
    neg = v < 0
    n = abs(round(v))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    return ("-₹" if neg else "₹") + s


def _pts(d):
    q = d.get("qty") or 0
    p = d.get("pnl")
    if p is None or not q:
        return None
    return round(p / q, 2)


def _strat_table(details, key, title):
    groups = {}
    for d in details:
        k = d.get(key) or "—"
        g = groups.setdefault(k, {"n": 0, "w": 0, "gross": 0.0})
        g["n"] += 1
        if (d.get("pnl") or 0) > 0:
            g["w"] += 1
        g["gross"] += d.get("pnl") or 0
    if not groups:
        return ""
    lines = [f"## By {title}", "", f"| {title.capitalize()} | Trades | Win% | Gross |",
             "|---|--:|--:|--:|"]
    for k in sorted(groups, key=lambda x: -groups[x]["gross"]):
        g = groups[k]
        wr = round(100 * g["w"] / g["n"], 1) if g["n"] else 0.0
        lines.append(f"| {k} | {g['n']} | {wr}% | {_inr(g['gross'])} |")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_export", help="output directory")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    res = order_store.trades_for_range("0000-00-00", "9999-12-31")
    details = res["details"]
    opens = res.get("open", [])
    stats = order_store.stats_summary()

    now = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None) + _dt.timedelta(hours=5, minutes=30)
    gen = now.strftime("%Y-%m-%d %H:%M IST")

    md = []
    md.append("# CODE3B — Trade Log (live)")
    md.append("")
    md.append(f"_Generated: {gen} · Source: live order_store (`data/trades.db`)_  ")
    md.append("_⚠️ PRIVATE — real trade P&L. Keep this repo private._")
    md.append("")
    md.append("> P&L below is **gross** (premium points × qty), before brokerage/taxes. "
              "Points = per-unit premium move (`₹P&L ÷ qty`).")
    md.append("")
    md.append("## Overall (all-time, completed trades)")
    md.append("")
    md.append(f"- **Trades:** {stats['n_trades']}  ·  **Wins:** {stats['wins']}  ·  "
              f"**Losses:** {stats['losses']}  ·  **Win rate:** {stats['win_rate']}%")
    md.append(f"- **Gross P&L:** {_inr(stats['gross_pnl'])}  ·  "
              f"**Profit factor:** {stats['profit_factor']}  ·  "
              f"**Expectancy:** {_inr(stats['expectancy'])}  ·  **Sharpe:** {stats['sharpe']}")
    md.append(f"- **Avg win:** {_inr(stats['avg_win'])}  ·  **Avg loss:** {_inr(stats['avg_loss'])}")
    md.append("")
    md.append(_strat_table(details, "strategy", "strategy"))
    md.append(_strat_table(details, "mode", "mode"))

    # ── Day-by-day ──
    by_date = {}
    for d in details:
        by_date.setdefault(d.get("exit_date") or d.get("entry_date") or "—", []).append(d)

    md.append("## Daily")
    md.append("")
    cum = 0.0
    for date in sorted(by_date):
        day = sorted(by_date[date], key=lambda x: (x.get("exit_time") or "", x.get("entry_time") or ""))
        net = sum((t.get("pnl") or 0) for t in day)
        cum += net
        md.append(f"### {date}  —  net {_inr(net)} · cumulative {_inr(cum)}  ({len(day)} trades)")
        md.append("")
        md.append("| Entry→Exit | Symbol | Side | Qty | Entry | Exit | Pts | Gross ₹ | Strategy | Mode | Exit Reason |")
        md.append("|---|---|---|--:|--:|--:|--:|--:|---|---|---|")
        for t in day:
            pts = _pts(t)
            md.append(
                f"| {t.get('entry_time','—')}→{t.get('exit_time','—')} "
                f"| {t.get('sym','—')} | {t.get('entry','—')} | {t.get('qty','—')} "
                f"| {t.get('entry_price','—')} | {t.get('exit_price','—')} "
                f"| {'' if pts is None else pts} | {_inr(t.get('pnl'))} "
                f"| {t.get('strategy','—')} | {t.get('mode','—')} | {t.get('exit_reason') or ''} |"
            )
        md.append("")

    # ── Open positions ──
    if opens:
        md.append("## Open positions (as of generation)")
        md.append("")
        md.append("| Symbol | Side | Qty | Entry | Strategy | Mode | Broker |")
        md.append("|---|---|--:|--:|---|---|---|")
        for o in opens:
            md.append(f"| {o.get('sym','—')} | {o.get('entry','—')} | {o.get('qty','—')} "
                      f"| {o.get('entry_price','—')} | {o.get('strategy','—')} "
                      f"| {o.get('mode','—')} | {o.get('broker','—')} |")
        md.append("")

    (out / "TRADE_LOG.md").write_text("\n".join(md), encoding="utf-8")

    # ── CSV ──
    fields = ["entry_date", "entry_time", "exit_date", "exit_time", "sym", "entry",
              "qty", "entry_price", "exit_price", "pnl", "strategy", "mode", "broker",
              "instrument", "source", "exit_reason", "sec_id"]
    with (out / "trades.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for d in details:
            w.writerow(d)

    print(f"OK — {stats['n_trades']} completed trades, {len(opens)} open  ->  {out}/TRADE_LOG.md + trades.csv")


if __name__ == "__main__":
    main()
