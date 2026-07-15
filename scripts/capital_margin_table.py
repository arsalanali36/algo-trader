#!/usr/bin/env python3
"""Build a REAL per-underlying margin table for the shared-capital-pool sim.

Historical option contracts in trades.db are EXPIRED — the live margin API can
only price CURRENTLY-tradable contracts, so per-position margin for the sim is
taken from a current EQUIVALENT contract per underlying (SPAN/exposure barely
depends on which strike, only on the underlying level + lot size). This is the
faithful stand-in for "what would this position have blocked", and it uses the
EXECUTING broker's real numbers (Kite order_margins / basket_order_margins),
not a multiplier — Rule 6B: reuses risk_gate.broker_real_margin + the same
hedge resolution semantics as strategy_safety.compute_hedge_target.

  naked_per_lot   = real Kite SELL-leg margin for a current ATM short.
  hedged_per_lot  = real Kite BASKET margin (SELL ATM + BUY hedge), netted
                    spread benefit — the number that lets more legs fit ₹11L.
  hedge params    = deployed config: >=hedge_offset_strikes OTM, walk further
                    until premium <= hedge_max_premium_rs (risk_gate.hedge_config).

Read-only margin queries — no orders, ₹0 cost. Output: data/capital_sim_margins.json
Run ON THE VPS (needs Kite token + dhan_master):
    venv/bin/python scripts/capital_margin_table.py
"""
import sys, os, json, time
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa
import risk_gate, dhan_master

CONFIG = os.path.join(ROOT, "data", "config.json")
OUT = os.path.join(ROOT, "data", "capital_sim_margins.json")
IDX_SECID = {"NIFTY": 13, "BANKNIFTY": 25}

# top-18 by SELL-leg count → ~96% of the 333 SELL legs; rest use SPAN% fallback.
TARGETS = ["NIFTY", "BANKNIFTY", "MARUTI", "SUNPHARMA", "AXISBANK", "LT", "TCS",
           "ULTRACEMCO", "TITAN", "HINDUNILVR", "RELIANCE", "ICICIBANK", "SBIN",
           "ADANIENT", "BAJFINANCE", "NESTLEIND", "INFY", "HDFCBANK"]


def _creds():
    d = json.load(open(CONFIG))
    return d.get("jwt_token"), str(d.get("client_id"))


def _ltp(seg, sec_id):
    """Rate-limited Dhan marketfeed LTP (the live ltp_poller daemon saturates
    this endpoint — go through dhan_rate_limiter + retry, else instant 429)."""
    import requests
    try:
        import dhan_rate_limiter as _rl
    except Exception:
        _rl = None
    tok, cid = _creds()
    for attempt in range(5):
        if _rl:
            try: _rl.acquire("ltp")
            except Exception: pass
        try:
            r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                              json={seg: [int(sec_id)]},
                              headers={"access-token": tok, "client-id": cid,
                                       "Content-Type": "application/json"}, timeout=6)
            if r.status_code == 429:
                if _rl:
                    try: _rl.note_429()
                    except Exception: pass
                time.sleep(1.2 * (attempt + 1))
                continue
            d = r.json().get("data", {}).get(seg, {})
            for v in d.values():
                return float(v.get("last_price") or 0)
            return 0.0
        except Exception:
            time.sleep(0.8)
    return 0.0


def _spot(sym):
    # index: prefer the poller's shared cache (zero API), else rate-limited fetch
    if sym in IDX_SECID:
        try:
            import shared_ltp_cache
            v = shared_ltp_cache.get_index(sym, max_age=120) or \
                shared_ltp_cache.get_stale(IDX_SECID[sym], max_age=300)
            if v:
                return float(v)
        except Exception:
            pass
        return _ltp("IDX_I", IDX_SECID[sym])
    try:
        info = dhan_master.get_equity_info(sym)
        # get_equity_info returns a tuple (sec_id, seg, type) — not a dict
        if isinstance(info, (list, tuple)) and info:
            sid = info[0]
        elif isinstance(info, dict):
            sid = info.get("sec_id") or info.get("security_id")
        else:
            sid = None
        if sid:
            try:
                import shared_ltp_cache
                v = shared_ltp_cache.get_stale(sid, max_age=300)
                if v:
                    return float(v)
            except Exception:
                pass
            return _ltp("NSE_EQ", sid)
    except Exception:
        pass
    return 0.0


def _kite():
    from brokers import get_broker
    return get_broker("kite")


def _basket_margin(kb, sell_sym, sell_sec, sell_prem, buy_sym, buy_sec, buy_prem, qty):
    """Real netted basket margin (SELL + BUY hedge) via Kite basket_order_margins."""
    try:
        kite = kb._get_kite()
        ksell = kb.resolve_symbol(sell_sym, sec_id=sell_sec)
        kbuy = kb.resolve_symbol(buy_sym, sec_id=buy_sec)
        if not ksell or not kbuy:
            return None
        basket = [
            {"exchange": "NFO", "tradingsymbol": ksell, "transaction_type": "SELL",
             "variety": "regular", "product": "MIS", "order_type": "LIMIT",
             "quantity": int(qty), "price": float(sell_prem)},
            {"exchange": "NFO", "tradingsymbol": kbuy, "transaction_type": "BUY",
             "variety": "regular", "product": "MIS", "order_type": "LIMIT",
             "quantity": int(qty), "price": float(buy_prem or 1.0)},
        ]
        res = kite.basket_order_margins(basket, consider_positions=True, mode="compact")
        fin = (res or {}).get("final") or {}
        t = fin.get("total")
        return float(t) if t is not None else None
    except Exception as e:
        print(f"    basket margin fail: {e}", flush=True)
        return None


