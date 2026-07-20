#!/usr/bin/env python3
"""DRY-RUN capture of the VRP Overnight Condor's intended entry.

Records the EXACT 4-leg structure + live premiums the strategy WOULD enter right
now (meant to run at the 15:10 entry window) — WITHOUT placing a single order.
So if the strategy stays disabled or is fixed only after market close, we still
have the correct entry recorded and can place/verify it — the day isn't wasted.

Reuses vrp_condor_trader's OWN helpers (load_creds / fetch_spot / _resolve /
_opt_ltp / _get_broker / load_config) so the captured structure can never drift
from what the strategy actually does. Never imports execution_gateway, never
calls execute_signal — read-only.

Output: data/vrp_condor_capture_<date>.json   (also prints to stdout/log)
"""
import sys
import os
import json
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("strategies/live", "_core", "_data", "."):
    sys.path.insert(0, os.path.join(BASE, _p))

import vrp_condor_trader as v   # reuse its helpers, NOT its order path


def main(strategy_id="vrp_condor_v1"):
    ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    tc = v.load_config(strategy_id)
    body = int(tc.get("body_off", 3))
    wing = int(tc.get("wing_off", 5))
    lots = int(tc.get("qty", 1))
    sym = tc.get("symbol", "NIFTY")
    bname = (tc.get("broker") or "dhan")

    try:
        token, cid = v.load_creds()
    except Exception as e:
        print(f"[capture] creds load failed: {e}", flush=True)
        return
    spot = v.fetch_spot(token, cid)
    if not spot:
        print("[capture] no spot — abort (retry a bit later)", flush=True)
        return

    # SAME strike geometry as _enter_condor(): SELL body ±body, BUY wings ±(body+wing)
    legdefs = [
        ("BUY",  "CE", +(body + wing), "wing"),
        ("BUY",  "PE", -(body + wing), "wing"),
        ("SELL", "CE", +body,          "body"),
        ("SELL", "PE", -body,          "body"),
    ]
    broker = v._get_broker(bname)
    legs = []
    for side, ot, off, kind in legdefs:
        r = v._resolve(sym, spot, ot, off)
        if not r:
            print(f"[capture] resolve failed {side} {ot} off={off}", flush=True)
            continue
        sec, tsym, lot = r
        prem = v._opt_ltp(broker, sec) or 0.0
        legs.append({
            "side": side, "kind": kind, "opt_type": ot, "strike_offset": off,
            "sec_id": str(sec), "trad_sym": tsym, "qty": lots * (lot or 1),
            "premium": round(float(prem), 2),
        })

    # net premium the strategy would book (wings long +, body short −) = net credit
    net = sum((1 if l["side"] == "BUY" else -1) * l["premium"] for l in legs)
    out = {
        "captured_at": ist.strftime("%Y-%m-%d %H:%M:%S"),
        "date": ist.strftime("%Y-%m-%d"),
        "strategy": strategy_id,
        "spot": round(spot, 1),
        "body_off": body, "wing_off": wing, "lots": lots,
        "net_premium": round(net, 2),
        "legs": legs,
        "note": "DRY-RUN — NO order placed. Intended VRP condor entry (15:10 window). "
                "Use to place manually / verify if the strategy is fixed after close.",
    }
    path = os.path.join(BASE, "data", f"vrp_condor_capture_{ist.strftime('%Y-%m-%d')}.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print("[capture] ✅ VRP condor entry captured (no order placed):", flush=True)
    print(json.dumps(out, indent=2), flush=True)
    print(f"[capture] saved → {path}", flush=True)


if __name__ == "__main__":
    main()
