"""broker_ledger.py — balance-over-time (ledger) store for the RMS Broker
Balances panel. DISPLAY-ONLY (no order / risk / trading path).

Two sources, merged for the graph + table (user chose "auto snapshot + CSV
upload", 2026-08-03):

  1. AUTO DAILY SNAPSHOT — once/day we record each broker's live balance
     (Cash / Collateral / Available / Total Margin) via
     risk_gate.get_broker_balance(). Builds a balance-over-time series going
     forward; a fund add-in shows up as a jump in the line.
     Store: data/broker_balance_history.json
       {"dhan": [{"date","ts","cash","collateral","available","total_margin"}], "kite": [...]}

  2. CSV LEDGER UPLOAD — the user downloads the broker's own ledger/statement
     CSV (Zerodha Console → Funds → Statement, or Dhan ledger) and uploads it.
     Gives the REAL historical closing-balance per day + the actual fund
     deposit/withdrawal events ("kab-kab fund add kiya") from day one, which no
     API exposes (Kite has no ledger API). Parser is tolerant of column naming.
     Store: data/broker_ledger_<broker>.json

The view() payload feeds one chart with two tabs (Dhan / Zerodha): a balance
line (CSV closing-balance preferred, else snapshot total) + fund-in/out markers
+ a table. No Dhan/Kite order path touched — snapshot only reads balances.
"""
import os as _o
import sys as _s
import csv as _csv
import io as _io
import json as _json
import re as _re
import threading as _threading
from datetime import datetime as _dtn

_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import _paths  # noqa: F401  (root + _core/_data on sys.path)

ROOT = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
_HIST_PATH = _o.path.join(ROOT, "data", "broker_balance_history.json")
_LEDGER_PATH = _o.path.join(ROOT, "data", "broker_ledger_{}.json")
_BROKERS = ("dhan", "kite")
_lock = _threading.Lock()


