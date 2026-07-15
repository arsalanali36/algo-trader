#!/usr/bin/env python3
"""One-off probe: REAL per-lot naked-short vs hedged-spread margin from the
EXECUTING broker (Kite, default_broker=kite), for a CURRENT NIFTY weekly option.

Historical/expired contracts can't be margined by the live API, so the capital
sim uses a per-underlying real ₹/lot constant probed from a current equivalent
contract. This proves the token is alive, that Kite basket_order_margins exists
(the ONLY source of real cross-leg spread benefit), and prints the two numbers.
Read-only margin queries — no orders, ₹0 cost.
"""
import sys, os, json
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import _paths  # noqa
import risk_gate, dhan_master

CONFIG = os.path.join(ROOT, "data", "config.json")


def _dhan_creds():
    d = json.load(open(CONFIG))
    return d.get("jwt_token"), str(d.get("client_id"))


def nifty_spot():
    import requests
    tok, cid = _dhan_creds()
    r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                      json={"IDX_I": [13]},
                      headers={"access-token": tok, "client-id": cid,
                               "Content-Type": "application/json"}, timeout=6)
    d = r.json().get("data", {}).get("IDX_I", {})
    for v in d.values():
        return float(v.get("last_price") or 0)
    return 0.0


def main():
    print("default_broker:", risk_gate.default_broker())
    spot = nifty_spot()
    print("NIFTY spot:", spot)
    if not spot:
        print("NO SPOT — abort"); return

    # ATM CE current-week (offset 0), and hedge 5 strikes further OTM (config)
    sell_sec, sell_sym, lot = dhan_master.get_option_contract("NIFTY", spot, "CE", 0)
    hedge_sec, hedge_sym, _ = dhan_master.get_option_contract("NIFTY", spot, "CE", 5)
    print(f"SELL leg: {sell_sym} sec={sell_sec} lot={lot}")
    print(f"HEDGE leg (5 OTM): {hedge_sym} sec={hedge_sec}")

    tok, cid = _dhan_creds()
    sell_prem = risk_gate._quick_option_ltp(sell_sec, tok, cid) or 100.0
    hedge_prem = risk_gate._quick_option_ltp(hedge_sec, tok, cid) or 5.0
    print(f"SELL premium ~{sell_prem}  HEDGE premium ~{hedge_prem}")

    qty = int(lot)
    # NAKED — real executing-broker margin for the single SELL leg
    naked = risk_gate.broker_real_margin(sell_sec, "NSE_FNO", qty, sell_prem, "SELL",
                                         trad_sym=sell_sym)
    print(f"\nNAKED real margin (1 lot={qty}): Rs {naked}")

    # HEDGED — real Kite BASKET margin (SELL ATM CE + BUY 5-OTM CE), netted spread benefit
    try:
        from brokers import get_broker
        kb = get_broker("kite")
        kite = kb._get_kite()
        ksell = kb.resolve_symbol(sell_sym, sec_id=sell_sec)
        khedge = kb.resolve_symbol(hedge_sym, sec_id=hedge_sec)
        print(f"kite syms: sell={ksell} hedge={khedge}")
        basket = [
            {"exchange": "NFO", "tradingsymbol": ksell, "transaction_type": "SELL",
             "variety": "regular", "product": "MIS", "order_type": "LIMIT",
             "quantity": qty, "price": float(sell_prem)},
            {"exchange": "NFO", "tradingsymbol": khedge, "transaction_type": "BUY",
             "variety": "regular", "product": "MIS", "order_type": "LIMIT",
             "quantity": qty, "price": float(hedge_prem)},
        ]
        has_basket = hasattr(kite, "basket_order_margins")
        print("kite has basket_order_margins:", has_basket)
        if has_basket:
            res = kite.basket_order_margins(basket, consider_positions=True, mode="compact")
            print("BASKET raw:", json.dumps(res, default=str)[:800])
            fin = (res or {}).get("final") or {}
            print(f"\nHEDGED real basket margin (1 lot): Rs {fin.get('total')}")
        # also per-leg sum for contrast (over-estimate — no spread benefit)
        m_sell = kb.margin_for_order(ksell, "NFO", "SELL", qty, sell_prem, product="MIS")
        m_buy = kb.margin_for_order(khedge, "NFO", "BUY", qty, hedge_prem, product="MIS")
        print(f"per-leg sum (no netting): sell {m_sell} + buy {m_buy} = "
              f"{(m_sell or 0)+(m_buy or 0)}")
    except Exception as e:
        import traceback; traceback.print_exc()
        print("BASKET probe failed:", e)


if __name__ == "__main__":
    main()
