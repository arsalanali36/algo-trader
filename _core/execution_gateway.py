"""
execution_gateway.py — THE single gateway every strategy calls to enter/exit.
(Task 3 — see _ADR/ADR-001-execute-signal-gateway.md, CLAUDE.md Rule 6B)

Pehle har strategy file apna entry-sequence khud likhti thi (marketable_price →
gate_entry → default SL tags → smart_order.execute) — teen jagah, teen halke-
alag versions, drift ka classic recipe (TRAP #15/#77 shape). Ab wo sequence
YAHAN ek baar hai; strategy file sirf entry/exit CONDITION likhti hai aur ye
do functions call karti hai:

    import execution_gateway as gw

    res = gw.execute_signal(strategy_id, symbol, side, lots, lot_size,
                            sec_id, trad_sym, mode=mode, broker_name=bname,
                            tag="RSI", log=log.info)
    if res["ok"]: ...position khuli...

    res = gw.execute_exit(strategy_id, symbol, sec_id, trad_sym, qty,
                          entry_side="BUY", mode=mode, broker_name=bname,
                          reason="RSI_MIDLINE_EXIT", tag="RSI", log=log.info)
    if res["status"] == "skipped_flat": ...already flat at broker — state saaf...

Kya milta hai (har strategy ko, bina yaad rakhe):
  ENTRY — no-premium skip (TRAP #1 ₹0-guard), strategy_safety.gate_entry()
  (poora RMS: daily-loss/drawdown/liquidity/max-premium/concentration/capital/
  broker-funds, fail-closed), RMS default per-instrument SL tags, size-down qty,
  smart_order.execute() (marketable-limit, chase, order_store recording).
  EXIT — fresh pre-exit flat-check (broker_sync.is_flat_fresh, strategy-aware —
  Task 5: doosri strategy ka same-contract lot MERI flat-check ko confuse nahi
  karta), phir smart_order.execute(is_exit=True) (extra chase rounds).

mode="backtest" — koi broker/order/order_store nahi. Sirf deterministic,
config-driven checks (abhi: per-index max-premium cap). Backtest engine isse
tabhi lagata hai jab uske cfg mein apply_live_risk_rules=true ho (default OFF
— purane backtest results silently change nahi hote). Baaki live-state checks
(drawdown/concentration/broker-funds) backtest mein by-design skip hain —
wo aaj ke live order_store/balance se aate hain, historical simulation se
nahi. Jab tak backtest apna simulated-capital state maintain nahi karta,
unhe "chalana" jhooth hota (dekho ADR-003).

Return shape (dono functions):
  {"ok": bool, "status": str, "reason": str, "order_id": ..., "price": float|None, "qty": int}
  status: "filled"|"paper"|"pending" (order gaya) | "blocked" (RMS) |
          "skipped" (no premium / contract issue) | "skipped_flat" (exit:
          already flat at broker) | "rejected"|"failed"
"""

import risk_gate
import smart_order
import strategy_safety
from brokers import get_broker


def _result(ok, status, reason="", order_id=None, price=None, qty=0):
    return {"ok": ok, "status": status, "reason": reason,
            "order_id": order_id, "price": price, "qty": qty}


def _instrument_for(seg):
    return "options" if seg == "NSE_FNO" else "equity"