# ---------------------------------------------------------------- io utils ----
def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    """Atomic write + .bak sibling (VPS cleanups have wiped gitignored user-data
    before — restore-on-corrupt pattern, same as stat_views)."""
    try:
        _o.makedirs(_o.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(obj, f, ensure_ascii=False, indent=2)
        _o.replace(tmp, path)
        if obj:
            try:
                with open(path + ".bak", "w", encoding="utf-8") as f:
                    _json.dump(obj, f, ensure_ascii=False)
            except Exception:
                pass
    except Exception as e:
        print("[broker_ledger] write fail:", path, e, flush=True)


def _read_hist():
    d = _read_json(_HIST_PATH, None)
    if not d:  # main missing/corrupt → self-heal from .bak
        d = _read_json(_HIST_PATH + ".bak", None)
    if not isinstance(d, dict):
        d = {}
    for b in _BROKERS:
        d.setdefault(b, [])
    return d


# --------------------------------------------------------------- snapshot ----
def _today():
    return _dtn.now().strftime("%Y-%m-%d")


def snapshot(force=False):
    """Record today's live balance for both brokers (one row/day/broker; a repeat
    same-day call overwrites today's row with the latest values). Reads only —
    risk_gate.get_broker_balance() is the same cached source the RMS cap uses."""
    try:
        import risk_gate
    except Exception as e:
        print("[broker_ledger] risk_gate import fail:", e, flush=True)
        return {"ok": False, "error": str(e)}
    today = _today()
    ts = int(_dtn.now().timestamp())
    with _lock:
        hist = _read_hist()
        wrote = False
        for b in _BROKERS:
            try:
                bal = risk_gate.get_broker_balance(b) or {}
            except Exception:
                bal = {}
            if not bal.get("ok"):
                continue  # unknown ≠ zero — never record a bogus 0 balance (Rule 7 spirit)
            row = {
                "date": today, "ts": ts,
                "cash": bal.get("cash"),
                "collateral": bal.get("collateral"),
                "available": bal.get("available"),
                "total_margin": bal.get("total_margin"),
            }
            rows = hist[b]
            if rows and rows[-1].get("date") == today:
                rows[-1] = row          # overwrite today's snapshot with latest
            else:
                rows.append(row)
            wrote = True
        if wrote:
            _write_json(_HIST_PATH, hist)
        return {"ok": True, "date": today}


def snapshot_if_due():
    """Take a snapshot only if today's isn't recorded yet (once/day). Cheap guard
    — safe to call on every RMS-tab open."""
    try:
        hist = _read_hist()
        today = _today()
        for b in _BROKERS:
            rows = hist.get(b) or []
            if not rows or rows[-1].get("date") != today:
                return snapshot()
        return {"ok": True, "skipped": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ------------------------------------------------------------- CSV ledger ----
_FUND_RX = _re.compile(
    r"fund|pay[\s\-]?in|pay[\s\-]?out|deposit|withdraw|bank\s*rec|"
    r"received|transfer|neft|imps|rtgs|upi|opening\s*bal",
    _re.I)
_DATE_FMTS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y",
              "%d %b %Y", "%d-%b-%y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S",
              "%d-%m-%Y %H:%M:%S")


def _norm_key(k):
    return _re.sub(r"[^a-z0-9]", "", str(k or "").lower())


def _parse_date(s):
    s = str(s or "").strip()
    if not s:
        return None
    # trim a trailing time if present ("2026-04-01 09:30" → "2026-04-01")
    for fmt in _DATE_FMTS:
        try:
            return _dtn.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    # last resort: first 10 chars look like ISO?
    if _re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    return None


def _to_float(v):
    try:
        s = str(v).replace(",", "").replace("₹", "").strip()
        if s in ("", "-", "--"):
            return 0.0
        neg = s.startswith("(") and s.endswith(")")   # accounting negatives
        s = s.strip("()")
        return -float(s) if neg else float(s)
    except Exception:
        return 0.0


def _find_col(norm_headers, *needles):
    """First header whose normalized name contains any needle."""
    for nk, orig in norm_headers:
        for n in needles:
            if n in nk:
                return orig
    return None


def _rows_to_ledger(all_rows):
    """Shared 2D-rows → ledger parser (fed by BOTH the CSV and the XLSX path).
    Tolerant of Zerodha statement / Dhan ledger column naming + a preamble before
    the header (Zerodha xlsx has Client-ID / 'Ledger for…' rows first) + a leading
    empty column. Returns (rows, warning). rows sorted by date:
      {date, particulars, debit, credit, amount(=credit-debit), balance, fund}
    fund=True when the row is a deposit/withdrawal (not a trade settlement)."""
    all_rows = [r for r in all_rows if any(str(c if c is not None else "").strip() for c in r)]
    if not all_rows:
        return [], "empty file"
    # header = first row containing a date-ish AND a debit/credit/balance-ish col
    # (scan generously — Zerodha xlsx preamble pushes it a few rows down)
    hidx = 0
    for i, r in enumerate(all_rows[:15]):
        nk = [_norm_key(c) for c in r]
        has_date = any("date" in k or "particular" in k or "narration" in k for k in nk)
        has_amt = any(("debit" in k or "credit" in k or "balance" in k or k == "dr" or k == "cr") for k in nk)
        if has_date and has_amt:
            hidx = i
            break
    headers = list(all_rows[hidx])
    norm_headers = [(_norm_key(h), h) for h in headers]
    date_c = _find_col(norm_headers, "postingdate", "valuedate", "txndate", "date")
    bal_c = _find_col(norm_headers, "netbalance", "runningbalance", "closingbalance", "balance")
    deb_c = _find_col(norm_headers, "debit", "withdrawal", "dr")
    cred_c = _find_col(norm_headers, "credit", "deposit", "cr")
    desc_c = _find_col(norm_headers, "particular", "narration", "description", "remark", "voucher", "detail")
    if not date_c:
        return [], "no date column found — is this a ledger file (Zerodha statement / Dhan ledger)?"
    out = []
    for row in all_rows[hidx + 1:]:
        r = dict(zip(headers, row))
        d = _parse_date(r.get(date_c))
        if not d:
            continue                      # rows without a date (Opening Balance, totals) skipped
        deb = _to_float(r.get(deb_c)) if deb_c else 0.0
        cred = _to_float(r.get(cred_c)) if cred_c else 0.0
        desc = str(r.get(desc_c) if r.get(desc_c) is not None else "").strip() if desc_c else ""
        _bv = r.get(bal_c)
        bal = _to_float(_bv) if (bal_c and str(_bv if _bv is not None else "").strip()) else None
        amt = round(cred - deb, 2)
        out.append({
            "date": d, "particulars": desc[:120],
            "debit": round(deb, 2), "credit": round(cred, 2),
            "amount": amt, "balance": bal,
            "fund": bool(_FUND_RX.search(desc)) if desc else False,
        })
    out.sort(key=lambda x: (x["date"], ))
    warn = "" if out else "0 rows parsed — check the file format"
    return out, warn


def parse_ledger_csv(csv_text):
    """Tolerant broker-ledger CSV parser → _rows_to_ledger."""
    try:
        rdr = _csv.reader(_io.StringIO(csv_text.lstrip("﻿")))
        return _rows_to_ledger(list(rdr))
    except Exception as e:
        return [], f"parse error: {e}"


def parse_ledger_xlsx(file_bytes):
    """Zerodha/Dhan ledger XLSX (the native download format) → _rows_to_ledger.
    Picks the first sheet with data; cells (datetime/float) stringify cleanly for
    _parse_date/_to_float."""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(_io.BytesIO(file_bytes), data_only=True)
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            if any(any(str(c if c is not None else "").strip() for c in r) for r in rows):
                return _rows_to_ledger(rows)
        return [], "empty workbook"
    except Exception as e:
        return [], f"xlsx parse error: {e}"


def _merge_ledger(broker, rows, warn):
    """Merge parsed ledger rows into data/broker_ledger_<broker>.json. Idempotent:
    dedupe by (date, particulars, debit, credit) so re-uploading the same statement
    is a no-op; a superset (more history) just adds the new rows."""
    broker = (broker or "").lower()
    if broker not in _BROKERS:
        return {"ok": False, "error": f"unknown broker {broker}"}
    if not rows:
        return {"ok": False, "error": warn or "no rows"}
    path = _LEDGER_PATH.format(broker)
    with _lock:
        existing = _read_json(path, None) or _read_json(path + ".bak", None) or []
        if not isinstance(existing, list):
            existing = []
        seen = {(r["date"], r.get("particulars", ""), r.get("debit", 0), r.get("credit", 0))
                for r in existing}
        added = 0
        for r in rows:
            k = (r["date"], r.get("particulars", ""), r.get("debit", 0), r.get("credit", 0))
            if k not in seen:
                seen.add(k)
                existing.append(r)
                added += 1
        existing.sort(key=lambda x: x["date"])
        _write_json(path, existing)
    return {"ok": True, "added": added, "total": len(existing), "warn": warn}


def import_ledger(broker, csv_text):
    """Public: import a CSV ledger."""
    rows, warn = parse_ledger_csv(csv_text)
    return _merge_ledger(broker, rows, warn)


def import_ledger_xlsx(broker, file_bytes):
    """Public: import an XLSX ledger (Zerodha/Dhan native download)."""
    rows, warn = parse_ledger_xlsx(file_bytes)
    return _merge_ledger(broker, rows, warn)


# ------------------------------------------------------------------ view ----
def _series_from_ledger(rows):
    """Daily closing-balance line from a ledger (last balance seen per date; if no
    balance column, running-sum of amounts). + fund add/withdraw markers."""
    if not rows:
        return [], []
    by_date = {}
    have_bal = any(r.get("balance") is not None for r in rows)
    running = 0.0
    for r in sorted(rows, key=lambda x: x["date"]):
        running += r.get("amount") or 0.0
        by_date[r["date"]] = r["balance"] if (have_bal and r.get("balance") is not None) else round(running, 2)
    series = [{"date": d, "balance": by_date[d]} for d in sorted(by_date)]
    funds = [{"date": r["date"], "amount": r["amount"],
              "type": "in" if r["amount"] >= 0 else "out",
              "note": r.get("particulars", "")}
             for r in rows if r.get("fund")]
    return series, funds


def _series_from_snapshots(rows):
    """Balance line from auto snapshots (total_margin per day) + big-jump markers
    inferred as fund events (only used when no CSV ledger uploaded)."""
    series, funds, prev = [], [], None
    for r in sorted(rows, key=lambda x: x.get("date", "")):
        val = r.get("total_margin")
        if val is None:
            val = r.get("cash")
        if val is None:
            continue
        series.append({"date": r["date"], "balance": round(float(val), 2)})
        if prev is not None:
            jump = val - prev
            if abs(jump) >= 10000:   # heuristic: a ≥₹10k day-over-day move ~ a fund transfer
                funds.append({"date": r["date"], "amount": round(jump, 2),
                              "type": "in" if jump >= 0 else "out", "note": "inferred (snapshot jump)"})
        prev = val
    return series, funds


def view():
    """Combined payload for the RMS ledger panel — per broker: balance-over-time
    series + fund markers + a table + current snapshot. Prefers the uploaded CSV
    ledger (real closing balance + real fund events); falls back to auto
    snapshots. One chart, two tabs (dhan / kite)."""
    hist = _read_hist()
    out = {"ok": True}
    for b in _BROKERS:
        led = _read_json(_LEDGER_PATH.format(b), None) or _read_json(_LEDGER_PATH.format(b) + ".bak", None) or []
        if not isinstance(led, list):
            led = []
        snaps = hist.get(b) or []
        has_balance = bool(led) and any(r.get("balance") is not None for r in led)
        if led:
            series, funds = _series_from_ledger(led)
            source = "ledger"
            table = [{"date": r["date"], "particulars": r.get("particulars", ""),
                      "debit": r.get("debit", 0), "credit": r.get("credit", 0),
                      "balance": r.get("balance"), "fund": r.get("fund", False)}
                     for r in sorted(led, key=lambda x: x["date"], reverse=True)]
        else:
            series, funds = _series_from_snapshots(snaps)
            source = "snapshot"
            table = [{"date": r["date"], "cash": r.get("cash"), "collateral": r.get("collateral"),
                      "available": r.get("available"), "balance": r.get("total_margin")}
                     for r in sorted(snaps, key=lambda x: x.get("date", ""), reverse=True)]
        out[b] = {
            "source": source,
            "series": series,
            "funds": funds,
            "table": table,
            "latest": (snaps[-1] if snaps else None),
            "has_ledger": bool(led),
            "has_balance": has_balance,   # real running-balance col (Zerodha) vs cumulative fund-flow (Dhan pay-in/out)
            "snapshot_days": len(snaps),
        }
    return out


# -------------------------------------------------------------------- cli ----
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--import-csv", dest="imp", nargs=2, metavar=("BROKER", "PATH"))
    ap.add_argument("--import-xlsx", dest="impx", nargs=2, metavar=("BROKER", "PATH"))
    ap.add_argument("--view", action="store_true")
    a = ap.parse_args()
    if a.snapshot:
        print(snapshot())
    elif a.imp:
        with open(a.imp[1], "r", encoding="utf-8", errors="replace") as f:
            print(import_ledger(a.imp[0], f.read()))
    elif a.impx:
        with open(a.impx[1], "rb") as f:
            print(import_ledger_xlsx(a.impx[0], f.read()))
    elif a.view:
        print(_json.dumps(view(), indent=2, default=str))
    else:
        ap.print_help()
