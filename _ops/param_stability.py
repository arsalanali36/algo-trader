"""Days-since-core-params-last-changed, per strategy — from the config audit log.

Answers the user's question: "kitne din se strategy ke core params (entry/exit
conditions, SL, target) me ched-chad NAHI hui" = kitne din ka data SAME params pe
collect hua = us data pe kitna bharosa kiya jaa sakta hai.

Source of truth = `data/rms_audit_log.json` (written by
trader_dashboard._config_audit_record on EVERY /api/config save). Each entry is
{ts, scope, field, old, new} (+ optional section). `scope` = the strategy's
config_key (or 'global'); `field` = the changed param. So the most-recent entry
whose scope == a strategy's config_key AND field is a CORE trading param tells us
when that strategy was last "touched".

REAL history — not forward-tracking. Only limitation: history begins when the audit
log started recording (its earliest ts); a strategy with no change since then is
reported as ">= N days stable" (we honestly can't see before tracking began).

Display-only: reads a file, computes dates, returns a dict. No config/order/risk write.
"""
import json
import os
from datetime import datetime, date

# Operational / cosmetic fields — changing THESE is not "touching the strategy's core
# trading params". Everything else in a strategy's own scope counts (entry/exit window,
# ATR/RR/TP/SL knobs, strike offsets, sizing, hedge, SL mode/values, profit target...).
_NON_CORE = {
    "active", "mode", "shadow_live", "shadow_live_enabled", "desc", "description",
    "name", "note", "notes", "tier", "role", "status", "keep_active",
    "last_run", "updated", "created", "liquidity_filter_enabled",
}


def _is_core(field):
    f = str(field or "")
    if not f or f in _NON_CORE:
        return False
    if f.startswith("IMG") or f.startswith("img") or f.startswith("_"):
        return False   # image tags, internal (_module/_lang/_...) — not trading params
    return True


def _date_of(ts):
    try:
        return datetime.strptime(str(ts)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def compute(audit_path, config_keys, today=None, hist_cap=20):
    """-> {config_key: {days, since, ever_changed, tracked_since, history}}.

    days         = days since this strategy's OWN core params last changed. If it never
                   changed within the tracked history, days counts from tracking start
                   (i.e. a lower bound — "at least" this stable).
    since        = ISO date of the last core-param change (None if none on record).
    ever_changed = was any core-param change for this strategy seen in the log.
    tracked_since= earliest date the audit log has any entry (history horizon).
    history      = [{ts, field, old, new}] core-param changes for this strategy, newest
                   first (capped) — the actual "kya-kya kab badla" for the hover.
    """
    today = today or date.today()
    try:
        log = json.load(open(audit_path, encoding="utf-8")) if os.path.exists(audit_path) else []
    except Exception:
        log = []
    if not isinstance(log, list):
        log = []

    all_dates = [d for d in (_date_of(e.get("ts")) for e in log if isinstance(e, dict)) if d]
    tracked_since = min(all_dates).isoformat() if all_dates else None

    cks = set(config_keys or [])
    last = {}   # ck -> latest core-change date
    hist = {}   # ck -> [ {ts, field, old, new} ... ]
    for e in log:
        if not isinstance(e, dict):
            continue
        scope, field = e.get("scope"), e.get("field")
        if scope not in cks or not _is_core(field):
            continue
        d = _date_of(e.get("ts"))
        if d and (scope not in last or d > last[scope]):
            last[scope] = d
        hist.setdefault(scope, []).append({
            "ts": e.get("ts"), "field": field,
            "old": e.get("old"), "new": e.get("new"),
        })
    for k in hist:
        hist[k].sort(key=lambda x: str(x.get("ts") or ""), reverse=True)   # newest first
        hist[k] = hist[k][:hist_cap]

    ts_date = _date_of(tracked_since) if tracked_since else None
    out = {}
    for ck in cks:
        ch = last.get(ck)
        base = ch or ts_date
        days = (today - base).days if base else None
        if days is not None and days < 0:
            days = 0
        out[ck] = {
            "days": days,
            "since": (ch.isoformat() if ch else None),
            "ever_changed": ch is not None,
            "tracked_since": tracked_since,
            "history": hist.get(ck, []),
        }
    return out