def execute_signal(strategy_id, symbol, side, lots, lot_size, sec_id, trad_sym,
                   seg="NSE_FNO", mode="paper", broker_name=None, tag="",
                   source="strategy", instrument=None, extra_tags=None,
                   group_id="", product=None, gate=True, est_price=None,
                   buffer_bps=10, sl_type_override=None, own_exit=False, log=print):
    """Ek ENTRY leg — RMS-gated, order_store-recorded. `lots * lot_size` = qty
    (size-down ho sakta hai — RETURNED qty hi state mein rakho, apna calculation
    nahi). `gate=False` sirf protective legs ke liye (hedge BUY — jo SELL ke
    saath jaana hi chahiye; use RMS se block karna naked SELL chhod deta).

    `own_exit=True`: strategy apna exit khud manage karta hai (jaise ORB-family —
    spot-ATR stop / premium tp-sl in-process) — is entry pe global RMS default
    per-instrument SL profile (aggressive-trailing wagera) NAHI lagana. RMS ka
    outer safety net (daily-loss cap + 3:15 EOD squareoff) phir bhi lagta hai
    (wo per-position tag pe depend nahi karta). #63."""
    qty = int(lots) * int(lot_size or 1)
    instrument = instrument or _instrument_for(seg)

    if mode == "backtest":
        return _backtest_gate(strategy_id, symbol, qty, est_price, seg)

    try:
        bname = str(broker_name or risk_gate.default_broker() or "dhan").lower()
        broker = get_broker(bname)
    except Exception as e:
        return _result(False, "failed", f"broker init failed: {e}")

    # Premium/price estimate — gate_entry ko chahiye (max-premium/concentration/
    # capital sab est_price pe chalte hain). Na mile to entry SKIP (₹0 record
    # kabhi nahi, TRAP #1). gate=False path pe pre-fetch NAHI hota — smart_order.
    # execute apna marketable_price khud leta hai aur no-price pe khud skip
    # karta hai (double REST lookup bachta hai, purana call-count preserved).
    if gate and (not est_price or est_price <= 0):
        est_price, _psrc = smart_order.marketable_price(side, sec_id, seg, broker, buffer_bps)
        if not est_price or est_price <= 0:
            log(f"[GATEWAY] {symbol} — no price/premium (rate-limit?) — entry skipped")
            return _result(False, "skipped", "no_premium")

    tags = list(extra_tags or [])
    if gate:
        gate_ok, gated_qty, gate_reason = strategy_safety.gate_entry(
            strategy_id, symbol, int(lots), int(lot_size or 1), est_price,
            side=side, sec_id=sec_id, seg=seg, mode=mode, broker=broker, log=log)
        if not gate_ok:
            log(f"[GATEWAY] [SKIP] {symbol} entry blocked — {gate_reason}")
            # price = est_price: caller ko blocked-row recording (CAPITAL_BLOCKED
            # ghost row, universe pattern) ke liye chahiye hota hai
            return _result(False, "blocked", gate_reason, price=est_price)
        if gated_qty and gated_qty != qty:
            log(f"[GATEWAY] [SIZE-DOWN] {symbol} qty {qty} -> {gated_qty} ({gate_reason})")
            qty = gated_qty

        # RMS default per-instrument SL tags — SIRF gated (main) entries pe.
        # gate=False = protective leg (hedge BUY): uspe default SL tag lagana
        # pos_monitor se hedge ko independently SL-out karwa deta (hedge apne
        # SELL ke saath hi jaana chahiye, akele nahi). Best-effort — profile
        # off ho to khaali.
        # sl_type_override lets a caller (e.g. webhook, whose config carries a
        # per-signal SL-type selector) pick the RMS SL profile; None = global.
        # own_exit strategies opt OUT — their own in-process exit governs, only the
        # RMS outer net (daily cap + 3:15 EOD, both tag-independent) still applies (#63).
        if own_exit:
            log(f"[GATEWAY] {symbol} — own_exit: strategy manages its own SL/target "
                f"(no RMS default trailing; daily-cap + 3:15 EOD still apply)")
        else:
            try:
                _sl = (risk_gate.default_instrument_sl_tags(strategy_id, symbol,
                                                            mode_override=sl_type_override)
                       if sl_type_override is not None
                       else risk_gate.default_instrument_sl_tags(strategy_id, symbol))
                tags += [t for t in _sl if t not in tags]
            except Exception:
                pass

    res = smart_order.execute(side, symbol, sec_id, seg, qty, trad_sym, mode, broker,
                              buffer_bps=buffer_bps, log=log, tag=tag,
                              source=source, strategy=strategy_id,
                              instrument=instrument, broker_name=bname,
                              group_id=group_id, extra_tags=tags,
                              product=product, is_exit=False)
    return _result(bool(res.get("ok")), res.get("status") or ("failed" if not res.get("ok") else "paper"),
                   res.get("reason", ""), res.get("order_id"), res.get("price"), qty)


