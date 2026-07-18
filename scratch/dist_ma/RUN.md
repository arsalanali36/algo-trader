# dist_ma — Phase 3 runbook (turn it on in PAPER)

Positional Distance-from-20EMA equity strategy. Everything is built + verified
offline; below is how to actually run it in paper on the dashboard machine.

## Pieces
| File | Role |
|------|------|
| `scratch/dist_ma/dist_ma.py` | indicators + backtest (research) |
| `scratch/dist_ma/dist_ma_engine.py` | decision engine (LIVE == backtest) |
| `scratch/dist_ma/portfolio.py` | ₹ book / CAGR / DD sim |
| `scratch/dist_ma/scanner.py` | ad-hoc "today's radar" |
| `strategies/live/dist_ma_trader.py` | the paper/live trader process |
| `_ops/dist_ma_daily_update.py` | keeps the daily-equity lake fresh |

## 1. Deploy the code (worktree → master → VPS)
```bash
# LOCAL: merge the worktree branch into master and push
git checkout master && git merge feat/dist-ma-extremes && git push origin master
# VPS: pull + restart dashboard (dir has a space — quote it)
ssh -i "C:/Users/arsal/.ssh/khazana_ed25519" root@72.61.173.32 \
  "cd '/root/ARSALAN/CODE3B- TV BACKTEST ENGINE' && git pull origin master && systemctl restart algo-dashboard"
```
> ⚠️ This ships `_core/risk_gate.py`, `trader_dashboard.py`, `health_check.py`
> changes to the LIVE box. Deploy market-CLOSED, check strategy PIDs before==after
> (`KillMode=process`), verify live positions intact. The trader itself is
> paper+inactive, but do this carefully — it's the money box.

## 2. Seed + schedule the daily-equity lake (VPS)
The trader reads COMPLETED daily bars from the lake. On the VPS it's empty — seed
it, then keep it fresh nightly:
```bash
# one-time seed (all F&O symbols, ~150 recent daily bars each)
cd '/root/ARSALAN/CODE3B- TV BACKTEST ENGINE' && venv/bin/python _ops/dist_ma_daily_update.py
```
systemd timer (post-close, Mon–Fri 16:20 IST = 10:50 UTC):
```ini
# /etc/systemd/system/algo-distma-lake.service
[Service]
WorkingDirectory=/root/ARSALAN/CODE3B- TV BACKTEST ENGINE
ExecStart=/root/ARSALAN/CODE3B- TV BACKTEST ENGINE/venv/bin/python _ops/dist_ma_daily_update.py
# /etc/systemd/system/algo-distma-lake.timer
[Timer]
OnCalendar=Mon..Fri 10:50 UTC
Persistent=true
[Install]
WantedBy=timers.target
```
`systemctl enable --now algo-distma-lake.timer`

## 3. Config entry (dashboard machine — nifty_config.json is per-machine/gitignored)
Add this key to `nifty_config.json` (or via the dashboard Run modal once it lists
"distma"). Start with `active:false`, flip to `true` when ready:
```json
"distma_v1": {
  "active": false,
  "mode": "paper",
  "broker": "kite",
  "capital": 100000,
  "max_slots": 10,
  "symbols": "",
  "thresh": -10.0,
  "look": 3,
  "entry_win": 3,
  "max_hold": 40,
  "sl_atr": 1.5
}
```
- `symbols: ""` = whole F&O lake. Or a comma list to start small.
- `capital / max_slots` = ₹ per position (₹10k default). Keep `max_slots ≥ 8`
  (diversification — see FINDINGS.md; 2-3 slots blew up to −84% DD).

## 4. Turn on + watch (Phase 3 goal)
1. Set `distma_v1.active = true` (mode stays `paper`).
2. It runs once per new completed trading day (positional — not intraday).
3. Watch dashboard **Orders & P&L** (Mode filter = Paper/All): BUY entries + SELL
   exits should appear with tag `DISTMA`, holding across days (positional).
4. Cross-check against the backtest: entry dates/prices should match the engine.
   Run `python scratch/dist_ma/scanner.py` to see the same radar independently.
5. After a few weeks of clean paper behaviour → discuss live (small qty, CNC).

## Notes / honest gaps
- Paper still needs a valid Dhan token (smart_order fetches a live equity quote to
  record the fill — same as every other strategy).
- Positional exemptions apply: no 3:15 squareoff, no RMS profit-lock/max-trades on
  this id (Rule 10 / TRAP #119) — it uses its own backtested exits.
- Return is ~index-like (~10%/yr, ~20-35% DD) — value is the disciplined timing,
  not outsized returns (see FINDINGS.md).