def probe(sym, min_strikes, max_prem):
    tok, cid = _creds()
    spot = _spot(sym)
    if not spot:
        print(f"  {sym}: NO SPOT — skip", flush=True)
        return None
    sell_sec, sell_sym, lot = dhan_master.get_option_contract(sym, spot, "CE", 0)
    if not sell_sec:
        print(f"  {sym}: no ATM contract — skip", flush=True)
        return None
    lot = int(lot or 1)
    sell_prem = risk_gate._quick_option_ltp(sell_sec, tok, cid) or max(1.0, spot * 0.01)
    # find ATM strike for width/notional
    # hedge: >= min_strikes OTM, walk further until premium <= max_prem
    off = max(int(min_strikes or 5), 1)
    hedge_sec, hedge_symn, _ = dhan_master.get_option_contract(sym, spot, "CE", off)
    hedge_prem = risk_gate._quick_option_ltp(hedge_sec, tok, cid) or 5.0
    for _ in range(12):
        if hedge_prem <= (max_prem or 15) or not hedge_sec:
            break
        off += 1
        hs, hsym, _ = dhan_master.get_option_contract(sym, spot, "CE", off)
        if not hs or hs == hedge_sec:
            break
        hedge_sec, hedge_symn = hs, hsym
        hedge_prem = risk_gate._quick_option_ltp(hedge_sec, tok, cid) or hedge_prem
    naked = risk_gate.broker_real_margin(sell_sec, "NSE_FNO", lot, sell_prem, "SELL",
                                         trad_sym=sell_sym)
    kb = _kite()
    hedged = _basket_margin(kb, sell_sym, sell_sec, sell_prem, hedge_symn, hedge_sec,
                            hedge_prem, lot)
    # strike step from sym (approx notional for span%)
    def _strike(s):
        try:
            return float(str(s).split("-")[2])
        except Exception:
            return spot
    sell_k = _strike(sell_sym); hedge_k = _strike(hedge_symn)
    rec = {
        "spot": round(spot, 2), "lot": lot, "atm_strike": sell_k,
        "sell_prem": round(sell_prem, 2), "hedge_strike": hedge_k,
        "hedge_prem": round(hedge_prem, 2),
        "strike_width": abs(hedge_k - sell_k),
        "naked_per_lot": round(naked, 2) if naked else None,
        "hedged_per_lot": round(hedged, 2) if hedged else None,
        "span_pct_naked": round(naked / (sell_k * lot), 4) if naked else None,
    }
    print(f"  {sym}: naked ₹{rec['naked_per_lot']}/lot  hedged ₹{rec['hedged_per_lot']}/lot  "
          f"span%={rec['span_pct_naked']}  hedgeΔ={off}strk prem₹{rec['hedge_prem']}", flush=True)
    return rec


def main():
    min_strikes, max_prem = risk_gate.hedge_config("range_v1")
    if not min_strikes:
        min_strikes, max_prem = 5, 15  # deployed global values even if hedge_enabled False
    print(f"hedge params: >= {min_strikes} strikes OTM, walk to premium <= ₹{max_prem}\n", flush=True)
    table = {}
    if os.path.exists(OUT):
        try:
            table = json.load(open(OUT))
        except Exception:
            table = {}
    for sym in TARGETS:
        if sym in table and table[sym].get("naked_per_lot"):
            print(f"  {sym}: cached", flush=True); continue
        try:
            rec = probe(sym, min_strikes, max_prem)
        except Exception as e:
            print(f"  {sym}: ERROR {e}", flush=True); rec = None
        if rec:
            table[sym] = rec
            json.dump(table, open(OUT, "w"), indent=1)  # checkpoint each
        time.sleep(0.4)
    # calibrate SPAN% fallback (median of stock probes) for un-probed underlyings
    stock_spans = [v["span_pct_naked"] for k, v in table.items()
                   if k not in IDX_SECID and v.get("span_pct_naked")]
    ratios = [v["hedged_per_lot"] / v["naked_per_lot"] for v in table.values()
              if v.get("hedged_per_lot") and v.get("naked_per_lot")]
    stock_spans.sort(); ratios.sort()
    med_span = stock_spans[len(stock_spans)//2] if stock_spans else 0.15
    med_ratio = ratios[len(ratios)//2] if ratios else 0.30
    table["_fallback"] = {"span_pct_naked": round(med_span, 4),
                          "hedged_naked_ratio": round(med_ratio, 4),
                          "note": "median of real stock probes; applied to strike*qty "
                                  "for un-probed underlyings"}
    json.dump(table, open(OUT, "w"), indent=1)
    print(f"\nFallback SPAN%={med_span:.3f}  hedged/naked ratio={med_ratio:.3f}")
    print(f"Wrote {OUT}  ({len([k for k in table if not k.startswith('_')])} underlyings)")


if __name__ == "__main__":
    main()
