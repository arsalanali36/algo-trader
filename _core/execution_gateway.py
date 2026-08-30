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
                   buffer_bps=10, sl_type_override=None, log=print):
    """Ek ENTRY leg — RMS-gated, order_store-recorded. `lots * lot_size` = qty
    (size-down ho sakta hai — RETURNED qty hi state mein rakho, apna calculation
    nahi). `gate=False` sirf protective legs ke liye (hedge BUY — jo SELL ke
    saath jaana hi chahiye; use RMS se block karna naked SELL chhod deta)."""
    qty = int(lots) * int(lot_size or 1)
    instrument = instrument or _instrument_for(seg)

    if mode == "backtest":
        return _backtest_gate(strategy_id, symbol, qty, est_price, seg)

    # DEFAULT trading-day guard (weekend + NSE holiday) — no strategy should OPEN a
    # position when the market is closed. This is the safety net so EVERY current and
    # future strategy gets it without remembering (a strategy's own loop-gate is the
    # first line; this is the last). Backtest is exempt (replays real trading days).
    # Exits are NOT gated (a held position can't be closed on a holiday anyway).
    # VRP condor once rolled on a Saturday for want of this — see market_calendar.
    try:
        import market_calendar as _mc
        if not _mc.is_trading_day():
            log(f"[GATEWAY] [SKIP] {symbol} entry — market band (weekend/holiday), koi entry nahi")
            return _result(False, "skipped", "market_closed")
    except Exception:
        pass   # calendar unavailable → fail-open (loop-gate still applies)

    # DEFAULT safe-mode guard — "broker bharosemand nahi hai" wala beech ka state.
    # System ke paas pehle sirf chal-raha/band the; token mar jaane pe wo NAYI live
    # entry try karta rehta tha jabki USI waqt exit complete nahi ho sakta tha
    # (_do_squareoff "leaving position open, will retry" loop). Ye us combination
    # ko rokta hai — risk badhna band, purani risk band karna jaari.
    #
    #   LIVE ENTRY -> band | EXIT -> jaari (alag function) | PAPER -> jaari
    #
    # PAPER ko chhuna sabse aasan galti hoti: user ka data-collection lane (14+
    # paper strategies + option-chain lake) usi pe zinda hai. Isliye gate sirf
    # mode=="live" pe. FAIL-OPEN — is guard me hi dikkat ho to trading na ruke.
    if mode == "live":
        try:
            import safe_mode as _sm
            _blk, _why = _sm.blocks_live_entry()
            if _blk:
                log(f"[GATEWAY] [SKIP] {symbol} LIVE entry — SAFE MODE ({_why}); "
                    f"exit + paper chalu hain")
                return _result(False, "blocked", f"safe_mode:{_why}", price=est_price)
        except Exception:
            pass   # fail-open

    # DEFAULT leg-collision guard (broker fungibility) — the MAIN GATE. A LIVE
    # strategy entry must NEVER land on a contract ANOTHER strategy already holds
    # open at the broker: option positions are fungible by CONTRACT, not strategy,
    # so a BUY on another strategy's SHORT (or vice-versa) silently nets/closes it
    # → that strategy's hedged structure breaks (the "dusri strategy ka leg square
    # off ho gaya" blunder). This is the universal safety net so EVERY current AND
    # future strategy is protected without wiring — same pattern as the trading-day
    # guard above. Strategies that resolve by offset SHOULD pre-shift the strike
    # (leg_collision.clear_leg / compute_hedge_target avoid=) so they still trade a
    # neighbouring strike; this gate is the backstop that REFUSES a colliding leg
    # outright when they didn't. LIVE-vs-LIVE only (paper never reaches the broker);
    # strategy-sourced entries only (a manual/trigger order is the user's own call).
    # Same-side (SELL on an existing SELL) is refused too — technically nets fine
    # but the shared fungible lot makes per-strategy exit/MTM accounting ambiguous.
    if mode == "live" and source == "strategy":
        _shared = False
        try:
            import leg_collision
            _shared = str(sec_id) in leg_collision.occupied_sec_ids(strategy_id)  # live-only, excludes self
        except Exception:
            _shared = False   # collision module unavailable → fail-open (strategy pre-shift still applies)
        if _shared:
            # NOTE: the block decision is OUTSIDE the try above so a logging failure
            # (e.g. a console that can't encode a char) can never silently disable
            # the gate — the refuse must happen regardless of whether the log prints.
            try:
                log(f"[GATEWAY] [SKIP] {trad_sym} -- contract already open for another LIVE "
                    f"strategy (broker would net the two -> broken structure); entry refused")
            except Exception:
                pass
            return _result(False, "blocked", "leg_collision")

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
            # Record the would-be trade (entry-intent + is-second ka premium) so a
            # later OFFLINE replay can price its hypothetical P&L — the only way to
            # answer "block na hote to kya hota" (blocked signal order_store me
            # kabhi nahi jaata). Ye SINGLE choke-point hai jahan se har strategy ki
            # entry block hoti hai → recording yahan = abhi + future ki har strategy
            # apne-aap covered. FAIL-SAFE: recording ki koi bhi dikkat order-decision
            # ko kabhi na tode (skipped_store khud swallow karta hai; import bhi try
            # me — VPS deploy-drift pe gate zinda rahe).
            try:
                import skipped_store
                skipped_store.record_skip(
                    strategy=strategy_id, symbol=symbol, side=side,
                    trad_sym=trad_sym, sec_id=sec_id,
                    intended_lots=lots, lot_size=lot_size,
                    entry_premium=est_price, block_reason=gate_reason, mode=mode)
            except Exception as _e:
                log(f"[GATEWAY] skip-record failed (non-fatal): {_e}")
            # Optional Telegram alert (default off) — "block na hota to entry aati"
            # ka pata rahe. Fail-safe: apna hi swallow karta hai.
            try:
                import telegram_notify
                telegram_notify.notify_blocked(strategy_id, trad_sym, side,
                                               reason=gate_reason, mode=mode)
            except Exception:
                pass
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
    # Telegram ENTRY alert — order gaya (filled/paper/pending). Config-gated
    # (default sirf live). Fail-safe + non-blocking (background thread).
    if res.get("ok"):
        try:
            import telegram_notify
            telegram_notify.notify_entry(strategy_id, trad_sym, side, qty,
                                         price=res.get("price"), mode=mode,
                                         status=res.get("status") or "", tag=tag)
        except Exception:
            pass
    # Safe-mode ko batao ki asli order gaya ya nahi — lagatar N live failures
    # (auth/margin/broker down) khud safe mode trip kar denge. PAPER counter ko
    # chhuta nahi (paper fail broker ke baare me kuch nahi kehta).
    try:
        import safe_mode as _sm
        _sm.note_order_result(mode, bool(res.get("ok")), res.get("reason", ""))
    except Exception:
        pass
    return _result(bool(res.get("ok")), res.get("status") or ("failed" if not res.get("ok") else "paper"),
                   res.get("reason", ""), res.get("order_id"), res.get("price"), qty)


