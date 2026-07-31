"""reconcile_csv.py — reconcile the app's LIVE ledger to an uploaded Zerodha tradebook CSV.

When the live-API reconcile hasn't run / isn't matching, the user exports Zerodha's
tradebook ("trades (NN).csv": Trade ID, Fill time, Type, Instrument, Product, Qty, Avg Price)
and uploads it here. We treat that CSV as the authoritative broker truth for the day and make
the app's per-contract net match it — recording only the DELTA that's missing (idempotent:
re-uploading the same CSV changes nothing once matched). Same authoritative-mirror philosophy
as reconcile_broker.py (ADR-011), but the source is the uploaded file instead of the Kite API.

Read-only planner by default: plan() computes the diff, apply(dry_run=False) writes.
LIVE only — PAPER is a separate simulated ledger, never touched here.
"""
import csv as _csv
import io
import os
import re
import sqlite3
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)
try:
    import _paths  # noqa
except Exception:
    pass

_MON3 = {'JAN': 'Jan', 'FEB': 'Feb', 'MAR': 'Mar', 'APR': 'Apr', 'MAY': 'May', 'JUN': 'Jun',
         'JUL': 'Jul', 'AUG': 'Aug', 'SEP': 'Sep', 'OCT': 'Oct', 'NOV': 'Nov', 'DEC': 'Dec'}
_WK = {'1': 'Jan', '2': 'Feb', '3': 'Mar', '4': 'Apr', '5': 'May', '6': 'Jun', '7': 'Jul',
       '8': 'Aug', '9': 'Sep', 'O': 'Oct', 'N': 'Nov', 'D': 'Dec'}
_MONTHLY = re.compile(r'^([A-Z&]+?)(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d+(?:\.\d+)?)(CE|PE)$')
_WEEKLY = re.compile(r'^([A-Z&]+?)(\d{2})([1-9OND])(\d{2})(\d+(?:\.\d+)?)(CE|PE)$')
_DEAD = {"rejected", "cancelled", "canceled", "failed", "expired", "blocked"}


def _fmt_strike(s):
    f = float(s)
    return str(int(f)) if f == int(f) else str(f)


def kite_to_trad_sym(instr):
    """Kite F&O tradingsymbol → the app's trad_sym (ROOT-MonYYYY-STRIKE-CE/PE). Handles
    monthly (NIFTY26AUG24100CE) + weekly (NIFTY2680424350CE = 2026 Aug 04) — the app uses
    month+year for both (no expiry day, like the rest of the codebase). (root, trad_sym) or
    (None, None) if it's not a recognised option symbol (e.g. an equity/future)."""
    s = (instr or '').strip().upper()
    m = _MONTHLY.match(s)
    if m:
        root, yy, mon, strike, opt = m.groups()
        return root, f"{root}-{_MON3[mon]}{2000 + int(yy)}-{_fmt_strike(strike)}-{opt}"
    m = _WEEKLY.match(s)
    if m:
        root, yy, mc, dd, strike, opt = m.groups()
        return root, f"{root}-{_WK[mc]}{2000 + int(yy)}-{_fmt_strike(strike)}-{opt}"
    return None, None


def parse_zerodha_tradebook(text):
    """Rows → [{trade_id,time,side,kite_sym,root,trad_sym,product,qty,price}]. Skips header /
    blank / non-option rows. `date` = the fill date if uniform (Zerodha exports one day)."""
    fills, unresolved, dates = [], [], set()
    for row in _csv.reader(io.StringIO(text)):
        if not row or len(row) < 7:
            continue
        tid = (row[0] or '').strip()
        if not tid.isdigit():
            continue                      # header / junk
        try:
            ftime = row[1].strip(); side = row[2].strip().upper()
            instr = row[3].strip(); prod = row[4].strip()
            qty = int(float(row[5])); px = float(row[6])
        except Exception:
            continue
        if side not in ('BUY', 'SELL'):
            continue
        root, ts = kite_to_trad_sym(instr)
        if ftime[:10]:
            dates.add(ftime[:10])
        if not ts:
            unresolved.append(instr)
            continue
        fills.append({'trade_id': tid, 'time': ftime, 'side': side, 'kite_sym': instr,
                      'root': root, 'trad_sym': ts, 'product': prod, 'qty': qty, 'price': px})
    date = sorted(dates)[-1] if dates else None
    return fills, sorted(set(unresolved)), date


def _app_net(date, broker_name='kite'):
    """App's LIVE per-contract net for the date (real fills only; excludes dead/blocked)."""
    import order_store
    net = defaultdict(int)
    try:
        with order_store._lock, order_store._conn() as c:
            c.row_factory = sqlite3.Row
            for r in c.execute("SELECT trad_sym,side,qty,status FROM orders "
                               "WHERE date=? AND mode='live' AND broker=?", (date, broker_name)):
                if str(r['status'] or '').lower() in _DEAD:
                    continue
                net[r['trad_sym']] += (1 if r['side'] == 'BUY' else -1) * int(r['qty'] or 0)
    except Exception:
        pass
    return net


