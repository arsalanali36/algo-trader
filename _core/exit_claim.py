"""In-process idempotency guard for EXIT orders — stops two concurrent exit
engines from both firing a close on the SAME (strategy, contract, side) within a
few seconds.

WHY (BANKNIFTY hedged straddle, 2026-08-17, LESSONS #176): a hedged group can be
closed by TWO independent engines that both run as daemon threads in the SAME
algo-monitor process:

  1. auto_straddle_loop → _run_position_exit_rules → execution_gateway.execute_basket_exit
     → execute_exit   (the ₹4k basket GROUP_SL / GROUP_TARGET exit)
  2. pos_monitor_loop  → _do_squareoff   (RMS daily profit-target / EOD 3:15 / SL/TP;
     cascades to group siblings, appending "_GROUP")

Both build "this leg is open" from order_store at cycle start, then both fire a
buy-to-close on the same short leg. The broker fill hasn't reflected yet (~8s
async-confirm lag, TRAP #63), so a broker-side flat-check (is_flat_fresh) still
reads "not flat" for the loser → a DUPLICATE close order lands. That extra
buy-to-close has no matching short left → a phantom naked long → an extra flatten
order → real wasted brokerage + STT (₹255 + ₹247 on 2026-08-17). Only bites the
LIVE hedged twin (the paper twin has just one exit engine).

is_flat_fresh can't win this race (it round-trips the broker, which lags). This
guard is INSTANT and in-process: first engine to claim (strategy, sec_id, side)
owns that close; the other skips it immediately — no broker round-trip.

Both loops share this process's memory, so a thread-safe module-global dict is
enough (no file / db). TTL is short so a genuine later re-close of the same
contract (a fresh re-entry round-trip) is never blocked — no strategy here
legitimately closes the same contract+side twice within the TTL.

Key = (mode, strategy, sec_id, exit_side). Strategy is IN the key on purpose so
two DIFFERENT strategies holding the same contract in the same direction can each
close their own leg (they are genuinely separate positions — TRAP #145 family).
"""
import threading
import time as _time

_lock = threading.Lock()
_claims = {}          # key -> claim_ts (monotonic)
DEFAULT_TTL = 15.0    # seconds — covers the <5s two-engine race, well under any
                      # legit re-entry+re-exit of the same contract by one strategy


def _key(mode, strategy, sec_id, exit_side):
    return "%s|%s|%s|%s" % (
        str(mode or "paper").lower(),
        str(strategy or ""),
        str(sec_id or ""),
        str(exit_side or "").upper(),
    )


def claim(strategy, sec_id, exit_side, mode="paper", ttl=DEFAULT_TTL):
    """Atomically claim the right to fire an exit on (strategy, sec_id, side).

    Returns True  → you won, PROCEED to place the close order.
    Returns False → another engine claimed it within the TTL, SKIP (it's already
                    being closed) so you don't fire a duplicate.

    Fail-OPEN: if the leg can't be keyed (missing sec_id/side) it returns True —
    a dedup guard must never itself strand a needed exit.
    """
    if not sec_id or not exit_side:
        return True
    k = _key(mode, strategy, sec_id, exit_side)
    now = _time.monotonic()
    with _lock:
        ts = _claims.get(k)
        if ts is not None and (now - ts) < ttl:
            return False
        _claims[k] = now
        # opportunistic prune so the dict can't grow unbounded across a session
        if len(_claims) > 256:
            for kk in [kk for kk, tt in _claims.items() if (now - tt) >= ttl]:
                _claims.pop(kk, None)
        return True


def release(strategy, sec_id, exit_side, mode="paper"):
    """Release a claim — call when the close order did NOT place / failed, so a
    legitimate retry on the very next cycle isn't blocked. No-op if not held."""
    if not sec_id or not exit_side:
        return
    with _lock:
        _claims.pop(_key(mode, strategy, sec_id, exit_side), None)