def _broker_exit_product(broker, broker_name, trad_sym, sec_id, log=print):
    """The product (MIS/NRML) the broker ACTUALLY holds for this contract, so a
    LIVE close nets the open leg instead of opening a fresh opposite one.

    Zerodha tracks MIS and NRML as SEPARATE positions — a close order in the
    WRONG product does NOT net the open leg, it opens a NEW position (TRAP #178:
    an NRML leg + a default-MIS close → old leg stays open + fresh MIS leg; the
    exact same shape hit the payoff combined SL/Target basket-exit — TRAP #181).
    `_do_squareoff`/`_close_position_impl` (trader_dashboard) already resolve this
    via `_broker_position_product`; this is the SAME logic for the gateway exit
    path (basket-exit / hedged-trader exits) which passed no product. Kept here
    (not imported) because _core must not import the UI entrypoint. Returns None
    on any failure → smart_order's default is preserved (no MIS-leg regression)."""
    try:
        if str(broker_name).lower() == "kite" and broker is not None and hasattr(broker, "resolve_symbol"):
            ksym = broker.resolve_symbol(trad_sym, sec_id)   # forward-only exact match (TRAP #13)
            if ksym:
                for p in (broker.positions_detailed() or []):
                    if p.get("kite_sym") == ksym and p.get("product"):
                        return str(p.get("product")).upper()
    except Exception as e:
        log(f"[GATEWAY] [EXIT-PRODUCT] {trad_sym} broker product read failed ({e}) — default")
    return None


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

    # ── Cross-engine exit dedup (LESSONS #176) — pos_monitor's _do_squareoff and
    # this basket-exit path both run as threads in the algo-monitor process and
    # can each fire a close on the same (strategy, contract, side) within seconds
    # (broker fill hasn't reflected → the flat-check above can't catch it). Win an
    # instant in-process claim or skip, so only ONE close order lands. Released
    # below if placement fails, so a legit retry isn't blocked.
    _claimed = False
    try:
        import exit_claim
        if not exit_claim.claim(strategy_id, sec_id, exit_side, mode):
            log(f"[GATEWAY] [DUP-GUARD] {trad_sym} {exit_side} exit already in-flight "
                f"for {strategy_id} — skipping duplicate close (phantom-leg guard)")
            return _result(True, "skipped_dup", "exit already in-flight for this contract", qty=qty)
        _claimed = True
    except Exception:
        _claimed = False

    tags = list(extra_tags or [])
    if reason and reason not in tags:
        tags.insert(0, reason)

    # Product-match on a LIVE close (TRAP #178/#181) — if the caller didn't pin a
    # product (basket-exit / payoff SL-target / hedged-trader exits all pass None),
    # read the broker's REAL product for this open leg so the exit NETS it. Without
    # this a NRML leg gets a default-MIS close → position doesn't close, a fresh MIS
    # position opens. None result → smart_order default unchanged (MIS legs safe).
    if mode == "live" and not product:
        product = _broker_exit_product(broker, bname, trad_sym, sec_id, log=log)

    res = smart_order.execute(exit_side, symbol, sec_id, seg, qty, trad_sym, mode, broker,
                              buffer_bps=buffer_bps, log=log, tag=tag,
                              source=source, strategy=strategy_id,
                              instrument=instrument, broker_name=bname,
                              group_id=group_id, extra_tags=tags,
                              product=product, is_exit=True)
    if _claimed and not res.get("ok"):
        # order didn't place — free the claim so the next cycle can retry
        try:
            import exit_claim
            exit_claim.release(strategy_id, sec_id, exit_side, mode)
        except Exception:
            pass
    # Telegram EXIT alert — asli exit order gaya. Config-gated (default live).
    # skipped_flat yahan tak nahi aata (upar hi return ho jaata) — sirf real exit.
    if res.get("ok"):
        try:
            import telegram_notify
            telegram_notify.notify_exit(strategy_id, trad_sym, exit_side, qty,
                                        price=res.get("price"), mode=mode,
                                        reason=reason or "", tag=tag)
        except Exception:
            pass
    return _result(bool(res.get("ok")), res.get("status") or ("failed" if not res.get("ok") else "paper"),
                   res.get("reason", ""), res.get("order_id"), res.get("price"), qty)


