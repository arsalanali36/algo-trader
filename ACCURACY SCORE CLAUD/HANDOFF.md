# CODE3B — HANDOFF (next session resume point)

**Last updated:** 2026-06-18. All work committed to CODE3B git (master) + GitHub.
Full log: `CODE3B/CLAUDE.md` Update Log.

---

## ✅ WHAT'S DONE — DON'T REDO

### Universe System (Phases 0–3) — VPS pe live
| File | Kaam |
|------|------|
| `brokers/base_broker.py` | Abstract interface (Dhan/Kite switchable) |
| `brokers/dhan_broker.py` | Dhan Data API candles (IST +5:30), REST orders/quote, funds |
| `brokers/kite_broker.py` | Stub — future Zerodha |
| `dhan_feed.py` | WebSocket Full packet → `LIVE` dict (ltp/bid/ask per sec_id) |
| `smart_order.py` | Marketable-limit (BUY=ask, SELL=bid, tick 0.05). Paper==Live. Shadow badge. |
| `universe.py` | Nifty-50 list + equity/option sec_id resolvers |
| `universe_trader.py` | Engine: scan all 50, route equity/stock_opt/index_opt, caps, 3:15 exit |
| `strategies/` | `base.py` + `sample_ema.py` + `always_buy.py` plugins |

Config: `nifty_config.json` → `"universe_v1"` variation block.
Dashboard: `STRATEGIES["universe"]` entry registered.

### Validation (Phases 4–5) — 90.2% exact / 93% entry
- Tool: `validate_strategy.py` — `--signals <pine-log.csv>` score, `--debug YYYY-MM-DD` trace, HTML report
- Methodology: **READ FIRST** → `ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md`
- Ground truth file: `ACCURACY SCORE CLAUD/TEST 1/pine-logs UPDATE.csv` (consistent single-run log)
- Engine fixes baked in: Wilder ATR, WICK_RATIO=2.5, redHammer upperWick≤body, next-bar fill, pyramiding=0, block ≥15:15

### Range Trader engine — `range_trader.py` on VPS ✅
Key values (DO NOT regress):
```python
WICK_RATIO = 2.5
# red_hammer: lw >= WICK_RATIO * body and uw <= body
# compute_atr: tr.ewm(alpha=1.0 / period, adjust=False).mean()  ← Wilder RMA
```

### Pine Version Control — `_PINE/` folder ✅
```
_PINE/range_chain.pine          ← CANONICAL latest (paste new version here)
_PINE/range_chain_zonelog.pine  ← validation variant (log.info ZONE+SIGNAL+EXIT)
_PINE/README.md                 ← ritual + TV chart link
```
Tagged `pine-v1` on GitHub. TV chart: https://in.tradingview.com/chart/KS2Wf9N5/

### VPS Status ✅
- IP: `72.61.173.32:5099` | Dir: `/root/code4/` | Service: `algo-dashboard`
- SSH Key: `C:\Users\arsal\.ssh\khazana_ed25519`
- All files deployed. `systemctl status algo-dashboard` → active.
- Deploy: `python deploy_vps.py` (in CODE3B dir)

---

## 🔴 PENDING — START HERE NEXT SESSION

### Phase 6 — Go Live (small qty, market hours)

**Pre-req:**
1. Dhan account mein balance fund karo (was ₹0.18 during testing)
2. Dhan JWT token refresh — Control tab mein paste karo (expires every 24h)
3. Market hours: 9:15–15:30 IST

**Test order flow (one manual order first):**
1. Dashboard open → `http://72.61.173.32:5099`
2. Quick Order widget → Paper toggle → **Live** toggle
3. NIFTY ATM CE/PE → LTP dikhe → Place Order → verify Dhan order-book mein aaya

**Then universe live:**
```json
// nifty_config.json mein universe_v1 update:
{
  "active": true,
  "mode": "live",        ← paper se live karo
  "max_concurrent_positions": 1,
  "max_trades_per_symbol": 1,
  "qty": 1
}
```
Start universe_v1 from dashboard → watch P&L tab → Open Positions LTP live → shadow badge ✓/✗.

**UI polish (pending, non-blocking):**
- Universe config tab in dashboard (abhi manual JSON edit)
- Shadow status badge on Open Positions rows (intended fill vs actual Dhan fill)
- Quick Order widget showing live bid/ask from `dhan_feed.py`

---

## Pine Workflow (agar user nayi script paste kare)

1. User chat mein poori nayi Pine paste karta hai
2. `_PINE/range_chain.pine` overwrite (Write tool)
3. `git diff -- _PINE/range_chain.pine` → changes explain (logic vs cosmetic)
4. Logic change → `range_trader.py` sync → `range_chain_zonelog.pine` update
5. User fresh Pine Logs export kare → `validate_strategy.py --signals <log>` → naya score
6. Commit + push: `pine: <summary> + engine sync`

---

## Key File Map

| File | Purpose |
|------|---------|
| `range_trader.py` | **Single source of truth** for all Range Chain logic (live + validation) |
| `validate_strategy.py` | Validation harness — score + HTML report + debug |
| `trader_dashboard.py` | Flask UI, spawn/stop strategies, P&L/Open Positions |
| `dhan_master.py` | Scrip master download + `get_sec_id_for_trad_sym()` |
| `deploy_vps.py` | SCP deploy to VPS |
| `nifty_config.json` | All strategy configs |
| `ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md` | **READ FIRST before any Pine→Python validation** |

## Critical rules (never violate)
- IPv4 force patch at top of every trader file (DH-905 — VPS IPv6 issue)
- Dhan token expires every 24h — Control tab se update
- `bundle.js` AUTO-GENERATED — kabhi direct edit mat karo
- `validate_strategy.py --signals` = single consistent log → score accurate; mixed exports = fake low scores
