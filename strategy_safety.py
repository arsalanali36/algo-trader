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
      3. check_contract_liquidity — live market-depth liquidity (NSE_FNO only)
      4. check_concentration — cross-strategy exposure cap per underlying
      5. check_capital   — capital_rs allocation (size-down if configured)
      6. check_broker_funds — LIVE mode only, real broker balance vs needed ₹

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
        # `broker` here is sometimes a broker NAME string (range_trader/webhook
        # callers don't always wrap it), sometimes a broker OBJECT (webhook_
        # executor passes one from its own _broker() factory) — risk_gate's
        # live-balance lookup needs a plain name string either way.
        broker_name = broker if isinstance(broker, str) else (broker.name() if hasattr(broker, "name") else None)
        g_blocked, g_reason, _g_hard = risk_gate.gating_status(strategy_id, mode=mode, broker=broker_name)
        if g_blocked:
            return False, 0, g_reason

        dd_ok, dd_reason = risk_gate.check_drawdown()
        if not dd_ok:
            return False, 0, dd_reason

        if sec_id and seg == "NSE_FNO" and risk_gate.liquidity_filter_enabled(strategy_id):
            liq_ok, liq_reason, _liq_details = check_contract_liquidity(sec_id, lot_size, log=log)
            if not liq_ok:
                return False, 0, liq_reason

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


def check_contract_liquidity(sec_id, lot_size, now=None, log=print):
    """Live market-depth liquidity gate for ONE option CONTRACT (not the
    underlying stock — a specific strike can be thin even on a liquid name,
    and vice versa). User-specified thresholds (2026-06-28), checked with an
    OR-style "any 2 of 3" pass rule rather than requiring all three — a
    contract that's a bit weak on ONE dimension but solid on the other two is
    still tradeable; requiring every single check is what made the old
    static stock whitelist (`universe.LIQUID_PREMIUM`) so restrictive:

      - spread_pct  = (best_ask - best_bid) / ltp * 100   — PASS if <= 1.5
      - volume_lots = day_cumulative_volume / lot_size     — PASS if >= 500
        (>= 50 before 9:45 AM IST — cumulative volume is naturally low early
        in the session; a fixed 500 floor would reject every single contract
        in the first 15 minutes regardless of real liquidity)
      - oi_lots     = open_interest / lot_size             — PASS if >= 300

    Data source: `dhan_feed.LIVE` (WebSocket Full packet — has bid/ask/ltp/
    oi/volume already, zero extra Dhan calls) first; falls back to a direct
    `/v2/marketfeed/quote` REST call (rate-limited, "ltp" priority) if the
    feed has no data yet for this contract (cold subscribe, or the feed
    itself is degraded — see LESSONS.md TRAP #12, the feed's live-tick
    reliability wasn't fully end-to-end verified during market hours).

    If NEITHER source has live depth data at all (not "thin", just genuinely
    unavailable), this FAILS OPEN (returns ok=True) with a loud warning —
    this is a new liquidity *enhancement*, not a P&L-correctness guard like
    the ₹0-fill tripwire (TRAP #1); blocking every trade because a brand-new
    data path is briefly unavailable would be a worse outcome than letting
    a trade through unchecked once. A contract WITH data that's simply thin
    still fails closed (2-of-3 rule above) — that distinction is the whole
    point of this fail-open-on-NO-data vs fail-closed-on-BAD-data split.

    Returns (ok: bool, reason: str, details: dict) — details always present
    (even on a pass, or when no data at all) for logging."""
    import dhan_feed
    q = dhan_feed.get_quote(sec_id)
    bid, ask, ltp = q.get("bid"), q.get("ask"), q.get("ltp")
    volume, oi = q.get("volume"), q.get("oi")

    if not (bid and ask and ltp):
        rq = _rest_quote_fallback(sec_id)
        if rq:
            bid, ask, ltp = rq.get("bid"), rq.get("ask"), rq.get("ltp")
            volume, oi = rq.get("volume"), rq.get("oi")

    try:
        bid, ask, ltp = float(bid or 0), float(ask or 0), float(ltp or 0)
    except (TypeError, ValueError):
        bid = ask = ltp = 0.0

    if not (bid and ask and ltp):
        log(f"[LIQUIDITY] no live market-depth data for sec_id={sec_id} — "
            f"failing OPEN (data unavailable, not confirmed illiquid)")
        return True, "", {"data_available": False}

    spread_pct = ((ask - bid) / ltp) * 100
    spread_ok = spread_pct <= 1.5

    if now is None:
        import dhan_master
        now = dhan_master.ist_now()
    vol_floor = 50 if (now.hour, now.minute) < (9, 45) else 500
    volume_lots = (float(volume or 0)) / lot_size if lot_size else 0
    volume_ok = volume_lots >= vol_floor

    oi_lots = (float(oi or 0)) / lot_size if lot_size else 0
    oi_ok = oi_lots >= 300

    details = {
        "data_available": True,
        "spread_pct": round(spread_pct, 2), "spread_ok": spread_ok,
        "volume_lots": round(volume_lots, 1), "volume_ok": volume_ok, "vol_floor": vol_floor,
        "oi_lots": round(oi_lots, 1), "oi_ok": oi_ok,
    }
    passed = sum([spread_ok, volume_ok, oi_ok])
    if passed >= 2:
        return True, "", details
    reason = (f"illiquid contract — only {passed}/3 liquidity checks passed "
              f"(spread={spread_pct:.2f}% vol={volume_lots:.0f}L oi={oi_lots:.0f}L, "
              f"need spread<=1.5% / vol>={vol_floor}L / oi>=300L, any 2 of 3)")
    return False, reason, details


def _rest_quote_fallback(sec_id, seg="NSE_FNO"):
    """One-shot REST fallback for liquidity data when dhan_feed has nothing
    yet for this contract. Returns {'bid','ask','ltp','volume','oi'} or None.
    Best-effort — any failure (network, unexpected response shape, rate
    limit) just returns None so the caller's fail-open path takes over."""
    try:
        import json
        import requests
        from pathlib import Path
        import dhan_rate_limiter as _rl

        cfg_file = Path(__file__).resolve().parent / "data" / "config.json"
        cfg = json.loads(cfg_file.read_text())
        headers = {"access-token": cfg["jwt_token"], "client-id": cfg["client_id"],
                   "Content-Type": "application/json"}
        _rl.acquire("ltp")
        r = requests.post("https://api.dhan.co/v2/marketfeed/quote",
                          json={seg: [int(sec_id)]}, headers=headers, timeout=5)
        if r.status_code == 429:
            _rl.note_429()
        if r.status_code != 200:
            return None
        node = (r.json() or {}).get("data", {}).get(seg, {}) or {}
        d = node.get(str(sec_id)) or next(iter(node.values()), None)
        if not d:
            return None
        depth = d.get("depth") or {}
        buy0 = (depth.get("buy") or [{}])[0]
        sell0 = (depth.get("sell") or [{}])[0]
        return {
            "bid": buy0.get("price"), "ask": sell0.get("price"),
            "ltp": d.get("last_price"), "volume": d.get("volume"), "oi": d.get("oi"),
        }
    except Exception:
        return None


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
