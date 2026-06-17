# CODE3B — Algo Trader (EMA + RSI + Range)

## Project Ka Kaam
Multi-strategy live/paper algo trader jo Dhan API pe orders deta hai.
Web dashboard se control hota hai — koi command line nahi.

## Files

| File | Kaam |
|------|------|
| `nifty_ema_trader.py` | EMA crossover strategy |
| `rsi_trader.py` | RSI overbought/oversold strategy |
| `range_trader.py` | Range breakout/zone strategy with advanced exits (ATR, Fib) |
| `trader_dashboard.py` | Flask web UI — port 5099, Backend API, process manager |
| `dhan_master.py` | Daily dhan scrip master download + Option contract resolver |
| `save_daily_summary.py` | Aaj ki P&L ko `results/` mein save karo |
| `deploy_vps.py` | SCP se VPS pe push + dashboard restart |
| `templates/index.html`| Dynamic grid UI for configuration and dashboard |

## VPS Info

- **IP:** `72.61.173.32` (Hostinger)
- **User:** `root`
- **Project dir:** `/root/code4/`
- **SSH Key:** `C:\Users\arsal\.ssh\khazana_ed25519`
- **Dashboard:** `http://72.61.173.32:5099`
- **Service:** `algo-dashboard` (systemd, auto-start on reboot)

## Deploy Karna

```bash
python deploy_vps.py
```

Ya manually:
```bash
scp -i "C:/Users/arsal/.ssh/khazana_ed25519" -o StrictHostKeyChecking=no <file> root@72.61.173.32:/root/code4/
ssh -i "C:/Users/arsal/.ssh/khazana_ed25519" root@72.61.173.32 "systemctl restart algo-dashboard"
```

## Strategy Variations & Options Trading

Ab system mein aap ek strategy ke **multiple variations** (jaise `ema_v1`, `ema_v2`, `rsi_v1`) alag alag configurations ke saath ek hi waqt pe chala sakte hain. Har variation ek isolated Python process ki tarah chalta hai.

- **Options Support**: Config grid mein Instrument = "Options" select karke Strike Offset (-3 to +3) de sakte hain.
- **PE/CE Selling**: Agar Long signal aata hai toh PE sell hoga, Short signal pe CE sell hoga. `dhan_master.py` dynamically live ATM strike calculate karke scrip nikalta hai.
- **Config Storage**: Saari configs ek single file `nifty_config.json` mein store hoti hain under keys like `ema_v1`, `range_v2`.

## Data Storage

```
/root/code4/              ← VPS pe
├── logs/                 ← Har variation ki separate log file (e.g. ema_v1.log)
├── nifty_config.json     ← All configurations (hot-reload)
├── data/                 
│   ├── config.json       ← Dhan JWT + client_id (SECRET — gitignored)
│   └── api-scrip-master.csv ← Dhan Options symbols list
└── results/
    ├── YYYY-MM-DD.txt    ← Daily P&L summary
    └── master_log.json   ← All-time P&L history
```

## Critical Rules — Kabhi Mat Bhoolna

### 1. IPv4 Force (DH-905 fix)
VPS pe IPv6 default hoti hai — Dhan reject karta hai. Har trader file ke top mein yeh hona CHAHIYE:
```python
import socket
_orig_gai = socket.getaddrinfo
def _v4(h, p, f=0, t=0, pr=0, fl=0):
    return _orig_gai(h, p, socket.AF_INET, t, pr, fl)
socket.getaddrinfo = _v4
```

### 2. yfinance for candles (DH-902 fix)
Dhan intraday candle API = paid subscription (DH-902).
**Free alternative: yfinance** — NSE symbols `.NS` suffix, indices `^NSEI`.

### 3. correlationId = strategy prefix
Har order ka ek specific correlation ID hota hai jisme strategy version prefix hota hai: `EMA_V1_NIFTY_<timestamp>`.
Issey dashboard P&L parse kar pata hai.

### 4. Dhan token — rozana update
JWT token har 24 ghante mein expire hota hai.
Dashboard ke **Control tab** mein token paste karo → Save.

### 5. Auto Scheduler
Subah 9:10 par `trader_dashboard.py` apne aap saare active variations ko paper mode mein start kar deta hai, aur 15:30 par stop.

## Update Log

| Date | Kya bana |
|------|----------|
| 2026-06-16 | Init — EMA + RSI trader, tabbed dashboard, VPS deploy, GitHub backup |
| 2026-06-16 | Added Range Strategy, Auto-scheduler for 9:10 AM |
| 2026-06-16 | **Options Trading** added via `dhan_master.py` (Dynamic Strike Offset PE/CE Selling) |
| 2026-06-16 | **Multi-Instance (Variations)** added with dynamic Grid UI in Vanilla JS, separated logs and processes |
| 2026-06-17 | Stale entry fix — `run_signal_engine` ab `signal_bar` + `total_bars` return karta hai; main loop skip karta hai agar `(total_bars - sig_bar) > 2` (purana historical signal) |
| 2026-06-17 | TATAMOTORS removed from SYMBOLS (delisted/yfinance error) |
| 2026-06-17 | Options branch mein `price=price` fix — paper log ab 0.00 nahi dikhata |
| 2026-06-17 | Startup exit guard — `if st["position"] is None: continue` before EXIT handler (fake startup trades fix) |
| 2026-06-17 | `[CONFIG]` log line har loop pe — TF, Instrument, Qty, MaxTrades, FreshZoneOnly, Exit mode, Entry rules |
| 2026-06-17 | Log panel — Pause/Play scroll button added; config line 3 separate lines mein (Entry / Exit / Config) |