def execute_exit(strategy_id, symbol, sec_id, trad_sym, qty, entry_side=None,
                 exit_side=None, seg="NSE_FNO", mode="paper", broker_name=None,
                 tag="", source="strategy", instrument=None, reason=None,
                 extra_tags=None, group_id="", product=None, flat_check=True,
                 buffer_bps=10, log=print):
    """Ek EXIT leg. `reason` = exit-reason tag (e.g. "RSI_MIDLINE_EXIT",
    "EOD_315_SQUAREOFF") — Completed Trades ka Exit Reason column isi se aata
    hai, kabhi mat chhodo. status="skipped_flat" ka matlab: broker pe already
    flat tha (manual close/SL) — caller apna in-memory state saaf kare, order
    NAHI gaya (phantom opposite-position guard, TRAP #44/#73/#75)."""
    if exit_side is None:
        if entry_side is None:
            return _result(False, "failed", "need entry_side or exit_side")
        exit_side = "SELL" if str(entry_side).upper() == "BUY" else "BUY"
    instrument = instrument or _instrument_for(seg)

    if mode == "backtest":
        return _result(True, "paper", "backtest exit — no live checks", qty=qty)

    try:
        bname = str(broker_name or risk_gate.default_broker() or "dhan").lower()
        broker = get_broker(bname)
    except Exception as e:
        log(f"[GATEWAY] [EXIT] broker init failed ({e}) — {symbol} exit skipped "
            f"(pos_monitor SL/EOD will still protect it via order_store)")
        return _result(False, "failed", f"broker init failed: {e}")

    # Fresh pre-exit flat-check (live only) — manual close ke baad apna exit
    # order ek phantom OPPOSITE position khol deta hai (1 trade -> 3 + tax).
    # strategy_id pass hota hai (Task 5): same contract mein DOOSRI strategy ka
    # lot broker-net ko non-zero rakhta hai — order_store cross-check ke bina
    # "mera exit fail hua" dikh kar over-sell ho jaata.
    if flat_check and mode == "live":
        try:
            import broker_sync
            if broker_sync.is_flat_fresh(bname, trad_sym, str(sec_id), strategy_id=strategy_id):
                log(f"[GATEWAY] [FLAT-CHECK] {trad_sym} already flat at broker (manual "
                    f"close?) — skipping exit order to avoid a phantom opposite position")
                return _result(True, "skipped_flat", "already flat at broker", qty=qty)
        except Exception as fe:
            log(f"[GATEWAY] [FLAT-CHECK] pre-exit check failed ({fe}) — proceeding (fail-open)")

    tags = list(extra_tags or [])
    if reason and reason not in tags:
        tags.insert(0, reason)

    res = smart_order.execute(exit_side, symbol, sec_id, seg, qty, trad_sym, mode, broker,
                              buffer_bps=buffer_bps, log=log, tag=tag,
                              source=source, strategy=strategy_id,
                              instrument=instrument, broker_name=bname,
                              group_id=group_id, extra_tags=tags,
                              product=product, is_exit=True)
    return _result(bool(res.get("ok")), res.get("status") or ("failed" if not res.get("ok") else "paper"),
                   res.get("reason", ""), res.get("order_id"), res.get("price"), qty)


# ─────────────────────────────────────────────────────────────────────────────
# Backtest mode — deterministic, config-driven checks only (ADR-003)
# ─────────────────────────────────────────────────────────────────────────────

def _backtest_gate(strategy_id, symbol, qty, est_price, seg):
    """Sirf wo checks jo pure config se deterministic hain (koi live state
    nahi): abhi per-index max-premium cap. Backtest engine mein entry se pehle
    call hota hai jab cfg.apply_live_risk_rules=true (default OFF)."""
    prem = float(est_price or 0)
    if seg == "NSE_FNO" and prem > 0:
        try:
            cap = risk_gate.max_premium_cap_for(symbol)
        except Exception:
            cap = None
        if cap is not None and prem > cap:
            return _result(False, "blocked",
                           f"premium ₹{prem:.2f} > max ₹{cap:.0f} cap for {symbol} (backtest, live rule)")
    return _result(True, "paper", "backtest gate passed", qty=qty)