def plan(text, broker_name='kite'):
    """READ-ONLY. Parse CSV → per-contract broker(CSV) net vs app net → what's out of sync."""
    fills, unresolved, date = parse_zerodha_tradebook(text)
    if not date:
        return {"ok": False, "msg": "CSV me koi valid trade row nahi mila (Zerodha tradebook export chahiye)."}
    csv_net = defaultdict(int)
    csv_px = defaultdict(lambda: {'BUY': [], 'SELL': []})   # trad_sym -> side -> [(qty,px)]
    for f in fills:
        csv_net[f['trad_sym']] += (1 if f['side'] == 'BUY' else -1) * f['qty']
        csv_px[f['trad_sym']][f['side']].append((f['qty'], f['price']))
    app_net = _app_net(date, broker_name)
    rows = []
    for ts in sorted(set(csv_net) | set(app_net)):
        bn, an = csv_net.get(ts, 0), app_net.get(ts, 0)
        rows.append({"trad_sym": ts, "broker_net": bn, "app_net": an, "delta": bn - an,
                     "match": bn == an})
    n_mis = sum(1 for r in rows if not r['match'])
    return {"ok": True, "date": date, "fills": len(fills), "unresolved": unresolved,
            "rows": rows, "mismatches": n_mis, "_csv_px": {k: dict(v) for k, v in csv_px.items()}}


def _avg_px(pairs):
    q = sum(p[0] for p in pairs)
    return round(sum(p[0] * p[1] for p in pairs) / q, 2) if q else 0.0


def apply(text, broker_name='kite', dry_run=True, log=print):
    """Make each contract's app net match the CSV (broker) net by recording the missing
    DELTA leg. Idempotent (matched contracts → delta 0 → skipped). Attributes the leg to the
    single open live strategy on that contract, else 'manual'. LIVE only. Records nothing on
    dry_run (default)."""
    import order_store
    from _ops import reconcile_broker as _rb
    p = plan(text, broker_name)
    if not p.get("ok"):
        return p
    date = p["date"]
    actions = []
    con = sqlite3.connect(str(order_store.DB_PATH))
    for r in p["rows"]:
        if r["match"]:
            continue
        ts, delta = r["trad_sym"], r["delta"]
        side = 'BUY' if delta > 0 else 'SELL'
        qty = abs(delta)
        px = _avg_px(p["_csv_px"].get(ts, {}).get(side, []))
        if not px:                                    # no CSV price for that side (partial data)
            actions.append({"type": "skip", "trad_sym": ts, "reason": "no CSV price for delta side"})
            continue
        try:
            strat = _rb._open_live_strategy(con, date, broker_name, ts) or "manual"
        except Exception:
            strat = "manual"
        sec_id = ""
        try:
            import dhan_master
            sec_id = dhan_master.get_sec_id_for_trad_sym(ts) or ""
        except Exception:
            sec_id = ""
        actions.append({"type": "record", "trad_sym": ts, "side": side, "qty": qty,
                        "price": px, "strategy": strat})
        if not dry_run:
            order_store.record(side, qty, px, source="csv_reconcile", strategy=strat,
                               mode="live", broker=broker_name, symbol=ts.split('-')[0],
                               instrument="options", trad_sym=ts, sec_id=str(sec_id),
                               segment="NSE_FNO", status="filled",
                               tags=["CSV_RECONCILE", "BROKER_MIRROR"])
    con.close()
    after = plan(text, broker_name) if not dry_run else p
    return {"ok": True, "date": date, "dry_run": dry_run, "actions": actions,
            "recorded": sum(1 for a in actions if a["type"] == "record"),
            "residual_mismatch": after["mismatches"], "unresolved": p["unresolved"]}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("csvfile")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    txt = open(a.csvfile, encoding="utf-8").read()
    if a.apply:
        import json
        print(json.dumps(apply(txt, dry_run=False), indent=2, default=str))
    else:
        p = plan(txt)
        print(f"=== CSV reconcile plan  date={p.get('date')}  fills={p.get('fills')}  mismatches={p.get('mismatches')} ===")
        for r in p.get("rows", []):
            flag = "" if r["match"] else f"   <-- DELTA {r['delta']:+d}"
            print(f"  {r['trad_sym']:30.30} broker={r['broker_net']:+6d}  app={r['app_net']:+6d}{flag}")
        if p.get("unresolved"):
            print("  unresolved symbols:", p["unresolved"])
