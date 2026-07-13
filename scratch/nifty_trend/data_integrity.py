"""
data_integrity.py — pre-significance data-trust checks for the hunt pipeline.

Two reusable guards, plus a combined `check()` that run_hunt.py bolts into every
strategy's meta.json as a `data_integrity` block. If either guard FAILS, the
strategy is NOT shippable even if significance + Monte-Carlo pass — same severity
as a significance fail ("Honest result: data not trustworthy, re-verify").

Why this exists — TRAP #109: the rolling-ATM marking bug produced a strategy that
PASSED significance + MC while its underlying option P&L was physically impossible
(a short-vol book showing a large GAIN on an 8% crash day). Significance can't see
a corrupt data source; these two checks can.

  1. assert_coverage(frame, reference_days, min_pct=0.9)  — TRAP #107 generalized.
     An inner-join against a still-filling data source silently truncates to the
     overlap window and yields a bogus short-history backtest. Refuse any frame
     whose day-coverage is below `min_pct` of the reference.

  2. worst_day_sanity(trades)  — TRAP #109 generalized.
     Take the single biggest ADVERSE spot move in the trade set; a long-option
     position can lose at most its premium there — it can NEVER post a large gain
     from a move against it. A strongly-positive P&L on the worst adverse move is
     the exact fingerprint of a marking/held-strike bug. Fail loud.
"""
import pandas as pd


# ---------------------------------------------------------------- coverage (1b)
def assert_coverage(frame_days, reference_days, min_pct=0.9, label=""):
    """Generic TRAP #107 guard. `frame_days`/`reference_days` = counts of distinct
    trading days (or the frames themselves — a DataFrame with a `day` column, or a
    Datetime column, is accepted and reduced to nunique). Returns (ok, detail)."""
    fd = _ndays(frame_days)
    rd = _ndays(reference_days)
    if rd <= 0:
        return True, {"check": "coverage", "label": label, "ok": True,
                      "note": "no reference days", "frame_days": fd, "ref_days": rd}
    pct = fd / rd
    ok = pct >= min_pct
    return ok, {"check": "coverage", "label": label, "ok": bool(ok),
                "frame_days": fd, "ref_days": rd, "pct": round(pct, 4),
                "min_pct": min_pct,
                "note": "" if ok else f"only {fd}/{rd} days ({pct:.1%}) — source still "
                        f"filling; inner-join would give a truncated/bogus backtest"}


def _ndays(x):
    if x is None:
        return 0
    if isinstance(x, (int, float)):
        return int(x)
    # a DataFrame/Series
    try:
        if hasattr(x, "day"):
            return int(x.day.nunique())
        if hasattr(x, "Datetime"):
            return int(pd.to_datetime(x.Datetime).dt.date.nunique())
    except Exception:
        pass
    try:
        return int(len(x))
    except Exception:
        return 0


# ------------------------------------------------------------ worst-day (1a/#109)
def worst_day_sanity(trades, structure="long_option", tol_frac=0.05):
    """TRAP #109 guard. For a long-option (ATM-buy) trade set, find the single
    trade with the biggest ADVERSE spot move (move against the position's
    direction) and assert its realised P&L isn't a large positive — a long option
    cannot profit from a move against it (max loss = premium). `structure` other
    than 'long_option' → skip with ok=True (bound only holds for bought options).

    Each trade needs: side ('LONG'/'SHORT' or containing CE/PE), entry_spot,
    exit_spot, qty, pnl. Returns (ok, detail)."""
    if structure != "long_option":
        return True, {"check": "worst_day", "ok": True, "structure": structure,
                      "note": f"skipped — bound only defined for long_option (got {structure})"}
    worst = None  # (adverse_move_abs, trade)
    for t in trades or []:
        try:
            es, xs = float(t.get("entry_spot", t.get("entry"))), float(t.get("exit_spot", t.get("exit")))
            move = xs - es
            bullish = _is_bullish(t.get("side", ""))
            adverse = (bullish and move < 0) or ((not bullish) and move > 0)
            if not adverse:
                continue
            amove = abs(move)
            if worst is None or amove > worst[0]:
                worst = (amove, t)
        except Exception:
            continue
    if worst is None:
        return True, {"check": "worst_day", "ok": True,
                      "note": "no adverse-move trade found (nothing to falsify)"}
    amove, t = worst
    qty = float(t.get("qty", 0) or 0)
    pnl = float(t.get("pnl", 0) or 0)
    # a long option on an ADVERSE move must lose (bounded). A pnl that's a
    # meaningfully-positive fraction of the intrinsic adverse move is impossible.
    intrinsic = amove * qty
    threshold = tol_frac * max(intrinsic, 1.0)
    ok = pnl <= threshold
    return ok, {"check": "worst_day", "ok": bool(ok),
                "worst_adverse_move_pts": round(amove, 1), "qty": qty,
                "trade_pnl": round(pnl, 1), "intrinsic_move_rs": round(intrinsic, 0),
                "threshold_rs": round(threshold, 0),
                "side": t.get("side", ""), "entry_dt": t.get("entry_dt", ""),
                "note": "" if ok else f"long option posted +{pnl:.0f} on a {amove:.1f}pt move "
                        f"AGAINST it (max it can lose = premium; a gain here is impossible) "
                        f"— data/marking bug, do NOT ship (TRAP #109 shape)"}


def _is_bullish(side):
    s = str(side).upper()
    if "CE" in s and "PE" not in s:
        return True
    if "PE" in s and "CE" not in s:
        return False
    return "LONG" in s or "BUY" in s  # LONG spot signal -> long CE


# ------------------------------------------------------------------- combined
def check(bs_trades, structure="long_option", frame_days=None, reference_days=None,
          min_pct=0.9):
    """Run both guards; return a meta-embeddable dict. `shippable` is False if
    either fails. run_hunt.py: only reached after significance+MC already passed,
    so a False here means 'significant BUT data not trustworthy — re-verify'."""
    wd_ok, wd = worst_day_sanity(bs_trades, structure=structure)
    checks = {"worst_day": wd}
    all_ok = wd_ok
    if frame_days is not None and reference_days is not None:
        cov_ok, cov = assert_coverage(frame_days, reference_days, min_pct=min_pct,
                                      label="bs_frame")
        checks["coverage"] = cov
        all_ok = all_ok and cov_ok
    return {"shippable": bool(all_ok), "checks": checks,
            "verdict": "data trustworthy" if all_ok
                       else "DATA NOT TRUSTWORTHY — re-verify (do not ship)"}
