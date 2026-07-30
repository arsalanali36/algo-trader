"""
fii_flow_view.py — DISPLAY-ONLY reader for the FII/DII flow dashboard (/fii-flow).

Merges the two self-collected NSE lakes into one compact columnar payload the
frontend charts read:
  - fii_flow.csv  (participant OI: FII/DII/Pro/Client index futures net + LSR,
                   FII index-options CE/PE net, FII/DII cash net)  — written by
                   `_ops/fii_flow.py`  (daily NSE participant-OI + cash reports)
  - chain_pcr.csv (NIFTY spot + near-expiry PCR + max-pain)         — written by
                   `_ops/chain_pcr.py` (FO bhavcopy)

Both paths come from `fii_flow.LAKE` (single source, Rule 6B — never re-hardcode
the lake location here). mtime-cached on (master_mtime, pcr_mtime) so repeated
API hits re-parse only when the collector actually appends a new day.

RULE 10 note: this is a CONTEXT map, not a signal. The next-day-direction
backtest on this exact data FAILED (significance + OOS — see memory
`project_code3b_fii_flow_confluence`). This module only DISPLAYS the flows; it
never gates, sizes, or orders. No order/risk/live path is imported.
"""
import os
import csv

# Reuse the collector's lake path (single source — do not re-hardcode). fii_flow
# is import-safe (its main() is __main__-guarded).
import fii_flow as _ff  # noqa: E402

MASTER_CSV = _ff.MASTER_CSV
PCR_CSV = os.path.join(_ff.LAKE, "chain_pcr.csv")

# Output column order the frontend expects (columnar rows are parallel to this).
COLS = ["date", "fii_cash", "dii_cash", "fii_fut", "dii_fut", "pro_fut",
        "cli_fut", "fii_lsr", "fii_ce", "fii_pe", "spot", "pcr", "maxpain"]

_cache = {"key": None, "payload": None}


def _num(v, dp=None):
    """'' / None / junk -> None; else int (dp=None) or rounded float."""
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return round(x, dp) if dp is not None else int(round(x))
    except (ValueError, TypeError):
        return None


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def series():
    """Return {'cols': [...], 'rows': [[...], ...], 'meta': {...}}.

    rows are sorted ascending by date, columnar-parallel to COLS. mtime-cached.
    """
    key = (_mtime(MASTER_CSV), _mtime(PCR_CSV))
    if _cache["key"] == key and _cache["payload"] is not None:
        return _cache["payload"]

    # chain_pcr keyed by date (spot / pcr_oi_near / max_pain_near)
    pcr = {}
    if os.path.exists(PCR_CSV):
        with open(PCR_CSV, newline="") as f:
            for r in csv.DictReader(f):
                pcr[r.get("date")] = r

    rows = []
    if os.path.exists(MASTER_CSV):
        with open(MASTER_CSV, newline="") as f:
            for r in csv.DictReader(f):
                d = r.get("date")
                if not d:
                    continue
                p = pcr.get(d, {})
                rows.append([
                    d,
                    _num(r.get("fii_cash_net"), 1),
                    _num(r.get("dii_cash_net"), 1),
                    _num(r.get("fii_fut_idx_net")),
                    _num(r.get("dii_fut_idx_net")),
                    _num(r.get("pro_fut_idx_net")),
                    _num(r.get("client_fut_idx_net")),
                    _num(r.get("fii_fut_idx_lsr"), 2),
                    _num(r.get("fii_opt_idx_ce_net")),
                    _num(r.get("fii_opt_idx_pe_net")),
                    _num(p.get("spot"), 1),
                    _num(p.get("pcr_oi_near"), 3),
                    _num(p.get("max_pain_near")),
                ])
    rows.sort(key=lambda x: x[0])

    def _cov(idx):
        got = [r[0] for r in rows if r[idx] is not None]
        return {"n": len(got), "from": got[0] if got else None}

    payload = {
        "cols": COLS,
        "rows": rows,
        "meta": {
            "n": len(rows),
            "first": rows[0][0] if rows else None,
            "last": rows[-1][0] if rows else None,
            "cash_cov": _cov(COLS.index("fii_cash")),
            "spot_cov": _cov(COLS.index("spot")),
            "fut_cov": _cov(COLS.index("fii_fut")),
        },
    }
    _cache["key"] = key
    _cache["payload"] = payload
    return payload


if __name__ == "__main__":
    import json
    s = series()
    print(json.dumps(s["meta"], indent=2))
    print("rows:", len(s["rows"]), "cols:", s["cols"])
