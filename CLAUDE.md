# CODE3B — Algo Trader (EMA + RSI)

## Project Ka Kaam
Multi-strategy live/paper algo trader jo Dhan API pe orders deta hai.
Web dashboard se control hota hai — koi command line nahi.

## Files

| File | Kaam |
|------|------|
| `nifty_ema_trader.py` | EMA 9/20 crossover, 1-min TF, 25 Nifty symbols |
| `rsi_trader.py` | RSI(14), 5-min TF, same symbols |
| `trader_dashboard.py` | Flask web UI — port 5099 |
| `save_daily_summary.py` | Aaj ki P&L ko `results/` mein save karo |
| `deploy_vps.py` | SCP se VPS pe push + dashboard restart |

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

## GitHub Backup

```bash
git add .
git commit -m "feat: description"
git push
```

Repo: https://github.com/arsalanali36/algo-trader (private)

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
**Yeh hatao mat — warna DH-905 error aayega.**

### 2. yfinance for candles (DH-902 fix)
Dhan intraday candle API = paid subscription (DH-902).
**Free alternative: yfinance** — NSE symbols `.NS` suffix, indices `^NSEI`.

### 3. correlationId = strategy prefix
Agar dono strategies same symbol trade karti hain (e.g. RELIANCE), orders Dhan mein alag dikhne chahiye:
- EMA orders: `EMA_RELIANCE_<timestamp>`
- RSI orders: `RSI_RELIANCE_<timestamp>`

### 4. Config hot-reload
Trader restart ki zaroorat nahi — `nifty_config.json` / `rsi_config.json` edit karo, agla loop pick kar lega.

### 5. Dhan token — rozana update
JWT token har 24 ghante mein expire hota hai.
Dashboard ke **Control tab** mein token paste karo → Save.

## Data Storage

```
/root/code4/              ← VPS pe
├── nifty_trader.log      ← EMA signals + orders
├── rsi_trader.log        ← RSI signals + orders
├── nifty_config.json     ← EMA config (hot-reload)
├── rsi_config.json       ← RSI config (hot-reload)
├── data/config.json      ← Dhan JWT + client_id (SECRET — gitignored)
└── results/
    ├── YYYY-MM-DD.txt    ← Daily P&L summary
    └── master_log.json   ← All-time P&L history
```

**Local mein store nahi hota** — results/ backup ke liye:
```bash
scp -i "C:/Users/arsal/.ssh/khazana_ed25519" -r root@72.61.173.32:/root/code4/results/ results/
```

## Symbol Map (Yahoo Finance)

```python
SYMBOLS = {
    "NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK",
    "RELIANCE": "RELIANCE.NS", "TCS": "TCS.NS",
    "INFY": "INFY.NS", "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS", "SBIN": "SBIN.NS",
    # ...25 total
}
```

## Dhan Security IDs (Live Orders)

```python
DHAN_INFO = {
    "RELIANCE": ("2885", "NSE_EQ"),
    "TCS":      ("11536", "NSE_EQ"),
    "INFY":     ("1594",  "NSE_EQ"),
    # ...
}
```
Nayi symbol add karni ho → Dhan scrip master CSV se `securityId` dhundho.

## Dashboard Tabs

| Tab | Kya hai |
|-----|---------|
| **Control** | Token update + EMA/RSI start/stop |
| **P&L** | Dono strategies ki P&L + trades table |
| **Log** | EMA log (left) + RSI log (right) |
| **Config** | EMA config + RSI config — alag alag save |

## Nai Strategy Add Karna

1. `<name>_trader.py` banao — `correlationId` mein strategy prefix lagao
2. `trader_dashboard.py` mein `STRATEGIES` dict mein entry add karo:
   ```python
   STRATEGIES = {
       "ema": {...},
       "rsi": {...},
       "newstrat": {"script": "newstrat_trader.py", "log": BASE_DIR/"newstrat.log", "cfg": BASE_DIR/"newstrat_config.json", "grep": "newstrat_trader"},
   }
   ```
3. Dashboard HTML mein Control/P&L/Log/Config tabs mein card add karo
4. `deploy_vps.py` ke `FILES` list mein add karo
5. Deploy: `python deploy_vps.py`

## Update Log

| Date | Kya bana |
|------|----------|
| 2026-06-16 | Project init — EMA + RSI trader, tabbed dashboard, VPS deploy, GitHub backup |