def _confirm_shorts_flat(shorts, log=print, timeout_s=4.0, poll_s=0.7):
    """Best-effort: wait (bounded) for the just-fired SHORT buy-to-close legs to
    read flat at the broker before the wings are removed. FAIL-OPEN — a check
    failure or timeout just proceeds (the SELL-first ORDERING is the primary
    guard; this only tightens the window). LIVE legs only."""
    import time as _t
    try:
        import broker_sync
    except Exception:
        return
    deadline = _t.time() + max(0.0, float(timeout_s))
    pending = [l for l in shorts if str(l.get("mode", "")).lower() == "live"]
    while pending and _t.time() < deadline:
        still = []
        for l in pending:
            try:
                bname = str(l.get("broker_name") or risk_gate.default_broker() or "dhan").lower()
                if not broker_sync.is_flat_fresh(bname, l.get("trad_sym"), str(l.get("sec_id")),
                                                 strategy_id=l.get("strategy")):
                    still.append(l)
            except Exception:
                pass          # can't verify -> don't block, drop from pending
        pending = still
        if pending:
            _t.sleep(max(0.1, float(poll_s)))
    if pending:
        log(f"[GATEWAY] [BASKET-EXIT] {len(pending)} short leg(s) not confirmed flat "
            f"within {timeout_s}s — removing wings anyway (ordering guard still held)")


def execute_basket_exit(legs, reason="GROUP_EXIT", log=print, confirm_between=True):
    """ORDERED square-off of a (possibly hedged) basket. Closes the SHORT
    (SELL-entry) legs FIRST via buy-to-close — which only REDUCES margin — then
    the LONG / protective-wing (BUY-entry) legs via sell-to-close.

    Removing the wings before the shorts turns a defined-risk structure momentarily
    NAKED, spiking the margin requirement, so the broker rejects the remaining
    exit orders ("order jaate nahi hai"). SELL-first ordering never increases
    nakedness at any intermediate step, so it can't hit that wall.

    `legs`: normalized dicts, each:
        {strategy, symbol, sec_id, trad_sym, qty, entry_side ('SELL'|'BUY'),
         mode, group_id, source, seg, broker_name(optional)}
    Returns [(leg, result), ...] in the order fired (shorts then wings)."""
    shorts = [l for l in legs if str(l.get("entry_side", "")).upper() == "SELL"]
    wings  = [l for l in legs if str(l.get("entry_side", "")).upper() == "BUY"]
    results = []

    def _fire_one(l):
        try:
            r = execute_exit(
                l.get("strategy") or "manual",
                l.get("symbol"),
                str(l.get("sec_id")),
                l.get("trad_sym"),
                abs(int(float(l.get("qty") or 0))),
                entry_side=str(l.get("entry_side") or "SELL").upper(),
                seg=l.get("seg") or "NSE_FNO",
                mode=str(l.get("mode") or "paper").lower(),
                broker_name=l.get("broker_name"),
                group_id=l.get("group_id") or "",
                source=l.get("source") or "strategy",
                reason=reason, log=log)
        except Exception as e:                 # one leg's failure must not strand the rest
            log(f"[GATEWAY] [BASKET-EXIT] leg exit FAIL {l.get('trad_sym')}: {e}")
            r = _result(False, "failed", f"basket leg exit: {e}",
                        qty=abs(int(float(l.get("qty") or 0))))
        results.append((l, r))
        return r

    for l in shorts:                       # Phase 1 — shorts buy-to-close (margin drops)
        _fire_one(l)
    if confirm_between and wings and shorts:   # Phase 1.5 — don't strip the hedge until shorts are (best-effort) flat
        _confirm_shorts_flat(shorts, log=log)
    for l in wings:                        # Phase 2 — wings sell-to-close
        _fire_one(l)
    return results


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
