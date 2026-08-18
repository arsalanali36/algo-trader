"""
leg_collision.py — keep two strategies off the SAME option contract.

WHY (live, hedged BNF strategies — user-reported 2026-08-18): at the BROKER,
option positions are FUNGIBLE by contract, not by strategy. If strategy A is
SHORT 57300-CE and strategy B places ANY order on 57300-CE, the broker nets it
against A's position:
  • B BUYs 57300-CE (its protective wing) → broker net -1 → 0 : A's short is
    silently CLOSED at the broker. A still shows it open in order_store, but at
    the broker A is now a NAKED long wing → A's hedged structure is BROKEN, and B
    never actually got a wing. ("dusri strategy ka leg square off ho gaya" —
    exactly the live blunder observed.)
  • B SELLs 57300-CE (its own short) → broker net -2 : two strategies' P&L,
    exit-accounting and combined-MTM ₹-basket monitoring all now key off one
    fungible lot.
Either way at least one strategy's structure / accounting breaks.

FIX: before opening a leg, if the resolved contract (sec_id) is ALREADY open for
a DIFFERENT strategy, step one strike further OTM and re-resolve — until the leg
lands on a contract no other strategy holds. Each strategy then owns DISTINCT
contracts → no broker-level annihilation, clean per-strategy accounting.

Reads only order_store (today's OPEN rows). Contract resolution is INJECTED by
the caller (dhan_master.get_option_contract) so this stays a thin policy layer,
reusable by ANY multi-leg strategy. FAIL-OPEN on any read error (can't check →
behave exactly as before, never worse).
"""

from datetime import datetime, timedelta, timezone


def _today_ist():
    return (datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")


def occupied_sec_ids(exclude_strategy="", live_only=True):
    """sec_ids currently OPEN at the broker for any strategy OTHER than
    `exclude_strategy` (today's order_store; CAPITAL_BLOCKED / zero-qty excluded).

    live_only=True (default): only LIVE positions count. Broker fungibility is a
    LIVE-only phenomenon — a PAPER leg never reaches the broker, so it can't net
    against anything. This is critical: the paper twins (straddle_920 etc.) trade
    the SAME strikes as their live siblings; counting their paper legs would
    false-block the live entry. Only real broker positions occupy a contract.

    Empty set on any error (fail-open — never blocks an entry just because the
    check failed)."""
    out = set()
    try:
        import order_store
        data = order_store.trades_for(_today_ist())
        for p in (data.get("open") or []):
            if (p.get("strategy") or "") == (exclude_strategy or ""):
                continue
            if live_only and str(p.get("mode") or "").lower() != "live":
                continue      # paper leg — no broker position, can't net
            if "CAPITAL_BLOCKED" in (p.get("tags") or []):
                continue      # never reached the broker — not a real position
            try:
                if abs(int(p.get("qty") or 0)) <= 0:
                    continue
            except (TypeError, ValueError):
                continue
            sid = str(p.get("sec_id") or "")
            if sid:
                out.add(sid)
    except Exception:
        return set()
    return out


def clear_leg(symbol, spot, opt_type, offset, avoid, resolve_fn,
              max_shift=4, log=print):
    """Resolve an option leg at `offset` that is NOT in `avoid` (a set of sec_ids
    already held by OTHER strategies, plus this strategy's own already-picked
    legs). On collision, step one strike further OTM (offset+1 — get_option_contract
    already inverts the sign for PE, so +1 is 'more OTM' for both CE and PE) and
    retry, up to `max_shift` steps.

    resolve_fn(symbol, spot, opt_type, off) -> (sec_id, trad_sym, lot_size).

    Returns (sec_id, trad_sym, lot_size, used_offset) for a CLEAR contract, or
    (None, None, None, None) if every candidate within max_shift collides / fails
    to resolve. The caller MUST treat None as 'abort this leg' — opening a
    colliding leg would break another live strategy's structure, so no trade is
    strictly better than a broken hedge.

    If `avoid` is empty the base offset is returned unchanged → ZERO behaviour
    change when no other strategy is in the market (the common case)."""
    try:
        base = resolve_fn(symbol, spot, opt_type, offset)
    except Exception as e:
        log(f"[COLLISION] {symbol} {opt_type} off={offset} base resolve err: {e}")
        return None, None, None, None
    if not avoid:
        return base[0], base[1], base[2], offset
    for k in range(int(max_shift) + 1):
        off = offset + k
        try:
            sec, tsym, lot = resolve_fn(symbol, spot, opt_type, off)
        except Exception:
            sec = tsym = lot = None
        if not sec:
            break
        if str(sec) not in avoid:
            if k > 0:
                log(f"[COLLISION] {symbol} {opt_type} off={offset} collided with "
                    f"another strategy's open leg — shifted to off={off} ({tsym})")
            return sec, tsym, lot, off
    log(f"[COLLISION] {symbol} {opt_type} off={offset}: no clear strike within "
        f"{max_shift} steps (all held by other strategies) — leg ABORTED (no naked/shared)")
    return None, None, None, None


if __name__ == "__main__":
    # Pure self-test of the shift logic with a fake resolver (offset -> strike).
    # Strikes: off k -> ('SEC%d' % k). Another strategy holds SEC0 and SEC1.
    def _fake(sym, spot, ot, off):
        return (f"SEC{off}", f"{sym}-{ot}-{off}", 25)

    # no avoid -> unchanged base
    assert clear_leg("BNF", 56600, "CE", 0, set(), _fake)[0] == "SEC0"
    # SEC0 occupied -> shift to SEC1... SEC1 occupied too -> SEC2
    r = clear_leg("BNF", 56600, "CE", 0, {"SEC0", "SEC1"}, _fake, max_shift=4)
    assert r[0] == "SEC2" and r[3] == 2, r
    # occupied leg already clear at base -> no shift
    r = clear_leg("BNF", 56600, "CE", 3, {"SEC0", "SEC1"}, _fake)
    assert r[0] == "SEC3" and r[3] == 3, r
    # everything within max_shift occupied -> abort (None)
    occ = {f"SEC{k}" for k in range(0, 20)}
    assert clear_leg("BNF", 56600, "CE", 0, occ, _fake, max_shift=4)[0] is None
    # resolver runs out of strikes -> abort
    def _fake_end(sym, spot, ot, off):
        return (None, None, None) if off > 2 else (f"SEC{off}", "x", 25)
    assert clear_leg("BNF", 56600, "CE", 0, {"SEC0", "SEC1", "SEC2"}, _fake_end, max_shift=6)[0] is None
    print("leg_collision self-test ok — 5 pass")
