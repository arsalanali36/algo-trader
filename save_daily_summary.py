#!/usr/bin/env python3
"""
save_daily_summary.py — Daily P&L summary from nifty_trader.log
Run after market close: python save_daily_summary.py
Saves to: /root/code4/results/YYYY-MM-DD.txt
"""

import re
import json
from datetime import datetime
from pathlib import Path

BASE_DIR    = Path(__file__).resolve().parent
LOG_FILE    = BASE_DIR / "nifty_trader.log"
RESULTS_DIR = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

today = datetime.now().strftime("%Y-%m-%d")

with open(LOG_FILE) as f:
    lines = f.readlines()

# Only today's lines
today_lines = [l for l in lines if l.startswith(today)]

# Extract signals
signals = {}
for line in today_lines:
    m = re.search(r'  (\w+)\s+close=([\d.]+)\s+signal=(BUY|SELL)', line)
    if m:
        sym, price, sig = m.group(1), float(m.group(2)), m.group(3)
        time_str = line[11:19]
        signals.setdefault(sym, []).append((sig, price, time_str))

# Calculate P&L
trades = []
total_pnl = 0
wins = losses = 0

for sym, entries in sorted(signals.items()):
    if len(entries) < 2:
        continue
    for i in range(len(entries) - 1):
        a, b = entries[i], entries[i+1]
        pnl = None
        if a[0] == 'BUY' and b[0] == 'SELL':
            pnl = (b[1] - a[1]) * 50
            trade = {'sym': sym, 'entry': 'BUY', 'entry_price': a[1], 'entry_time': a[2],
                     'exit': 'SELL', 'exit_price': b[1], 'exit_time': b[2], 'pnl': pnl}
        elif a[0] == 'SELL' and b[0] == 'BUY':
            pnl = (a[1] - b[1]) * 50
            trade = {'sym': sym, 'entry': 'SELL', 'entry_price': a[1], 'entry_time': a[2],
                     'exit': 'BUY', 'exit_price': b[1], 'exit_time': b[2], 'pnl': pnl}
        if pnl is not None:
            trades.append(trade)
            total_pnl += pnl
            if pnl > 0: wins += 1
            else: losses += 1

# Write summary
out_file = RESULTS_DIR / f"{today}.txt"
with open(out_file, 'w') as f:
    f.write(f"{'='*55}\n")
    f.write(f"  DAILY TRADING SUMMARY — {today}\n")
    f.write(f"  Strategy: EMA 9/20 | TF: 1min | Qty: 50\n")
    f.write(f"{'='*55}\n\n")

    if not trades:
        f.write("  No completed trades today.\n")
    else:
        f.write(f"  {'SYMBOL':<12} {'ENTRY':<5} {'@PRICE':<10} {'EXIT':<5} {'@PRICE':<10} {'P&L':>8}  RESULT\n")
        f.write(f"  {'-'*65}\n")
        for t in trades:
            tag = 'WIN ✅' if t['pnl'] > 0 else 'LOSS ❌'
            f.write(f"  {t['sym']:<12} {t['entry']:<5} {t['entry_price']:<10.2f} {t['exit']:<5} {t['exit_price']:<10.2f} {t['pnl']:>+8.0f}  {tag}\n")

        f.write(f"\n{'─'*55}\n")
        f.write(f"  Total Trades : {len(trades)}\n")
        f.write(f"  Wins         : {wins}\n")
        f.write(f"  Losses       : {losses}\n")
        f.write(f"  Win Rate     : {wins/len(trades)*100:.1f}%\n")
        f.write(f"  Total P&L    : {total_pnl:+.0f}\n")
        f.write(f"{'='*55}\n")

# Print to screen too
with open(out_file) as f:
    print(f.read())

print(f"Saved: {out_file}")

# Also append to master JSON log
master = RESULTS_DIR / "master_log.json"
log_data = []
if master.exists():
    log_data = json.loads(master.read_text())

log_data.append({
    "date": today,
    "trades": len(trades),
    "wins": wins,
    "losses": losses,
    "win_rate": round(wins/len(trades)*100, 1) if trades else 0,
    "total_pnl": round(total_pnl, 2),
    "details": trades
})
master.write_text(json.dumps(log_data, indent=2))
print(f"Master log updated: {master}")
