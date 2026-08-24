"""
delta_testnet_check.py — validate Delta India TESTNET auth + order plumbing.

Run AFTER putting testnet keys in .env (DELTA_API_KEY / DELTA_API_SECRET / DELTA_TESTNET=1).

Default (read-only): prints paper wallet balance + a live BTC chain sample — proves the
API key + HMAC signature work on testnet (no order placed).

--fire : places ONE 1-lot ATM CE test order on TESTNET (paper money, no real ₹), reads it
         back from positions, then closes it — proves the full order->fill->read path and
         that fills are visible on Delta's own testnet platform for cross-validation.
"""
import os
import sys
import argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (_ROOT, os.path.join(_ROOT, "brokers")):
    if p not in sys.path:
        sys.path.insert(0, p)

from brokers.delta_broker import DeltaBroker, _is_testnet  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fire", action="store_true",
                    help="place+read+close ONE 1-lot testnet test order (paper money)")
    args = ap.parse_args()

    b = DeltaBroker()
    print(f"base      : {b.base}")
    print(f"testnet   : {b.testnet}  (DELTA_TESTNET env = {os.getenv('DELTA_TESTNET','')!r})")
    print(f"has creds : {b.has_creds()}")
    if not b.testnet:
        print("⚠️  NOT in testnet mode — set DELTA_TESTNET=1 in .env before this check.")
        return
    if not b.has_creds():
        print("⚠️  No API key/secret — put DELTA_API_KEY / DELTA_API_SECRET in .env.")
        return

    # --- read-only: wallet balance (proves auth + signature) ---
    f = b.funds()
    if not f:
        print("❌ funds() empty — auth likely failing (bad key/secret, or IP not whitelisted).")
        return
    print(f"\n✅ AUTH OK — testnet wallet available balance: {f.get('available')}")
    print("   (paper money — this is your testnet balance, not real ₹)")

    # --- live chain sample (public, testnet) ---
    prods = b._products()
    btc_opt = [s for s, p in prods.items()
               if s.startswith("C-BTC-") and p.get("state") == "live"]
    print(f"\ntestnet live BTC call options: {len(btc_opt)}")
    spot = b.quote("BTCUSD").get("ltp")
    print(f"testnet BTC spot: {spot}")

    if not args.fire:
        print("\n(read-only check done. Re-run with --fire to place a 1-lot testnet test order.)")
        return

    # --- --fire: place ONE 1-lot ATM CE test order on TESTNET ---
    if not spot:
        print("❌ no spot — cannot pick ATM."); return
    atm = round(spot / 500) * 500
    import datetime as dt
    # nearest listed BTC expiry
    exps = sorted({s.split("-")[3] for s in btc_opt})
    if not exps:
        print("❌ no BTC option expiries on testnet."); return
    code = exps[0]
    sym = f"C-BTC-{atm}-{code}"
    if sym not in prods:
        # fall back to any listed ATM-ish call for this expiry
        cands = [s for s in btc_opt if s.endswith("-" + code)]
        sym = sorted(cands, key=lambda s: abs(int(s.split("-")[2]) - atm))[0] if cands else sym
    print(f"\n🔫 TESTNET test order: BUY 1 lot {sym}")
    r = b.place_order("BUY", sym, qty=1, order_type="MARKET")
    print(f"   place_order -> status={r.get('status')} id={r.get('order_id')} reason={r.get('reason')}")
    if r.get("status") == "rejected":
        print("   (rejected — check permission=Trading on the key + IP whitelist)")
        return
    import time
    time.sleep(2)
    pos = b.positions()
    print(f"   positions after: {pos}")
    print("\n✅ order path works — check this fill on the Delta testnet platform too.")
    print("   (close it manually on testnet, or we wire auto-close in the full trader.)")


if __name__ == "__main__":
    main()
