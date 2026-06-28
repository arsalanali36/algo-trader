"""
strategy_safety.py — THE shared "backoffice" layer for every strategy that
sells naked options. Every safety/RMS concern that used to be copy-pasted
separately into range_trader.py / webhook_executor.py / universe_trader.py
(and silently drifted apart — see LESSONS.md TRAP #15) lives here ONCE.

A brand-new strategy should need exactly two calls:

    ok, qty, reason = gate_entry(strategy_id, symbol, lots, lot_size, est_price,
                                  side="SELL", sec_id=sec_id, mode=mode, broker=broker)
    if not ok:
        log(f"[SKIP] {reason}"); return

    ... place the SELL leg however this strategy normally places orders ...

    h_sec_id, h_trad_sym, h_lot = compute_hedge_target(
        strategy_id, symbol, spot_price, option_type, sell_offset, quote_fn=...)
    if h_sec_id:
        ... place a BUY of (h_sec_id, h_trad_sym, h_lot) the same way ...

Placement itself is deliberately NOT done here — different strategies place
orders differently today (range_trader: raw Dhan REST; webhook_executor /
universe_trader: smart_order.execute() via the brokers/ abstraction). This
module only answers "should this trade happen, and at what hedge strike" —
the same answer regardless of how the order actually gets sent.

See CLAUDE.md "Building a new strategy" for the full checklist (this module
covers steps 2 and 4 of that checklist; order_store recording + broker-class
usage + smart_order's order-confirm fix are the other steps).
"""


def gate_entry(strategy_id, symbol, lots, lot_size, est_price, side="SELL",
               sec_id=None, seg="NSE_FNO", mode="paper", broker=None,
               log=print):
    """Pre-trade RMS gate for any entry (option SELL or BUY, equity, etc.) —
    `side` matters to check_concentration/check_capital (e.g. SELL legs get
    a margin multiplier BUY legs don't).

    Runs, in order (first block wins):
      1. gating_status   — cheap pre-check, no LTP call (per-strategy hard
                            block: daily-loss breached / capital fully used)
      2. check_drawdown  — global daily drawdown circuit breaker
      3. check_concentration — cross-strategy exposure cap per underlying
      4. check_capital   — capital_rs allocation (size-down if configured)
      5. check_broker_funds — LIVE mode only, real broker balance vs needed ₹

    Returns (proceed: bool, qty: int, reason: str). `qty` may be SIZED DOWN
    from `lots * lot_size` if capital_mode="size_down" let it fit a smaller
    size instead of rejecting outright — always use the RETURNED qty, not
    your original lots*lot_size, when placing the order.

    Any exception inside an RMS check is treated as a BLOCK, never a
    silent pass-through (fail-closed — a check that can't run must never
    look the same as a check that ran and approved)."""
    import risk_gate
    qty = lots * lot_size
    try:
        g_blocked, g_reason, _g_hard = risk_gate.gating_status(strategy_id)
        if g_blocked:
            return False, 0, g_reason

        dd_ok, dd_reason = risk_gate.check_drawdown()
        if not dd_ok:
            return False, 0, dd_reason

        price = float(est_price or 0)
        if price > 0:
            conc_ok, conc_reason = risk_gate.check_concentration(symbol, qty, price, side=side)
            if not conc_ok:
                return False, 0, conc_reason

        cap_ok, cap_reason = risk_gate.check_capital(strategy_id, qty, price, side=side, sec_id=sec_id, seg=seg)
        if not cap_ok:
            fit_lots = 0
            if risk_gate.capital_mode(strategy_id) == "size_down":
                fit_lots = risk_gate.sized_lots(strategy_id, lots, lot_size, price, side=side, sec_id=sec_id, seg=seg)
            if fit_lots > 0:
                return True, fit_lots * lot_size, f"sized_down: {cap_reason}"
            return False, 0, cap_reason

        if mode == "live" and broker is not None:
            fund_ok, fund_reason = risk_gate.check_broker_funds(broker, qty * price)
            if not fund_ok:
                return False, 0, fund_reason
    except Exception as e:
        return False, 0, f"risk gate check failed (fail-closed): {e}"

    return True, qty, ""


def compute_hedge_target(strategy_id, symbol, spot_price, option_type, sell_offset,
                          quote_fn=None, min_strikes_override=None, max_search=15, log=print):
    """Resolve the auto-hedge BUY contract for a SELL leg that already went
    through (or is about to go through) gate_sell_entry — does NOT place
    anything, just answers "which contract, if any".

    Reads risk_gate.hedge_config(strategy_id): (min_strikes, max_premium_rs).
    Hedge is OFF if both are unset (returns (None, None, None) immediately —
    cheap, no contract lookups at all in the common off case).
    `min_strikes_override`, if given, replaces the RMS-tab min_strikes floor
    (e.g. a legacy per-strategy config field that predates the shared RMS tab
    knob) — max_premium_rs always comes from risk_gate regardless, since
    no legacy field ever had that knob.

    min_strikes is a FLOOR (at least this many strikes further OTM than the
    sold leg). If max_premium_rs is also set, keeps walking further OTM past
    that floor (via `quote_fn(sec_id) -> float|None` for each candidate's
    premium) until one is <= max_premium_rs — cheaper insurance, useful for
    NIFTY where strikes sit close together. Whichever lands FURTHER OTM wins;
    if quote_fn is None or every lookup fails, falls back to the min_strikes
    floor (never blocks/skips the hedge just because premium couldn't be
    checked — only a contract-resolve failure does that).

    Returns (sec_id, trad_sym, lot_size) or (None, None, None) if hedge is
    off, or the contract couldn't be resolved at all (logged either way)."""
    import risk_gate
    import dhan_master

    min_strikes, max_premium = risk_gate.hedge_config(strategy_id)
    if min_strikes_override:
        min_strikes = int(min_strikes_override)
    if not min_strikes and not max_premium:
        return None, None, None

    offset = sell_offset + max(int(min_strikes or 0), 1)
    sec_id, trad_sym, lot_size = dhan_master.get_option_contract(symbol, spot_price, option_type, offset)

    if max_premium and quote_fn is not None:
        for _i in range(max_search):
            if not sec_id:
                break
            try:
                prem = quote_fn(sec_id)
            except Exception:
                prem = None
            if prem is not None and prem <= max_premium:
                break
            offset += 1
            sec_id, trad_sym, lot_size = dhan_master.get_option_contract(symbol, spot_price, option_type, offset)

    if not sec_id:
        log(f"[HEDGE] contract resolve failed for {symbol} {option_type} offset={offset} — hedge leg skipped")
        return None, None, None
    return sec_id, trad_sym, lot_size
