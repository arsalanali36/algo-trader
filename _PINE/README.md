# _PINE — TradingView Pine scripts (version-controlled)

Yeh folder Pine strategies ka **system of record** hai. TradingView pe edit karo,
phir yahan paste/save karo — git har version track karega (diff, history, revert),
GitHub pe backup (`algo-trader.git`).

## Files
| File | Kya hai |
|------|---------|
| `range_chain.pine` | **CANONICAL latest** — Ars_Auto_Rev_Chain strategy. Naya version isi me overwrite hota hai. |
| `range_chain_zonelog.pine` | Same strategy + `log.info` (ZONE/SIGNAL/EXIT) — **validation** ke liye (Pine Logs export → `validate_strategy.py --signals`). |

> Original snapshots `ACCURACY SCORE CLAUD/TEST 1/` me bhi hain (validation record);
> aage se canonical yahi `_PINE/` files hain.

## Workflow (nayi version aane par — Claude se)
1. Tum poori nayi script **chat me paste** karo.
2. Claude `range_chain.pine` **overwrite** karta hai.
3. `git diff -- _PINE/range_chain.pine` → exact changes; Claude **plain-Hinglish me explain** karta hai
   (logic change vs cosmetic flag karke).
4. **Logic change** ho to → `range_trader.py` (Python engine) sync + `range_chain_zonelog.pine` update.
5. Tum fresh **Pine Logs** export do (`range_chain_zonelog.pine` se) → `validate_strategy.py --signals <log>` → **naya match-score**.
   Methodology: `../ACCURACY SCORE CLAUD/VALIDATION_PLAYBOOK.md`.
6. Pine + engine **ek saath commit** + GitHub push.

## Useful git
```bash
git diff -- _PINE/range_chain.pine        # last commit se kya badla
git log --oneline -- _PINE/range_chain.pine   # is file ki history
git tag pine-v2                            # milestone version mark
git checkout <commit> -- _PINE/range_chain.pine   # purani version wapas
```

## Engine sync — kahan
Pine ka logic (entry/exit/zone/levels/ATR/candle) ka Python mirror = **`../range_trader.py`**
(single source of truth — live trader + validation dono yahi use karte hain).
Sirf plots/labels/colors/boxes wale changes engine ko affect nahi karte.

## TV chart
Strategy chart: https://in.tradingview.com/chart/KS2Wf9N5/  (update if it changes)
