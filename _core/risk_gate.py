#!/usr/bin/env python3
"""
risk_gate.py — capital allocation gate (RMS Stage 1).

Idea: define how much capital (₹ notional, qty*price) each strategy is allowed
to have deployed at once, plus a global ceiling across all strategies. Before
any entry, `check_capital()` tells the caller whether it fits. If not, the
caller should NOT place a real order — instead record the would-be entry as a
"blocked" leg (status='blocked', tags=['CAPITAL_BLOCKED', ...]) so it's visible
in Orders & P&L instead of silently vanishing.

Config lives in nifty_config.json["_risk"]["global"/"per_strategy"]["capital_rs"]
— same dict shape trader_dashboard.py already uses for max_loss_pct/max_loss_rs
(see _risk_config() there). Strategy-specific overrides global; absent on both
= no cap (unlimited, current default behavior unchanged).

Capital-in-use is notional (qty*entry_price) summed over today's open positions
for that strategy — NOT real margin. Margin-aware sizing is Stage 2.
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TC_FILE = BASE_DIR / "nifty_config.json"


def _inr(n):
    """Format a ₹ amount in Indian digit grouping (2,00,000 — lakh/crore
    style), not Python's default international grouping (200,000). Every
    RMS block-reason string below is user-facing (shown directly in the
    dashboard's "Capital se Block hui Entries" table) — a plain ₹171878
    with no separators at all reads ambiguously as "1.7L or 17L?" (found
    2026-07-03, user asked for this explicitly after exactly that
    confusion)."""
    try:
        n = round(float(n))
    except (TypeError, ValueError):
        return str(n)
    neg = n < 0
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        last3, rest = s[-3:], s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        out = ",".join(parts) + "," + last3
    return ("-" if neg else "") + out


def _risk_cfg():
    try:
        cfg = json.loads(TC_FILE.read_text()) if TC_FILE.exists() else {}
    except Exception:
        cfg = {}
    rc = cfg.get("_risk") or {}
    return {"global": rc.get("global") or {}, "per_strategy": rc.get("per_strategy") or {}}


def default_broker():
    """Returns the globally configured default live broker (e.g. 'dhan', 'kite')."""
    return _risk_cfg().get("global", {}).get("default_broker", "dhan")


def _parse_hm(s, default=(15, 15)):
    """'15:15' -> (15, 15). Bad/blank input -> default."""
    try:
        h, m = str(s).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return (h, m)
    except Exception:
        pass
    return default


def exit_time_config():
    """SINGLE SOURCE OF TRUTH for intraday square-off + no-entry-after times.
    Har strategy (range/rsi/ema/universe) + webhook + pos_monitor_loop yahi se
    time uthaate hain — ab har file me alag hardcoded (15,15) nahi (RMS tab se
    editable). nifty_config["_risk"]["global"]: auto_squareoff_at / no_entry_after
    ("HH:MM" IST). Returns (squareoff_hm, no_entry_hm) as (H, M) tuples.

    no_entry_after ka gate hi "3:15 ke baad entry -> turant squareoff -> ₹50
    zabardasti brokerage" wale bug ko rokta hai (entry hi na ho)."""
    g = _risk_cfg().get("global", {})
    squareoff = _parse_hm(g.get("auto_squareoff_at", "15:15"))
    no_entry  = _parse_hm(g.get("no_entry_after", "15:15"))
    return squareoff, no_entry


def max_premium_config():
    """Per-index max option-premium entry cap (₹), user-requested 2026-07-07.
    A new option entry is BLOCKED if its per-unit premium exceeds the cap for
    that index. Rationale: an expensive premium (BANKNIFTY especially) moves a
    fixed-₹ SL's worth in seconds, so a ₹1,000 SL gets hit almost instantly —
    the user would rather just not enter such contracts. Caps are PER-INDEX
    because BANKNIFTY premiums run far larger than NIFTY's, so one shared number
    would either be too loose for NIFTY or too tight for BANKNIFTY. Blank / 0 /
    None on a key = no cap for that bucket (unlimited, current behaviour)."""
    g = _risk_cfg().get("global", {})
    def _f(key):
        try:
            v = g.get(key)
            if v in (None, "", 0, "0"):
                return None
            return float(v)
        except (TypeError, ValueError):
            return None
    return {
        "nifty":     _f("max_premium_nifty"),
        "banknifty": _f("max_premium_banknifty"),
        "stock":     _f("max_premium_stock"),
    }


def max_premium_cap_for(symbol):
    """Resolve the applicable per-index premium cap (₹) for an underlying root
    symbol, or None if no cap applies. NIFTY→nifty bucket, BANKNIFTY→banknifty
    bucket, everything else (stocks + FINNIFTY/SENSEX/etc)→stock bucket."""
    cfg = max_premium_config()
    s = (symbol or "").upper()
    if s == "NIFTY":
        return cfg["nifty"]
    if s == "BANKNIFTY":
        return cfg["banknifty"]
    return cfg["stock"]


def default_sl_profile(strategy=None):
    """Unified per-trade default SL/Target selector (2026-07-07 — merge of the
    old three overlapping systems into ONE mode, so only one is ever active and
    the "kaunsa default lag raha hai" confusion is gone). Returns (enabled, mode)
    with mode ∈ {'legacy','dropdown','aggressive'}:

      legacy     — per-lot ₹ (Fixed Target / Fixed SL) → stamped as
                   SL_TYPE:rs/TP_TYPE:rs entry tags. SL_VAL/TP_VAL is a
                   PER-LOT ₹ amount (price-distance stays constant per lot,
                   consistent with aggressive mode — see _generic_px's "rs"
                   branch, lot-scaled since 2026-07-09).
      dropdown   — separate SL type+value / Target type+value (the old 2️⃣ card)
                   → stamped as the chosen SL_TYPE/TP_TYPE entry tags.
      aggressive — per-lot rupee target + stepped/aggressive trailing (the old
                   5️⃣ Default Target/SL) → run at MONITOR time, no entry tags.

    Backward-compat: if the new default_sl_mode key isn't present (pre-merge
    config), infer the mode from the old separate flags so nothing silently
    changes behaviour on upgrade — default_tsl_enabled→aggressive, else a set
    default_sl_type→dropdown, else off.

    Per-strategy override (2026-07-14, task 81): pass strategy= to let
    _risk.per_strategy[<sid>].default_sl_enabled (true/false, absent=inherit)
    and .default_sl_mode ('legacy'/'dropdown'/'aggressive', absent=inherit)
    override the global pick — the global Default-SL was applying to EVERY
    strategy and silently diverging validated backtests (Critical Rule 10);
    now each strategy can opt in/out or pick its own mode. ₹ values stay
    global. strategy=None → pure global behaviour, unchanged."""
    rc = _risk_cfg()
    g = rc.get("global", {})
    mode = g.get("default_sl_mode")
    if mode in ("legacy", "dropdown", "aggressive"):
        en_raw = g.get("default_sl_enabled")
        en, mode = (True if en_raw is None else bool(en_raw)), mode
    elif g.get("default_tsl_enabled"):
        en, mode = True, "aggressive"
    elif g.get("default_sl_type"):
        en, mode = True, "dropdown"
    elif g.get("default_sl_rs"):
        # Old flat-₹ fallback SL was active (a live per-trade SL before this
        # merge) — infer legacy so it keeps applying until the user picks a mode.
        en, mode = True, "legacy"
    else:
        en, mode = False, "dropdown"
    if strategy:
        ps = (rc.get("per_strategy", {}).get(strategy, {}) or {})
        ps_mode = ps.get("default_sl_mode")
        if ps_mode in ("legacy", "dropdown", "aggressive"):
            mode = ps_mode
        ps_en = ps.get("default_sl_enabled")
        if ps_en is not None and ps_en != "":
            en = ps_en if isinstance(ps_en, bool) else str(ps_en).lower() == "true"
    return en, mode


def kill_floor_config():
    """Account-level trailing kill-floor settings (2026-07-02, user-requested).
    Semantics (locked with user, real-data-calibrated against 2026-07-02's own
    trades — peak ₹5,193 / final ₹1,937 / ₹3,256 giveback):
      enabled     — master switch (default OFF until user turns it on in RMS tab)
      arm_rs      — MTM must cross this once to ARM the floor (₹500 default);
                    below it only the existing daily-loss cap protects
      gap_rs      — floor = confirmed_peak − gap (₹1,500 default — today's data:
                    ₹500 would have killed the day at ~₹1,000-1,500 before the
                    real peak; ₹1,500 survives normal multi-position flutter)
      confirm_secs— MTM must stay below the floor this many CONSECUTIVE seconds
                    to fire (60 default) — a single spike/whipsaw never fires it
    Floor ratchets up ₹1-fine with every CONFIRMED new peak (min of two
    consecutive readings — a one-tick spike can never inflate the peak), and
    NEVER moves down. Fire → squareoff everything + block entries all day."""
    g = _risk_cfg().get("global", {})
    def _f(key, default):
        try:
            v = g.get(key)
            return default if v is None else float(v)
        except (TypeError, ValueError):
            return default
    return {
        "enabled": bool(g.get("kill_floor_enabled", False)),
        "arm_rs": _f("kill_floor_arm_rs", 500.0),
        "gap_rs": _f("kill_floor_gap_rs", 1500.0),
        "confirm_secs": _f("kill_floor_confirm_secs", 60.0),
    }


def kill_floor_fired_today() -> bool:
    """True if the account-level kill-floor (or aggregate trailing lock — same
    flag file, deliberately: both are account-level 'day is done' events) has
    fired today. Checked by gating_status() so EVERY entry path (strategies via
    strategy_safety.gate_entry AND webhook via its own flag check) is blocked —
    previously only the webhook honored this flag, a gap found while building
    the kill-floor (2026-07-02)."""
    try:
        from datetime import datetime, timedelta, timezone
        ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
        flag = BASE_DIR / "data" / f"trailing_lock_fired_{ist.strftime('%Y-%m-%d')}.txt"
        return flag.exists()
    except Exception:
        return False


def per_instrument_lock_config():
    """Per-position trailing lock settings (2026-07-02) — same arm+gap+confirm
    design as kill_floor_config(), just scoped to ONE open position instead of
    the whole account. Built specifically because the OLD per-instrument
    trailing lock (flat ₹ or % of peak, no arm threshold, no confirm window)
    was the exact system that misfired historically — same root cause as the
    old account-level lock (TRAP #71/#72/#77 family): no confirm-window, so a
    single noisy tick could trigger it. Replaced outright, not patched.
      enabled     — master switch (default OFF)
      arm_rs      — this position's OWN unrealized P&L must cross this once
                    to arm ITS floor (₹500 default, same convention as
                    account-level)
      gap_rs      — this position's floor = its own confirmed peak − gap
      confirm_secs— this position's P&L must stay below ITS floor this many
                    consecutive seconds to fire (60 default)
    Firing squares off ONLY that one position — does NOT block account-wide
    entries (that would defeat the entire point of "per-instrument": one bad
    position shouldn't stop everything else — explicit user decision,
    matches TRAP #77's fix for the old design)."""
    g = _risk_cfg().get("global", {})
    def _f(key, default):
        try:
            v = g.get(key)
            return default if v is None else float(v)
        except (TypeError, ValueError):
            return default
    return {
        "enabled": bool(g.get("per_instrument_lock_enabled", False)),
        "arm_rs": _f("per_instrument_lock_arm_rs", 500.0),
        "gap_rs": _f("per_instrument_lock_gap_rs", 1500.0),
        "confirm_secs": _f("per_instrument_lock_confirm_secs", 60.0),
    }


def advance_trailing_lock(state, mtm, arm_rs, gap_rs, confirm_secs, now_ts, mtm_unreliable=False):
    """One tick of the shared arm+gap+confirm+2-reading-peak trailing-lock
    state machine — used by BOTH the account-level kill-floor and the
    per-instrument lock (2026-07-02). Extracted into one function specifically
    so the two systems' safety logic can never independently drift the way
    TRAP #77 found (a per-X variant copy-pasting an aggregate branch and
    missing an adjustment) — fix the state machine once here, both callers
    get it.

    state: mutable dict {armed, peak, floor, breach_since, fired, prev_mtm} —
           caller owns persistence (disk-persist it, restart-safe).
    mtm:   this tick's raw P&L reading (account MTM, or one position's
           unrealized P&L — caller decides the scope).
    arm_rs/gap_rs/confirm_secs: this scope's config.
    now_ts: caller's own time.time() (or equivalent) — testable, no hidden
           clock dependency.
    mtm_unreliable: True if this tick's price data is incomplete/missing —
           freezes the peak AND the breach timer (never advances toward
           firing on data known to be incomplete); state["prev_mtm"] is also
           NOT updated on an unreliable tick, so the confirmed-peak's 2-
           reading window isn't corrupted by a bad reading either.

    Mutates state in place. Returns (state, changed) — changed=True if the
    caller should persist state to disk this tick (avoids a write every
    single cycle when nothing moved)."""
    changed = False
    confirmed = mtm if state.get("prev_mtm") is None else min(state["prev_mtm"], mtm)
    if not mtm_unreliable and confirmed > state.get("peak", 0.0):
        state["peak"] = round(confirmed, 2)
        changed = True
    if not state.get("armed") and state.get("peak", 0.0) >= arm_rs:
        state["armed"] = True
        changed = True
    if state.get("armed"):
        new_floor = round(state["peak"] - gap_rs, 2)
        if state.get("floor") is None or new_floor > state["floor"]:
            state["floor"] = new_floor
            changed = True
        if mtm_unreliable:
            pass
        elif mtm < state["floor"]:
            if state.get("breach_since") is None:
                state["breach_since"] = now_ts
                changed = True
            elif now_ts - state["breach_since"] >= confirm_secs:
                state["fired"] = True
                changed = True
        else:
            if state.get("breach_since") is not None:
                state["breach_since"] = None
                changed = True
    if not mtm_unreliable:
        state["prev_mtm"] = mtm
    return state, changed


def default_target_sl_config(strategy=None):
    """Default Target/SL exit profile (2026-07-04, user-designed) — a GLOBAL,
    per-position, rupee-based exit manager applied to every open option leg when
    enabled. All ₹ values are PER-LOT and scale by the position's lot count, so
    one config behaves consistently across instruments with very different lot
    sizes / premiums (points/percent would drift; rupee-MTM doesn't).

    Semantics (locked with user, mockup + graph approved):
      enabled            — master switch (default OFF until turned on in RMS tab)
      target_per_lot     — fixed profit target ₹/lot → exit (2000 default)
      initial_sl_per_lot — starting stop ₹/lot below entry (1000 default)
      favour_step        — every this many ₹ of favourable MTM/lot...        (100)
      sl_move            — ...the SL ratchets up this many ₹/lot             (100)
      aggressive_pct     — once peak MTM crosses this % of target...          (50)
      aggressive_mult    — ...each sl_move becomes this multiple (2x)        (2.0)
      min_cushion        — SL never sits closer than this ₹/lot below the
                           confirmed peak (0 = allowed to touch price;
                           >0 = whipsaw guard — SL trails a fixed gap so live
                           noise can't instant-stop a still-good trade). This is
                           the fix for the 'gap hits 0 → SL == live price' zone
                           the graph exposed (same class as TRAP #80-81).
    The trailing is driven by the CONFIRMED peak (2-reading min, spike-proof —
    same technique as advance_trailing_lock), not a single raw tick.

    Per-strategy values (2026-07-14, task 81 follow-up): pass strategy= to let
    _risk.per_strategy[<sid>] override any default_tsl_* ₹ value for THAT
    strategy's positions (blank/absent = inherit the global value) — same
    inherit semantics as the enabled/mode override."""
    rc = _risk_cfg()
    g = rc.get("global", {})
    ps = (rc.get("per_strategy", {}).get(strategy or "", {}) or {})
    def _f(key, default):
        for src in (ps, g):
            try:
                v = src.get(key)
                if v is not None and str(v).strip() != "":
                    return float(v)
            except (TypeError, ValueError):
                pass
        return default
    # Aggressive runs only when the unified per-trade SL mode is 'aggressive'
    # (2026-07-07 merge). Backward-compatible: default_sl_profile() falls back to
    # the old default_tsl_enabled flag when the new mode key isn't set yet.
    # strategy= → the per-strategy enabled/mode override decides for THAT strategy.
    _en, _mode = default_sl_profile(strategy)
    # Per-strategy override (task 81): a strategy with default_sl_enabled=true in
    # its Per-Strategy Override keeps the aggressive engine available for ITS
    # positions even when the global switch is OFF — firing is still gated
    # per-position by the AGGR_TSL entry marker, which only that strategy's
    # entries get stamped with.
    _ps_any = any(
        ((v or {}).get("default_sl_enabled") in (True, "true"))
        for v in (_risk_cfg().get("per_strategy") or {}).values()
    )
    return {
        "enabled": bool(_en and _mode == "aggressive"),
        # feature_on = the aggressive TSL engine is switched on at all, regardless
        # of whether 'aggressive' is the CURRENT global default mode. pos_monitor
        # gates the aggressive firing on this + the per-position AGGR_TSL tag, so a
        # position opened aggressive (incl. a per-webhook aggressive pick) keeps
        # being managed even if the global default is later switched away.
        "feature_on": bool(_en or _ps_any),
        "target_per_lot": _f("default_tsl_target_per_lot", 2000.0),
        "initial_sl_per_lot": _f("default_tsl_initial_sl_per_lot", 1000.0),
        "favour_step": _f("default_tsl_favour_step", 100.0),
        "sl_move": _f("default_tsl_sl_move", 100.0),
        "aggressive_pct": _f("default_tsl_aggressive_pct", 50.0),
        "aggressive_mult": _f("default_tsl_aggressive_mult", 2.0),
        "min_cushion": _f("default_tsl_min_cushion", 0.0),
    }


def target_sl_level(peak_mtm, cfg, lots):
    """Pure math: given the confirmed PEAK favourable MTM (₹, whole position)
    reached so far, return the current SL level (₹, signed — negative = still a
    loss stop, positive = locked profit). Deterministic; identical to the
    dashboard simulator's trail. Ratchets up only (caller feeds it a monotonic
    peak). Never returns above (peak_mtm - min_cushion), never below the
    initial stop."""
    lots = max(1, int(lots or 1))
    target = cfg["target_per_lot"] * lots
    init_sl = cfg["initial_sl_per_lot"] * lots
    fav = cfg["favour_step"] * lots
    move = cfg["sl_move"] * lots
    cushion = cfg["min_cushion"] * lots
    agg_at = target * cfg["aggressive_pct"] / 100.0
    mult = cfg["aggressive_mult"]
    sl = -init_sl
    if fav <= 0 or peak_mtm <= 0:
        return sl
    steps = int(peak_mtm // fav)
    for i in range(1, steps + 1):
        p = i * fav
        sl += (move * mult) if p > agg_at else move
    ceil = peak_mtm - cushion          # never lock closer than the cushion
    if sl > ceil:
        sl = ceil
    if sl < -init_sl:
        sl = -init_sl
    return sl


def advance_target_sl(state, mtm, cfg, lots, mtm_unreliable=False):
    """One monitor-tick of the Default Target/SL profile. Pure — caller owns
    `state` and its disk persistence (restart-safe).

    state: {peak, prev_mtm, fired}
    mtm:   this position's current unrealised P&L (₹, whole position).
    Returns (state, action, sl_level):
      action ∈ {None, 'TARGET', 'SL'} — caller squares off the position on
      anything non-None (tag DEFAULT_TSL_TARGET / DEFAULT_TSL_SL).
    A tick with incomplete price data (mtm_unreliable) never advances the peak
    and never fires — same freeze-don't-fire rule as advance_trailing_lock."""
    lots = max(1, int(lots or 1))
    target = cfg["target_per_lot"] * lots
    confirmed = mtm if state.get("prev_mtm") is None else min(state["prev_mtm"], mtm)
    if not mtm_unreliable and confirmed > state.get("peak", 0.0):
        state["peak"] = round(confirmed, 2)
    sl_level = target_sl_level(state.get("peak", 0.0), cfg, lots)
    action = None
    if not mtm_unreliable and not state.get("fired"):
        if target > 0 and mtm >= target:
            action = "TARGET"
            state["fired"] = True
        elif mtm <= sl_level:
            action = "SL"
            state["fired"] = True
        state["prev_mtm"] = mtm
    return state, action, round(sl_level, 2)


# ── Live broker balance (cash + collateral) — single cached source, shared by
# the dashboard's header/RMS-tab display AND the live daily-loss cap below, so
# there's exactly one funds() call per broker per TTL window, not one per caller.
_balance_cache = {}   # broker_name -> (ts, {available, collateral, total_margin, ok})
_BALANCE_TTL = 20      # seconds — funds() is a real broker API hit, not LTP-cheap


def get_broker_balance(broker_name):
    """{available, collateral, total_margin, ok} for 'dhan'/'kite', cached
    _BALANCE_TTL seconds. total_margin = available + collateral — the real
    capital base a LIVE position's risk is actually sized against (cash alone
    understates it for an account using collateral/pledged-securities margin,
    which is the normal way option-selling capital works on both brokers).
    ok=False (available/collateral/total_margin=None) on any failure — callers
    must treat that as 'unknown', never as 'zero balance'."""
    import time as _t
    cached = _balance_cache.get(broker_name)
    if cached and (_t.time() - cached[0]) < _BALANCE_TTL:
        return cached[1]
    out = {"available": None, "collateral": None, "total_margin": None,
           "used_margin": None, "cash": None, "ok": False}
    try:
        from brokers import get_broker
        f = get_broker(broker_name).funds()
        if f:
            avail = float(f.get("available") or 0)
            coll = float(f.get("collateral") or 0)
            used = f.get("used_margin")
            used = float(used) if used is not None else None
            cash = f.get("cash")
            cash = float(cash) if cash is not None else None
            out = {"available": avail, "collateral": coll,
                   "total_margin": avail + (used or 0),
                   "used_margin": used, "cash": cash, "ok": True}
    except Exception:
        pass
    _balance_cache[broker_name] = (_t.time(), out)
    return out


# How far back to look for a positional strategy's still-open legs (see the
# carry-over block in _today_open). Same window the dashboard's /api/orders
# carry-over uses — long enough for a multi-day hold, short enough that ancient
# rows can't creep in.
_CARRY_LOOKBACK_DAYS = 7


def _today_open(strategy=None, mode=None):
    """Today's real open positions. mode='live'/'paper' filters to that pool —
    paper strategies' simulated margins must not eat the LIVE capital budget
    (found live 2026-07-10: ₹5.2L of paper stock-option margins consumed the
    ₹10L global cap and sized a real webhook entry 2 lots → 1). mode=None =
    all (backward compat for display/reconcile callers)."""
    from datetime import datetime, timedelta, timezone
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    date_str = ist_now.strftime("%Y-%m-%d")
    try:
        import order_store
        data = order_store.trades_for(date_str)
    except Exception:
        return []
    open_pos = data.get("open", [])
    # ── Positional / overnight carry-over ────────────────────────────────────
    # A positional strategy's legs were opened on a PRIOR day and are still
    # live, but this query is date-scoped — so RMS could not see their capital
    # at all (a real 4-leg NIFTY condor holding ~₹85k of margin counted as ₹0).
    # That's the mirror of TRAP #119: the same today-scoping that HID positional
    # positions from the dashboard also hid them from the risk engine.
    #
    # ONLY opt-in `allow_overnight` strategies carry over — an intraday
    # strategy is flat by EOD, so a lingering prior-day "open" row for it is a
    # STALE record, not live capital (this DB has ~38 such rows). Counting
    # those would invent phantom capital and false-block every new entry —
    # strictly worse than the under-count being fixed here.
    try:
        _lb = (ist_now - timedelta(days=_CARRY_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
        _carry = order_store.trades_for_range(_lb, date_str).get("open", [])
        _seen = {(p.get("sym"), str(p.get("sec_id")), p.get("entry_date")) for p in open_pos}
        for p in _carry:
            if (p.get("entry_date") or date_str) >= date_str:
                continue                      # today's own opens already present
            if not allow_overnight(p.get("strategy")):
                continue                      # intraday leftovers = stale, not live
            k = (p.get("sym"), str(p.get("sec_id")), p.get("entry_date"))
            if k in _seen:
                continue
            open_pos.append(p)
            _seen.add(k)
    except Exception:
        pass                                  # carry-over is additive; never break the base query
    if mode is not None:
        m = str(mode).lower().strip()
        open_pos = [p for p in open_pos if str(p.get("mode") or "").lower() == m]
    # A CAPITAL_BLOCKED entry was REFUSED — never actually placed at the
    # broker — but order_store's netting has no "blocked" terminal status,
    # so it falls through as a phantom unmatched "open position" (exit_price
    # None). The dashboard UI already excludes these (templates/index.html's
    # isCapBlocked filter) when showing real Open Positions; capital_in_use()/
    # exposure_by_underlying() below never had the same exclusion, so a
    # blocked entry's re-estimated margin kept compounding into the NEXT
    # signal's "in-use" figure — a cascading false-block loop where one
    # rejected attempt permanently (for the rest of the day) makes every
    # later real signal look like it would breach the cap too, even with
    # zero real capital actually deployed. Found live 2026-07-03.
    open_pos = [p for p in open_pos
                if "CAPITAL_BLOCKED" not in (p.get("tags") or [])]
    if strategy is not None:
        open_pos = [p for p in open_pos if p.get("strategy") == strategy]
    return open_pos


def _margin_multiplier(strategy, rc=None):
    """Fallback ONLY — used when the real Dhan margin-calculator call (see
    dhan_real_margin below) fails (no token, network, rate-limited, unexpected
    response shape). Selling an option blocks margin, not just the premium
    received — qty*price massively understates the real capital block for
    SELL legs, so even the fallback applies a configurable multiplier rather
    than raw notional. Strategy-specific margin_multiplier overrides global;
    default 1.0 (off) so existing setups aren't surprised by this."""
    rc = rc or _risk_cfg()
    strat_mult = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("margin_multiplier")
    glob_mult = (rc.get("global", {}) or {}).get("margin_multiplier")
    mult = strat_mult if strat_mult is not None else glob_mult
    try:
        return float(mult) if mult is not None else 1.0
    except Exception:
        return 1.0


_MARGIN_CACHE = {}     # (sec_id, seg, qty, side, round(price)) -> (ts, margin_rs)
_MARGIN_CACHE_TTL = 90  # seconds — margin doesn't move fast enough to need fresher than this


def _dhan_creds():
    try:
        cfg = json.loads((BASE_DIR / "data" / "config.json").read_text())
        return cfg.get("jwt_token", ""), cfg.get("client_id", "")
    except Exception:
        return "", ""


def dhan_real_margin(sec_id, seg, qty, price, side, product_type="INTRADAY"):
    """Real NSE-grade margin (SPAN + exposure) for ONE leg, straight from Dhan's
    own `/v2/margincalculator` — this is the actual number Dhan would block for
    that single order, not a guessed multiplier. Cached (90s TTL) since this is
    a live API call and gets queried repeatedly for the same open positions.

    IMPORTANT LIMITATION: this evaluates each leg as if it were the ONLY
    position in the account. It does NOT know about hedge/spread benefit
    across multiple legs of the same strategy (e.g. a short strangle: SELL CE
    + SELL PE on the same underlying+expiry) — NSE/the broker only recognizes
    that benefit once both legs are actually live in the same account, which a
    pre-trade per-leg API call can't see. For genuinely hedged strategies this
    will OVER-estimate margin (conservative, not wrong-direction-dangerous) —
    if that matters, check the real combined margin in the Dhan app once both
    legs are live and set a manual per-strategy `capital_rs` override instead
    of relying on this estimate for that strategy.

    Returns None on any failure — caller must fall back to _margin_multiplier."""
    if not sec_id or qty <= 0 or price <= 0:
        return None
    key = (str(sec_id), seg, int(qty), str(side).upper(), round(float(price)))
    now = __import__("time").time()
    cached = _MARGIN_CACHE.get(key)
    if cached and (now - cached[0]) < _MARGIN_CACHE_TTL:
        return cached[1]
    token, cid = _dhan_creds()
    if not token or not cid:
        return None
    try:
        import requests
        import dhan_rate_limiter as _rl
        _rl.acquire("account")
        r = requests.post("https://api.dhan.co/v2/margincalculator",
            json={"dhanClientId": cid, "exchangeSegment": seg,
                  "transactionType": str(side).upper(), "quantity": int(qty),
                  "productType": product_type, "securityId": str(sec_id),
                  "price": float(price), "triggerPrice": 0},
            headers={"access-token": token, "client-id": cid, "Content-Type": "application/json"},
            timeout=6)
        if r.status_code == 429:
            _rl.note_429()
        if r.status_code != 200:
            return None
        d = r.json() or {}
        total = d.get("totalMargin")
        if total is None:
            return None
        margin = float(total)
        _MARGIN_CACHE[key] = (now, margin)
        return margin
    except Exception:
        return None


_KITE_MARGIN_CACHE = {}  # same key shape + TTL as _MARGIN_CACHE above


def kite_real_margin(sec_id, seg, qty, price, side, product_type="INTRADAY", trad_sym=None):
    """Real Zerodha margin for ONE leg via Kite Connect's order_margins API —
    the exact ₹ Zerodha (the EXECUTING broker when default_broker=kite) would
    block for this order. Same 90s cache + same per-leg limitation as
    dhan_real_margin (no cross-leg hedge benefit pre-trade). Symbol resolution
    goes through kite_broker's structured-field resolver (TRAP #13/#59) —
    never a string-guess. Returns None on any failure — caller must fall back
    to dhan_real_margin, then _margin_multiplier."""
    if not sec_id or qty <= 0 or price <= 0:
        return None
    key = (str(sec_id), seg, int(qty), str(side).upper(), round(float(price)))
    now = __import__("time").time()
    cached = _KITE_MARGIN_CACHE.get(key)
    if cached and (now - cached[0]) < _MARGIN_CACHE_TTL:
        return cached[1]
    try:
        from brokers import get_broker
        import dhan_master
        broker = get_broker("kite")
        if not trad_sym:
            trad_sym = dhan_master.get_trad_sym_for_sec_id(sec_id)
        if not trad_sym:
            return None
        kite_sym = broker.resolve_symbol(trad_sym, sec_id=sec_id)
        if not kite_sym:
            return None
        exchange = {"NSE_FNO": "NFO", "NSE_EQ": "NSE"}.get(seg, "NFO")
        product = "NRML" if str(product_type).upper() in ("NRML", "MARGIN", "CNC") else "MIS"
        margin = broker.margin_for_order(kite_sym, exchange, side, qty, price, product=product)
        if margin is None:
            return None
        _KITE_MARGIN_CACHE[key] = (now, margin)
        return margin
    except Exception:
        return None


_BASKET_MARGIN_CACHE = {}   # key -> (ts, hedged_margin)


def basket_margin_enabled():
    """Kill-switch for the basket (hedge-benefit) capital estimate below.
    _risk.global.basket_margin_enabled — default True. Turn OFF to fall back to
    the old per-leg sum everywhere, without a redeploy."""
    try:
        v = (_risk_cfg().get("global", {}) or {}).get("basket_margin_enabled")
        return True if v is None else bool(v)
    except Exception:
        return True


def kite_basket_margin(rows, product_type="NRML"):
    """Real Zerodha margin for a MULTI-LEG structure via Kite's
    basket_order_margins — the ₹ actually blocked for the basket AS A WHOLE,
    i.e. WITH cross-leg hedge benefit.

    This closes the gap both per-leg estimators openly carry ("no cross-leg
    hedge benefit pre-trade" — see kite_real_margin/dhan_real_margin): summing
    per-leg margins massively overstates a hedged structure. Live-measured
    2026-07-16: a 2-leg vertical = ₹37,813 basket vs ₹1,49,554 per-leg sum
    (75% over); a 4-leg NIFTY condor = ₹82,334 vs ₹3,72,015 (78% over).

    Same 90s cache as the per-leg estimators. Returns float, or None on ANY
    failure — the caller must then fall back to the per-leg sum (the higher,
    conservative number). Never treat None as 0.
    """
    legs = [p for p in (rows or []) if p.get("sec_id")]
    if len(legs) < 2:
        return None
    key = tuple(sorted((str(p.get("sec_id")), str(p.get("entry")).upper(),
                        int(float(p.get("qty") or 0)),
                        round(float(p.get("entry_price") or 0)))
                       for p in legs))
    now = __import__("time").time()
    cached = _BASKET_MARGIN_CACHE.get(key)
    if cached and (now - cached[0]) < _MARGIN_CACHE_TTL:
        return cached[1]
    try:
        from brokers import get_broker
        broker = get_broker("kite")
        product = "NRML" if str(product_type).upper() in ("NRML", "MARGIN", "CNC") else "MIS"
        orders = []
        for p in legs:
            ksym = broker.resolve_symbol(p.get("sym"), sec_id=p.get("sec_id"))
            if not ksym:
                return None                      # one unresolved leg → whole basket unsafe
            seg = p.get("segment") or "NSE_FNO"
            orders.append({
                "exchange": {"NSE_FNO": "NFO", "NSE_EQ": "NSE"}.get(seg, "NFO"),
                "tradingsymbol": ksym,
                "transaction_type": str(p.get("entry")).upper(),
                "variety": "regular", "product": product, "order_type": "LIMIT",
                "quantity": int(float(p.get("qty") or 0)),
                "price": float(p.get("ltp") or p.get("entry_price") or 0),
            })
        m = broker.basket_margin(orders)
        if m is None:
            return None
        _BASKET_MARGIN_CACHE[key] = (now, float(m))
        return float(m)
    except Exception:
        return None


def _group_capital(rows, rc=None):
    """₹ capital for the open legs of ONE strategy. A multi-leg option structure
    gets the broker's REAL basket margin (hedge benefit included); anything else
    — single leg, non-F&O, basket disabled, or ANY failure — falls back to the
    per-leg sum, which is the higher/conservative number. Capped by that sum so
    this can never report MORE than the old behaviour did."""
    per_leg = sum(_leg_capital(p, rc) for p in rows)
    if not basket_margin_enabled() or default_broker() != "kite":
        return per_leg
    fno = [p for p in rows if (p.get("segment") or "NSE_FNO") == "NSE_FNO" and p.get("sec_id")]
    if len(fno) < 2 or len(fno) != len(rows):
        return per_leg                          # mixed/equity/single → no basket claim
    b = kite_basket_margin(rows)
    if b is None or b <= 0:
        return per_leg
    return min(b, per_leg)


def broker_real_margin(sec_id, seg, qty, price, side, product_type="INTRADAY", trad_sym=None):
    """Margin estimate from the EXECUTING broker (TRAP #90's lesson: when
    orders go to broker B, capital checks must be validated against broker B's
    numbers). default_broker=kite → real Zerodha order_margins first; anything
    else / Kite failure → Dhan's margin-calculator; None → caller's
    multiplier fallback. Single entry point for every RMS margin estimate —
    don't call dhan_real_margin directly from capital checks anymore."""
    if default_broker() == "kite":
        m = kite_real_margin(sec_id, seg, qty, price, side, product_type, trad_sym=trad_sym)
        if m is not None:
            return m
    return dhan_real_margin(sec_id, seg, qty, price, side, product_type)


def _leg_capital(p, rc=None):
    """Margin-adjusted ₹ capital for one open-position dict from order_store.
    SELL legs: try the executing broker's real margin first (broker_real_margin
    — Kite when default_broker=kite, else Dhan calculator); fall back to the
    configurable multiplier estimate only if that call fails."""
    try:
        qty, price = float(p.get("qty") or 0), float(p.get("entry_price") or 0)
    except Exception:
        return 0.0
    notional = qty * price
    if str(p.get("entry") or "").upper() == "SELL":
        real = broker_real_margin(p.get("sec_id"), p.get("segment") or "NSE_FNO", qty, price, "SELL",
                                  trad_sym=p.get("sym"))
        if real is not None:
            return real
        notional *= _margin_multiplier(p.get("strategy"), rc)
    return notional


def capital_in_use(strategy=None, mode=None):
    """₹ capital currently deployed (margin-adjusted for SELL legs — see
    _margin_multiplier) over open positions. strategy=None → ALL strategies
    (for the global cap check). mode='live'/'paper' → only that pool
    (see _today_open); None → all modes combined.

    Legs are grouped BY STRATEGY and each group costed via _group_capital, so a
    strategy's own hedged structure is charged the broker's real basket margin
    instead of the sum of its legs' standalone margins (which overstates a
    hedged position badly — a live 2-leg vertical measured ₹1.50L per-leg vs
    ₹37.8k real). Cross-STRATEGY netting is deliberately NOT claimed even though
    the broker would give it — that keeps this an over-estimate, never an
    under-estimate, of what's really blocked."""
    rc = _risk_cfg()
    rows = _today_open(strategy, mode=mode)
    if not rows:
        return 0.0
    by_strat = {}
    for p in rows:
        by_strat.setdefault(p.get("strategy") or "", []).append(p)
    return sum(_group_capital(g, rc) for g in by_strat.values())


def check_capital(strategy, qty, price, side="SELL", sec_id=None, seg="NSE_FNO", mode=None):
    """Would adding qty@price (side BUY/SELL) to `strategy` breach its allocation
    or the global ceiling? Returns (ok: bool, reason: str). reason='' when ok.

    mode='live'/'paper': in-use is computed from THAT pool only — a live entry
    checks against live positions, paper against paper (same ₹ cap value, two
    separate pools). mode=None keeps the old combined behavior.

    SELL legs (option-selling, the common case for these strategies): if
    sec_id is given, tries the EXECUTING broker's real margin first (see
    broker_real_margin — Kite order_margins when default_broker=kite, else
    Dhan's margin-calculator; actual SPAN+exposure for this exact order),
    falling back to the configurable margin_multiplier estimate only if that
    fails or sec_id wasn't provided. BUY legs use the premium paid as-is
    (that IS the capital committed, no margin involved).

    Strategy-specific capital_rs overrides the global one for that strategy's
    own cap; the global cap (sum across ALL strategies) always applies too —
    whichever is hit first blocks the entry."""
    rc = _risk_cfg()
    qty, price = float(qty or 0), float(price or 0)
    needed = qty * price
    if str(side or "SELL").upper() == "SELL":
        real = broker_real_margin(sec_id, seg, qty, price, "SELL") if sec_id else None
        needed = real if real is not None else needed * _margin_multiplier(strategy, rc)
    if needed <= 0:
        return True, ""

    strat_cap = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("capital_rs")
    glob_cap = (rc.get("global", {}) or {}).get("capital_rs")

    pool = f", {str(mode).lower()}-pool" if mode else ""
    if strat_cap is not None:
        try:
            strat_cap = float(strat_cap)
            in_use = capital_in_use(strategy, mode=mode)
            if in_use + needed > strat_cap:
                return False, (f"strategy capital cap ₹{_inr(strat_cap)} hit "
                                f"(in-use ₹{_inr(in_use)} + needed ₹{_inr(needed)}{pool})")
        except Exception:
            pass

    if glob_cap is not None:
        try:
            glob_cap = float(glob_cap)
            in_use_all = capital_in_use(None, mode=mode)
            if in_use_all + needed > glob_cap:
                return False, (f"global capital cap ₹{_inr(glob_cap)} hit "
                                f"(in-use ₹{_inr(in_use_all)} + needed ₹{_inr(needed)}{pool})")
        except Exception:
            pass

    return True, ""


def _quick_option_ltp(sec_id, token, cid):
    """Best-effort option premium fetch (Dhan /v2/marketfeed/ltp), same call shape
    the legacy _TRADERS/*.py place_order() functions already make. Returns None on
    any failure — caller should fall back to a spot/estimate price."""
    try:
        import requests
        import dhan_rate_limiter as _rl
        _rl.acquire("ltp")
        r = requests.post("https://api.dhan.co/v2/marketfeed/ltp",
                          json={"NSE_FNO": [int(sec_id)]},
                          headers={"access-token": token, "client-id": cid, "Content-Type": "application/json"},
                          timeout=5)
        if r.status_code == 429:
            _rl.note_429()
        if r.status_code != 200:
            return None
        data = r.json().get("data", {}).get("NSE_FNO", {})
        for v in (data.values() if isinstance(data, dict) else []):
            ltp = float(v.get("last_price") or v.get("ltp") or 0)
            if ltp:
                return ltp
    except Exception:
        pass
    return None


def check_capital_option(strategy, qty, sec_id, token, cid, fallback_price=0.0, side="SELL"):
    """Like check_capital() but fetches the real option premium first (capital
    relevance for options is the premium, not the underlying spot price the
    legacy traders pass around internally). Falls back to fallback_price if the
    LTP fetch fails (still useful as a rough gate rather than skipping entirely)."""
    price = _quick_option_ltp(sec_id, token, cid) or float(fallback_price or 0)
    return check_capital(strategy, qty, price, side=side, sec_id=sec_id)


def capital_mode(strategy):
    """'reject' (default, Stage 1 behavior — block the whole entry) or
    'size_down' (fill the largest qty that fits remaining capital instead).
    Strategy-specific overrides global; absent on both = 'reject'."""
    rc = _risk_cfg()
    m = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("capital_mode")
    if m is None:
        m = (rc.get("global", {}) or {}).get("capital_mode")
    m = str(m or "reject").lower().strip()
    return m if m in ("reject", "size_down") else "reject"


def sized_lots_option(strategy, lots, lot_size, sec_id, token, cid, fallback_price=0.0, side="SELL"):
    """sized_lots() but fetches the real option premium first, like
    check_capital_option() does for the reject path."""
    price = _quick_option_ltp(sec_id, token, cid) or float(fallback_price or 0)
    return sized_lots(strategy, lots, lot_size, price, side=side, sec_id=sec_id)


def sized_lots(strategy, lots, lot_size, price, side="SELL", sec_id=None, seg="NSE_FNO", mode=None):
    """For capital_mode='size_down': how many of the requested `lots` (each
    `lot_size` qty) actually fit in the remaining capital? Returns an int
    0..lots — 0 means even one lot doesn't fit (caller should still block).
    Respects lot boundaries (can't size into a fractional lot). Pass sec_id
    for SELL legs to use the executing broker's real margin (see check_capital).
    mode='live'/'paper' → size against that pool only (see check_capital)."""
    lots = int(lots or 0)
    if lots <= 0:
        return 0
    per_lot_qty = max(1, int(lot_size or 1))
    for try_lots in range(lots, 0, -1):
        ok, _ = check_capital(strategy, try_lots * per_lot_qty, price, side=side, sec_id=sec_id, seg=seg, mode=mode)
        if ok:
            return try_lots
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# RMS Stage 3 — real broker funds, cross-strategy concentration, drawdown
# circuit breaker, capital/funds reconciliation.
# ─────────────────────────────────────────────────────────────────────────────

def check_broker_funds(broker, needed_rs):
    """LIVE mode only — does the broker's actual available balance cover this
    entry? Catches drift our own bookkeeping can't see (manual trades, other
    apps drawing the same account, partial fills elsewhere). Best-effort: if
    funds() fails or returns nothing usable, DON'T block — an API hiccup
    shouldn't silently halt live trading; fall through to the capital_rs gate,
    which is more important since it's not affected by API flakiness."""
    if needed_rs <= 0 or broker is None or not hasattr(broker, "funds"):
        return True, ""
    try:
        f = broker.funds() or {}
        avail = float(f.get("available") or 0)
    except Exception:
        return True, ""
    if avail <= 0:
        return True, ""  # couldn't determine — don't block on an unknown
    if needed_rs > avail:
        return False, f"broker funds insufficient (avail ₹{_inr(avail)} < needed ₹{_inr(needed_rs)})"
    return True, ""


def _underlying(symbol_or_tradsym):
    """'NIFTY-Jun2026-24000-CE' -> 'NIFTY'; 'RELIANCE' -> 'RELIANCE'."""
    return str(symbol_or_tradsym or "").split("-")[0].upper()


def exposure_by_underlying(underlying=None, mode=None):
    """₹ margin-adjusted capital currently deployed per underlying, across ALL
    strategies (this is the whole point — a per-strategy cap can't see another
    strategy piling into the same name). underlying=None -> dict for all.
    mode='live'/'paper' → only that pool (see _today_open); None → all."""
    rc = _risk_cfg()
    totals = {}
    for p in _today_open(None, mode=mode):
        u = _underlying(p.get("symbol") or p.get("sym"))
        totals[u] = totals.get(u, 0.0) + _leg_capital(p, rc)
    if underlying is not None:
        return totals.get(_underlying(underlying), 0.0)
    return totals


def check_concentration(symbol, qty, price, side="SELL", mode=None):
    """Would this entry push combined exposure to `symbol`'s underlying (across
    ALL strategies) past max_underlying_exposure_rs? Global-only setting (this
    is inherently a cross-strategy check) — nifty_config.json["_risk"]["global"]
    ["max_underlying_exposure_rs"]. Absent = no cap (off by default)."""
    rc = _risk_cfg()
    cap = (rc.get("global", {}) or {}).get("max_underlying_exposure_rs")
    if cap is None:
        return True, ""
    try:
        cap = float(cap)
    except Exception:
        return True, ""
    needed = float(qty or 0) * float(price or 0)
    if str(side or "SELL").upper() == "SELL":
        needed *= _margin_multiplier(None, rc)
    if needed <= 0:
        return True, ""
    u = _underlying(symbol)
    in_use = exposure_by_underlying(u, mode=mode)
    if in_use + needed > cap:
        pool = f", {str(mode).lower()}-pool" if mode else ""
        return False, (f"underlying '{u}' concentration cap ₹{_inr(cap)} hit "
                        f"(in-use ₹{_inr(in_use)} + needed ₹{_inr(needed)}{pool})")
    return True, ""


def _today_realized_pnl():
    """Sum of realized P&L (completed trades, all strategies) for today —
    from order_store's already-netted 'details'."""
    
    from datetime import datetime, timedelta, timezone
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    date_str = ist_now.strftime("%Y-%m-%d")
    try:
        import order_store
        data = order_store.trades_for(date_str)
    except Exception:
        return 0.0
    return sum(float(d.get("pnl") or 0) for d in data.get("details", [])
               if d.get("mode") != "paper")


def check_drawdown(unrealized_pnl=0.0):
    """Global circuit breaker — once today's cumulative P&L (realized completed
    trades + unrealized open positions, across ALL strategies) breaches
    daily_drawdown_cap_rs, block ALL new entries (existing positions' own SL/
    max-loss still manage themselves independently — this is a separate, blunter
    "stop digging" switch). unrealized_pnl is the caller's best-effort estimate
    (e.g. from /api/rms-summary) — pass 0 if unavailable, the realized-only
    check is still meaningful. Absent config = no breaker (off by default)."""
    rc = _risk_cfg()
    cap = (rc.get("global", {}) or {}).get("daily_drawdown_cap_rs")
    if cap is None:
        return True, ""
    try:
        cap = float(cap)
    except Exception:
        return True, ""
    if cap <= 0:
        return True, ""
    total = _today_realized_pnl() + float(unrealized_pnl or 0)
    if total <= -abs(cap):
        return False, f"daily drawdown cap ₹{_inr(cap)} hit (today's P&L ₹{_inr(total)})"
    return True, ""


# ── Unified daily loss breaker (SUPREME RMS layer) ───────────────────────────
# One ₹ daily-loss cap per strategy that NO strategy can bypass. It is the single
# source of truth that replaces the older scattered caps (webhook's own
# daily_amount_cap, the per-position total_capital_rs 1%, the drawdown breaker).
# It both (a) blocks new entries and (b) force-squares-off that strategy's open
# positions once breached. Per-strategy SL/target stay independent BELOW this —
# they can exit earlier, but can never let a strategy run past this loss.
#
# Resolution order for the cap (first non-null wins), so existing config keeps
# working and it's ALWAYS on by default:
#   per_strategy[strat].max_loss_rs  ->  global.max_loss_rs  ->
#   global.daily_drawdown_cap_rs     ->  DEFAULT_DAILY_LOSS_RS
DEFAULT_DAILY_LOSS_RS = 5000.0

# ── Daily profit target (mirror of daily-loss cap) ───────────────────────────
# When combined realized P&L hits this target: block all new entries + force
# squareoff of all open positions. Same resolution order as loss cap.
# per_strategy[strat].profit_target_rs -> global.profit_target_rs -> None (off)
# Unlike the loss cap, this has NO default — if not configured, it never fires.


def effective_daily_profit_target(strategy=None, rc=None):
    """₹ profit target for the day. Returns None if not configured (off)."""
    rc = rc or _risk_cfg()
    ps = (rc.get("per_strategy", {}).get(strategy or "", {}) or {})
    gl = rc.get("global", {}) or {}
    for v in (ps.get("profit_target_rs"), gl.get("profit_target_rs")):
        if v is not None:
            try:
                f = float(v)
                if f > 0:
                    return f
            except Exception:
                pass
    return None


def daily_profit_target_hit(strategy, unrealized=0.0, rc=None):
    """True if today's combined P&L (realized + unrealized) has hit the profit
    target for `strategy`. Returns (hit, reason)."""
    target = effective_daily_profit_target(strategy, rc=rc)
    if target is None:
        return False, ""
    pnl = _strategy_day_pnl(strategy, unrealized)
    if pnl >= abs(target):
        return True, f"🎯 Daily profit target ₹{_inr(target)} hit for '{strategy}' (today's P&L ₹{_inr(pnl)})"
    return False, ""


def effective_max_trades_per_day(strategy=None, rc=None):
    """Max entries a strategy may take in one day. per_strategy overrides global;
    None/blank/0 = OFF (no cap). Returns int or None."""
    rc = rc or _risk_cfg()
    ps = (rc.get("per_strategy", {}).get(strategy or "", {}) or {})
    gl = rc.get("global", {}) or {}
    for v in (ps.get("max_trades_per_day"), gl.get("max_trades_per_day")):
        if v not in (None, "", 0, "0"):
            try:
                n = int(float(v))
                if n > 0:
                    return n
            except Exception:
                pass
    return None


def entries_today(strategy):
    """How many entries `strategy` has ALREADY taken today, read from order_store
    — the durable record — not from an in-memory counter.

    THE point of this function: every strategy kept its own `trades_today` int,
    seeded on startup as `0 if pos is None else 1`. That is wrong the moment a
    strategy is restarted after its position CLOSED — the day's count silently
    resets to 0 and the strategy can re-take its ENTIRE max-trades-per-day quota
    (seen live 2026-07-16: orbst went trades=1 -> trades=0 across a restart,
    having already taken and stopped out its trade). Restarts are routine here
    (deploys, crashes, VPS reboots), so the cap was never really enforced.

    Counts BOTH completed round-trips and still-open legs whose ENTRY is today,
    minus CAPITAL_BLOCKED phantoms (TRAP #86/#92). Returns None if it can't be
    read — callers must decide, never silently treat that as 0."""
    try:
        import order_store, datetime as _dt
        today = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        data = order_store.trades_for(today) or {}
        n = 0
        for bucket in ("details", "open"):
            for t in (data.get(bucket) or []):
                if t.get("strategy") != strategy:
                    continue
                if "CAPITAL_BLOCKED" in (t.get("tags") or []):
                    continue
                if (t.get("entry_date") or today) != today:
                    continue          # positional leg entered on a prior day
                n += 1
        return n
    except Exception:
        return None


def daily_max_trades_hit(strategy, rc=None):
    """True if `strategy` has already taken its max entries today (RMS cap).
    Returns (hit, reason). Fail-open (never blocks) if the count can't be read —
    a bookkeeping read must not silently halt a strategy.

    Counting is delegated to entries_today() so this and the strategies' own
    per-day caps can never disagree. That also fixed a real undercount here:
    this used to read order_store's "details" bucket only — i.e. COMPLETED
    round-trips — so a still-OPEN entry didn't count toward the cap and a
    strategy could exceed it (docstring already claimed "open + completed";
    the code never did)."""
    cap = effective_max_trades_per_day(strategy, rc=rc)
    if not cap:
        return False, ""
    n = entries_today(strategy)
    if n is None:
        return False, ""
    if n >= cap:
        return True, f"📋 Max trades/day reached ({n}/{cap}) for '{strategy}' — no further entries today"
    return False, ""


def effective_daily_loss_cap(strategy=None, rc=None, mode=None, broker=None):
    """The unified ₹ daily-loss cap for a strategy (always returns a positive
    number — RMS is mandatory).

    LIVE mode + a configured max_loss_pct + a working broker-balance fetch:
    the cap becomes `max_loss_pct% of that broker's REAL total_margin (cash +
    collateral)` — computed fresh each call (cached 20s, see
    get_broker_balance()), so it tracks the actual account, not a number
    someone typed in once. This REPLACES the ₹ cap below for live (per user
    request 2026-06-29: "is balance ka 1% limit ko follow karenge, na ki
    paper trade ke limits") — it does not combine with it. Falls through to
    the static ₹ cap if mode isn't 'live', no broker given, no max_loss_pct
    configured, or the balance fetch fails (fail-safe: never silently
    "unlimited").

    Paper mode (or anything missing above): per-strategy overrides global;
    absent everywhere falls back to DEFAULT_DAILY_LOSS_RS so it can never be
    silently off."""
    rc = rc or _risk_cfg()
    ps = (rc.get("per_strategy", {}).get(strategy or "", {}) or {})
    gl = (rc.get("global", {}) or {})

    if mode == "live" and broker:
        pct = ps.get("max_loss_pct") if ps.get("max_loss_pct") is not None else gl.get("max_loss_pct")
        if pct is not None:
            try:
                pct = float(pct)
            except Exception:
                pct = None
            if pct and pct > 0:
                bal = get_broker_balance(broker)
                if bal.get("ok") and bal.get("total_margin"):
                    return bal["total_margin"] * (pct / 100.0)

    for v in (ps.get("max_loss_rs"), gl.get("max_loss_rs"),
              gl.get("daily_drawdown_cap_rs")):
        if v is not None:
            try:
                f = float(v)
                if f > 0:
                    return f
            except Exception:
                pass
    return DEFAULT_DAILY_LOSS_RS


def _strategy_day_pnl(strategy, unrealized_by_strat=None):
    """Today's realized P&L for one strategy (from order_store's netted details)
    + an optional caller-supplied unrealized estimate for that strategy."""
    
    from datetime import datetime, timedelta, timezone
    ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    date_str = ist_now.strftime("%Y-%m-%d")
    realized = 0.0
    try:
        import order_store
        data = order_store.trades_for(date_str, strategy=strategy)
        realized = sum(float(d.get("pnl") or 0) for d in data.get("details", []))
    except Exception:
        pass
    return realized + float(unrealized_by_strat or 0)


def default_instrument_sl_tags(strategy, symbol=None, mode_override=None):
    """Default stop-loss & target tags, applied automatically to EVERY NEW position at entry time.
    Written/Modified by Antigravity AI.
    
    Priority:
    1. If per-strategy default_sl_rs is set, use SL_TYPE:rs and SL_VAL:default_sl_rs.
    2. Else, use the global default_sl_type and default_sl_val if set.
    3. Always append default_tp_type and default_tp_val if set.
    """
    rc = _risk_cfg()
    tags = []

    # Unified per-trade default SL mode (2026-07-07 merge). Only one mode's tags
    # ever get stamped. 'aggressive' is a MONITOR-time profile (advance_target_sl
    # via default_target_sl_config) — it stamps NO entry tags, so return []. 'off'
    # → []. Applies to NEW positions only (existing open positions keep whatever
    # tags they already have — user's explicit call, mid-trade SL never changes).
    _en, _mode = default_sl_profile(strategy)   # per-strategy override aware (task 81)
    # Per-position mode override (2026-07-09) — a caller (e.g. a webhook config's
    # own SL-Type selector) can make THIS position use a different one of the 3
    # profiles (legacy/aggressive/dropdown) than the global RMS default, still
    # using that profile's global ₹ values. None → follow the global default.
    if mode_override in ("legacy", "aggressive", "dropdown"):
        _en, _mode = True, mode_override
    if not _en:
        return []
    if _mode == "aggressive":
        # Aggressive is a MONITOR-time profile (advance_target_sl), not an entry
        # tag — but stamp a marker so pos_monitor only applies it to positions
        # opened WHILE aggressive was the chosen mode (user's "new trades only"
        # rule — a mid-day switch to aggressive must NOT grab already-open
        # trades). Not an SL_TYPE/TP_TYPE prefix, so the generic SL check + exit-
        # reason badge both ignore it.
        return ["AGGR_TSL:1"]

    g0 = rc.get("global") or {}
    # Per-strategy VALUE overrides (2026-07-14, task 81 follow-up) — the
    # Per-Strategy Override table's ⚙ values editor writes mode-value keys into
    # per_strategy[<sid>]; blank/absent = inherit the global card's value.
    ps0 = (rc.get("per_strategy", {}).get(strategy or "", {}) or {})
    def _cv(key):
        v = ps0.get(key)
        if v is None or str(v).strip() == "":
            return g0.get(key)
        return v
    if _mode == "legacy":
        # Per-lot ₹ (SL_TYPE:rs interprets val as a PER-LOT ₹ amount — see
        # _generic_px's "rs" branch, lot-scaled since 2026-07-09). The
        # price-distance stays constant per lot and scales with lot count,
        # consistent with aggressive mode.
        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        if g0.get("default_sl_mode") == "legacy" or ps0.get("default_sl_mode") == "legacy":
            # Explicitly chosen in the merged UI (globally or by this strategy's
            # own override) → user's 5000/2000 defaults.
            sl_rs = _num(_cv("default_legacy_sl_rs")) or 2000.0
            tp_rs = _num(_cv("default_legacy_tp_rs")) or 5000.0
        else:
            # Inferred from the pre-merge default_sl_rs fallback → preserve the
            # OLD behaviour EXACTLY: that flat ₹ was an SL only, no target.
            sl_rs = _num(_cv("default_legacy_sl_rs")) or _num(g0.get("default_sl_rs")) or 0.0
            _tp = _cv("default_legacy_tp_rs")
            tp_rs = _num(_tp) if _tp not in (None, "", 0, "0") else 0.0
        if sl_rs > 0:
            tags.extend([f"SL_TYPE:rs", f"SL_VAL:{sl_rs}"])
        if tp_rs and tp_rs > 0:
            tags.extend([f"TP_TYPE:rs", f"TP_VAL:{tp_rs}"])
        return tags

    # ── mode == "dropdown" ── (the old 2️⃣ behaviour, preserved verbatim) ──
    # 1. Check strategy-specific legacy default_sl_rs
    ps_rs = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("default_sl_rs")
    if ps_rs is not None:
        try:
            val = float(ps_rs)
            if val > 0:
                tags.extend([f"SL_TYPE:rs", f"SL_VAL:{val}"])
        except Exception:
            pass

    # 2. If no strategy legacy SL is set, check per-strategy (⚙ values) then global SL.
    # The type+value PAIR comes from ONE source — this strategy's own pair if both
    # its type and value are set, else the global pair (never mix the two).
    if not tags:
        g = rc.get("global") or {}
        _ps_t, _ps_v = ps0.get("default_sl_type"), ps0.get("default_sl_val")
        if _ps_t and _ps_v is not None and str(_ps_v).strip() != "":
            sl_type, sl_val = _ps_t, _ps_v
        else:
            sl_type, sl_val = g.get("default_sl_type"), g.get("default_sl_val")
        _sl_cc = ps0.get("default_sl_candle_close")
        if _sl_cc is None or str(_sl_cc).strip() == "":
            _sl_cc = g.get("default_sl_candle_close")
        _sl_cc = (_sl_cc is True) or (str(_sl_cc).lower() == "true")
        if sl_type and sl_val is not None and str(sl_val).strip() != "":
            if sl_type == "trailing_pt":
                if ":" in str(sl_val):
                    gap_part, step_part = str(sl_val).split(":", 1)
                    gap_val = float(gap_part)
                    step_val = float(step_part)
                    tags.extend([f"SL_TYPE:trailing_pt", f"SL_VAL:{gap_val}", f"SL_TRAIL_STEP:{step_val}"])
                else:
                    gap_val = float(sl_val)
                    tags.extend([f"SL_TYPE:trailing_pt", f"SL_VAL:{gap_val}"])
            else:
                tags.extend([f"SL_TYPE:{sl_type}", f"SL_VAL:{sl_val}"])
            if _sl_cc:
                tags.append("SL_CANDLE_CLOSE:true")
        else:
            # Fall back to legacy global default_sl_rs if type/val not configured
            gl_rs = g.get("default_sl_rs")
            if gl_rs is not None:
                try:
                    val = float(gl_rs)
                    if val > 0:
                        tags.extend([f"SL_TYPE:rs", f"SL_VAL:{val}"])
                        if _sl_cc:
                            tags.append("SL_CANDLE_CLOSE:true")
                except Exception:
                    pass

    # 3. Check and append target (TP) — per-strategy pair first, else global
    g = rc.get("global") or {}
    _ps_tt, _ps_tv = ps0.get("default_tp_type"), ps0.get("default_tp_val")
    if _ps_tt and _ps_tv is not None and str(_ps_tv).strip() != "":
        tp_type, tp_val = _ps_tt, _ps_tv
    else:
        tp_type, tp_val = g.get("default_tp_type"), g.get("default_tp_val")
    _tp_cc = ps0.get("default_tp_candle_close")
    if _tp_cc is None or str(_tp_cc).strip() == "":
        _tp_cc = g.get("default_tp_candle_close")
    _tp_cc = (_tp_cc is True) or (str(_tp_cc).lower() == "true")
    if tp_type and tp_val is not None and str(tp_val).strip() != "":
        if tp_type == "trailing_pt":
            if ":" in str(tp_val):
                gap_part, step_part = str(tp_val).split(":", 1)
                gap_val = float(gap_part)
                step_val = float(step_part)
                tags.extend([f"TP_TYPE:trailing_pt", f"TP_VAL:{gap_val}", f"TP_TRAIL_STEP:{step_val}"])
            else:
                gap_val = float(tp_val)
                tags.extend([f"TP_TYPE:trailing_pt", f"TP_VAL:{gap_val}"])
        else:
            tags.extend([f"TP_TYPE:{tp_type}", f"TP_VAL:{tp_val}"])
        if _tp_cc:
            tags.append("TP_CANDLE_CLOSE:true")

    return tags


def daily_loss_breached(strategy, unrealized=0.0, rc=None, mode=None, broker=None):
    """SUPREME check: True if `strategy` has lost >= its unified daily cap today
    (realized + caller's unrealized estimate). Used by BOTH the entry gate (block
    new entries) and pos_monitor (square off open legs). Returns (breached, reason).

    `mode`/`broker` (if given) let the cap be computed against the broker's
    REAL live balance for live positions — see effective_daily_loss_cap()."""
    cap = effective_daily_loss_cap(strategy, rc=rc, mode=mode, broker=broker)
    pnl = _strategy_day_pnl(strategy, unrealized)
    if pnl <= -abs(cap):
        return True, f"RMS daily loss cap ₹{_inr(cap)} hit for '{strategy}' (today's P&L ₹{_inr(pnl)})"
    return False, ""


def capital_headroom(strategy, mode=None):
    """Remaining ₹ capital for `strategy` before its own cap OR the global cap is
    hit (whichever is tighter). None = no cap configured (unlimited). Uses
    order_store entry prices + cached margin only — makes NO live LTP/quote call,
    so it's safe to poll cheaply and to short-circuit a scan loop with.
    mode='live'/'paper' → headroom within that pool only (see capital_in_use)."""
    rc = _risk_cfg()
    strat_cap = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("capital_rs")
    glob_cap = (rc.get("global", {}) or {}).get("capital_rs")
    rooms = []
    if strat_cap is not None:
        try:
            rooms.append(float(strat_cap) - capital_in_use(strategy, mode=mode))
        except Exception:
            pass
    if glob_cap is not None:
        try:
            rooms.append(float(glob_cap) - capital_in_use(None, mode=mode))
        except Exception:
            pass
    return min(rooms) if rooms else None


def gating_status(strategy, unrealized=0.0, mode=None, broker=None):
    """Consolidated "can this strategy take a NEW entry right now?" answer — used
    by the RMS panel AND by the traders' pre-scan short-circuit so a fully-blocked
    strategy stops firing wasteful LTP calls + stops piling up PX 0.00 blocked
    rows. Returns (blocked: bool, reason: str, hard: bool).

    hard=True  → won't reverse today on its own (daily-loss or global drawdown
                 breached) — genuinely "no further entries today".
    hard=False → recoverable intraday (capital fully used; frees up when a
                 position closes).

    `mode`/`broker`, if known by the caller, let the daily-loss cap be computed
    against the broker's real live balance for a live position — see
    effective_daily_loss_cap(). Pre-scan callers that don't know either yet can
    omit them (falls back to the static ₹ cap, same as before).

    Makes NO live LTP/quote call of its own (the broker-balance fetch above IS
    a real API call, but it's cached 20s — see get_broker_balance()) so it's
    still cheap to poll at the usual cadence."""
    breached, why = daily_loss_breached(strategy, unrealized=unrealized, mode=mode, broker=broker)
    if breached:
        return True, why, True
    # Account-level kill-floor / aggregate trailing lock fired today → the day
    # is DONE for new entries, every strategy, every path (2026-07-02 — before
    # this, only webhook_executor checked the flag; strategies could keep
    # entering right after a kill-all squareoff).
    if kill_floor_fired_today():
        return True, "account kill-floor fired today — profit locked, no further entries", True
    pt_hit, pt_why = daily_profit_target_hit(strategy, unrealized=unrealized)
    if pt_hit:
        return True, pt_why, True
    # Max trades/day cap (per-strategy, off by default) — 2026-07-15
    mt_hit, mt_why = daily_max_trades_hit(strategy)
    if mt_hit:
        return True, mt_why, True
    dd_ok, dd_why = check_drawdown(unrealized_pnl=unrealized)
    if not dd_ok:
        return True, dd_why, True
    room = capital_headroom(strategy, mode=mode)
    if room is not None and room <= 0:
        return True, "capital cap fully used (no headroom — frees up when a position closes)", False
    return False, "", False


def reconcile_funds(broker):
    """Read-only health_check.py-style comparison: our own capital_in_use(None)
    vs the broker's actual available funds. Doesn't block anything — just
    reports drift so it can be investigated (manual trades outside the system,
    a fill we didn't record, etc). Returns a dict, never raises."""
    out = {"ok": True, "our_capital_in_use": capital_in_use(None),
           "broker_available": None, "drift_rs": None, "note": ""}
    if broker is None or not hasattr(broker, "funds"):
        out["note"] = "no broker funds() available — skipped"
        return out
    try:
        f = broker.funds() or {}
        avail = f.get("available")
        if avail is None:
            out["note"] = "broker funds() returned nothing usable"
            return out
        out["broker_available"] = float(avail)
    except Exception as e:
        out["note"] = f"broker funds() failed: {e}"
        return out
    out["note"] = "compare manually — 'available' shrinking faster than our capital_in_use grows usually means an untracked fill"
    return out


def shadow_live_enabled(strategy):
    """Diagnostic mode — when True, a PAPER entry also fires a REAL broker order
    in parallel (same price/qty), purely to compare against Dhan's actual
    fill/reject (slippage check, price-band/freeze rejects, etc). The paper
    fill price/P&L recorded to order_store is NEVER touched by the shadow
    order's outcome — it's a side-channel diagnostic, not a fallback.

    OFF by default (False) everywhere — must be explicitly turned on per
    strategy or globally. Intended for short, deliberate testing windows with
    a near-zero account balance so the shadow orders can't actually fill.
    nifty_config.json["_risk"]["global"/"per_strategy"]["shadow_live"]."""
    rc = _risk_cfg()
    v = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("shadow_live")
    if v is None:
        v = (rc.get("global", {}) or {}).get("shadow_live")
    return bool(v)


def hedge_config(strategy):
    """Auto-hedge settings for naked option-SELL strategies (currently
    range_trader). Per-strategy overrides global; absent on both = hedge OFF.
    nifty_config.json["_risk"]["global"/"per_strategy"]["hedge_offset_strikes"
    / "hedge_max_premium_rs"].

    hedge_offset_strikes: MINIMUM strikes further OTM than the sold leg.
    hedge_max_premium_rs: if set, the hedge keeps walking further OTM (beyond
    the minimum above) until it finds a strike whose premium is at or below
    this ₹ amount — cheaper insurance, common ask for NIFTY where strikes are
    close together and a fixed strike-count can still be expensive.
    Whichever of the two lands FURTHER OTM wins (the safer/cheaper one) —
    the minimum is a floor, never a ceiling.

    Returns (offset_strikes:int, max_premium_rs:float|None). offset_strikes==0
    and max_premium_rs is None means hedge is OFF.
    """
    rc = _risk_cfg()
    g = (rc.get("global", {}) or {})
    # Global hedge switch: if disabled globally, return 0, None (hedging completely off)
    # Fixed by Antigravity AI.
    if not g.get("hedge_enabled", True):
        return 0, None

    ps = (rc.get("per_strategy", {}).get(strategy or "", {}) or {})
    offset = ps.get("hedge_offset_strikes")
    if offset is None:
        offset = g.get("hedge_offset_strikes")
    max_prem = ps.get("hedge_max_premium_rs")
    if max_prem is None:
        max_prem = g.get("hedge_max_premium_rs")
    try:
        offset = int(offset) if offset else 0
    except (TypeError, ValueError):
        offset = 0
    try:
        max_prem = float(max_prem) if max_prem else None
    except (TypeError, ValueError):
        max_prem = None
    return offset, max_prem


def liquidity_filter_enabled(strategy):
    """ON by default everywhere — strategy_safety.check_contract_liquidity()
    (live spread/volume/OI gate, any-2-of-3) runs unless explicitly turned
    OFF for this strategy or globally. nifty_config.json["_risk"]["global"/
    "per_strategy"]["liquidity_filter"] (per-strategy overrides global)."""
    rc = _risk_cfg()
    v = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("liquidity_filter")
    if v is None:
        v = (rc.get("global", {}) or {}).get("liquidity_filter")
    return True if v is None else bool(v)


# Strategies whose DESIGN is a positional/overnight hold (ADR-006 lane). This is
# a DURABLE code-level baseline: nifty_config.json gets rewritten by the app and
# has repeatedly dropped the per_strategy["allow_overnight"] key (silently turning
# a positional strategy back into an intraday one → it gets force-squared-off at
# 3:15 / the next night). These IDs are always overnight-allowed regardless of
# what the config currently holds. Config can still turn it ON for other strategies.
# Add a new positional strategy's id here when you build one.
_ALWAYS_OVERNIGHT = {"vrp_condor_v1", "vrp_v1"}


def allow_overnight(strategy):
    """OPT-IN ONLY (default False) — whether THIS strategy's option positions may
    be held past the blanket 3:15 EOD squareoff (i.e. a positional/overnight lane,
    e.g. the VRP weekly-straddle held to expiry — see _ADR/ADR-006).

    True if the id is in the durable `_ALWAYS_OVERNIGHT` set (code-level, can't be
    wiped by a config rewrite) OR
    nifty_config.json["_risk"]["per_strategy"][<sid>]["allow_overnight"] = true.
    There is NO global switch on purpose — this must be turned on per-strategy,
    deliberately, never account-wide. Any config/parse problem → the code-level
    set still decides (fail-safe for the known positional strategies; unknown
    strategies default False → squared off at 3:15 like everything else, never
    silently left open overnight). Expiry-day squareoff, ITM guard and the RMS
    daily-loss breaker still apply regardless of this flag."""
    if (strategy or "") in _ALWAYS_OVERNIGHT:
        return True
    try:
        rc = _risk_cfg()
        v = (rc.get("per_strategy", {}).get(strategy or "", {}) or {}).get("allow_overnight")
        return bool(v) if v is not None else False
    except Exception:
        return False


# ── Expiry-day guards ────────────────────────────────────────────────────────

# On expiry day, close ALL open option positions at this time (not 3:15)
# — avoids last-hour chaos, physical-delivery margin blocks, and ITM panic.
EXPIRY_EOD_HM = (14, 55)

# No new entries on expiry day after this time.
EXPIRY_NO_ENTRY_AFTER_HM = (14, 0)


def is_expiry_day(trad_sym=None, sec_id=None):
    """True if today is the expiry date of this F&O contract.

    Tries two methods (first hit wins):
    1. Parse day from trad_sym — works for stock options ("NIFTY-28Jun2026-...")
    2. sec_id lookup via dhan_master — works for index options ("NIFTY-Jun2026-...")

    Returns False on any parse failure (safe default — guards run on confirmed
    expiry-day contracts only, never misfire on a non-expiry leg).
    """
    import datetime as _dt
    today = _dt.date.today()

    # Method 1: day present in trad_sym (stock options include DDMonYYYY)
    if trad_sym:
        try:
            parts = trad_sym.split("-")
            dmy = parts[1]          # "28Jun2026" (stock) or "Jun2026" (index)
            if len(dmy) > 7:        # day prefix present
                day = int(dmy[:2])
                mon = _dt.datetime.strptime(dmy[2:5], "%b").month
                year = int(dmy[5:])
                return _dt.date(year, mon, day) == today
        except Exception:
            pass

    # Method 2: dhan_master exact expiry (reliable for both index + stock)
    if sec_id:
        try:
            import sys as _s, os as _o
            _root = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
            if _root not in _s.path:
                _s.path.insert(0, _root)
            import dhan_master
            expiry = dhan_master.get_expiry_for_sec_id(sec_id)
            return expiry == today if expiry else False
        except Exception:
            pass

    return False


def option_is_itm(trad_sym, spot_price):
    """True if a SHORT option position (SELL) is currently In-The-Money.

    trad_sym : e.g. "NIFTY-Jun2026-23900-PE" or "MARUTI-28Jun2026-13600-PE"
    spot_price: live underlying LTP

    For SELL PE: ITM when spot < strike (option has intrinsic value → we lose)
    For SELL CE: ITM when spot > strike

    Returns False on any parse failure.
    """
    try:
        parts = trad_sym.split("-")
        strike = float(parts[-2])
        opt_type = parts[-1].upper()   # "PE" or "CE"
        if opt_type == "PE":
            return spot_price < strike
        if opt_type == "CE":
            return spot_price > strike
    except Exception:
        pass
    return False
