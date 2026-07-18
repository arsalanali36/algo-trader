#!/usr/bin/env python3
"""
bs_shadow.py — DAILY Black-Scholes shadow of the REAL paper/live trades.

For every completed trade the app actually did (order_store), reprice the SAME
option (same strike/type) at the SAME entry & exit minute using a Black-Scholes
model instead of the real market premium. Cache per-day so the Stats tab can show
"Real vs BS" divergence without recomputing.

    python _ops/bs_shadow.py                         # today
    python _ops/bs_shadow.py --date 2026-07-16
    python _ops/bs_shadow.py --backfill 2026-07-13 2026-07-17

Output:  data/bs_shadow/<date>.json
    { date, generated_at, totals:{real_net,bs_net,n},
      by_strategy:{sid:{real_net,bs_net,n}},
      trades:[ {strategy,sym,side,qty,entry_time,exit_time,
                real_gross,real_tax,real_net, bs_gross,bs_tax,bs_net, bs_ok} ] }

DISPLAY-ONLY. Places no orders, touches no live money-path (Rule 6D safe).
Real numbers reproduce the dashboard exactly (validated); only the BS column is modelled.
"""
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))); import _paths  # noqa: root+_core/_data on path
_s.path.insert(0, _o.path.join(_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))), "scratch", "nifty_trend"))

import json, socket, datetime, argparse
import requests
import pandas as pd

# IPv4 force — Dhan rejects IPv6 (DH-905)
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda h, p, f=0, t=0, pr=0, fl=0: _gai(h, p, socket.AF_INET, t, pr, fl)

import order_store
import bs_option as bs
try:
    import dhan_master
except Exception:
    dhan_master = None

ROOT = _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__)))
OUT_DIR = _o.path.join(ROOT, "data", "bs_shadow")
SPOT_DIR = _o.path.join(OUT_DIR, "_spot")
NT = _o.path.join(ROOT, "scratch", "nifty_trend")
os_makedirs = lambda d: _o.makedirs(d, exist_ok=True)
os_makedirs(OUT_DIR); os_makedirs(SPOT_DIR)

_cfg = json.load(open(_o.path.join(ROOT, "data", "config.json")))
HDRS = {"access-token": _cfg["jwt_token"], "client-id": _cfg["client_id"], "Content-Type": "application/json"}
IDX = {"NIFTY": "13", "BANKNIFTY": "25"}
SS, SE = datetime.time(9, 15), datetime.time(15, 29)


# ---------- spot (index 1-min) with per-day disk cache ----------
def _fetch_index(sec, date):
    body = {"securityId": sec, "exchangeSegment": "IDX_I", "instrument": "INDEX",
            "expiryCode": 0, "fromDate": date, "toDate": date}
    r = requests.post("https://api.dhan.co/v2/charts/intraday", headers=HDRS, json=body, timeout=40)
    r.raise_for_status()
    d = r.json()
    if not (isinstance(d, dict) and d.get("close")):
        return {}
    out = {}
    for t, c in zip(d["timestamp"], d["close"]):
        dtm = datetime.datetime.fromtimestamp(t)
        if dtm.weekday() < 5 and SS <= dtm.time() <= SE:
            out[dtm.strftime("%Y-%m-%d %H:%M")] = float(c)
    return out


def spot_series(underlying, date):
    """Return {'YYYY-MM-DD HH:MM': close} for the index, cached to disk."""
    sec = IDX.get(underlying)
    if not sec:
        return None
    fp = _o.path.join(SPOT_DIR, f"{underlying}_{date}.json")
    if _o.path.isfile(fp):
        try:
            return json.load(open(fp))
        except Exception:
            pass
    try:
        s = _fetch_index(sec, date)
    except Exception as e:
        print(f"  spot fetch fail {underlying} {date}: {e}", flush=True)
        return None
    if s:
        json.dump(s, open(fp, "w"))
    return s


def _spot_at(series, hhmm):
    """Nearest minute <= hhmm (series keys are 'date HH:MM')."""
    if not series:
        return None
    if hhmm in series:
        return series[hhmm]
    keys = sorted(k for k in series if k <= hhmm)
    return series[keys[-1]] if keys else None


# ---------- sigma (realised-vol map, same as run_hunt) ----------
_SIGMA = None
def sigma_for(date_obj):
    global _SIGMA
    if _SIGMA is None:
        try:
            h = pd.read_csv(_o.path.join(NT, "nifty_1min.csv"))
            tc = [c for c in h.columns if c.lower() in ("datetime", "date", "timestamp")][0]
            cc = [c for c in h.columns if c.lower() == "close"][0]
            h["_t"] = pd.to_datetime(h[tc])
            daily = h.set_index("_t")[cc].resample("1D").last().dropna()
            daily.index = daily.index.date
            _SIGMA = bs.realised_vol_map(daily)
        except Exception as e:
            print("  sigma_map build fail:", e, flush=True)
            _SIGMA = {}
    if date_obj in _SIGMA:
        return _SIGMA[date_obj]
    prior = [d for d in _SIGMA if d <= date_obj]          # vol persists → nearest prior day, not a flat 0.15
    return _SIGMA[max(prior)] if prior else 0.15


# ---------- expiry (sec_id first, never parse trad_sym; fallback = weekly) ----------
def expiry_dt(sec_id, ts):
    if dhan_master:
        for fn in ("get_expiry_for_sec_id", "get_expiry_date_for_sec_id"):
            f = getattr(dhan_master, fn, None)
            if f:
                try:
                    e = f(int(sec_id))
                    if e:
                        return pd.Timestamp(str(e)).replace(hour=15, minute=30)
                except Exception:
                    pass
    ts = pd.Timestamp(ts); d = ts.date()
    for a in range(8):                       # 2026 NIFTY weekly = Tuesday (Thu->Tue 2025-09)
        c = d + datetime.timedelta(days=a)
        if c.weekday() == 1:
            return pd.Timestamp(c).replace(hour=15, minute=30)
    return ts + pd.Timedelta(days=3)


def _T(ts, exp):
    return max((pd.Timestamp(exp) - pd.Timestamp(ts)).total_seconds(), 60) / (365.25 * 86400)


# ---------- reprice one day ----------
def build_day(date):
    res = order_store.trades_for(date)
    details = res["details"] if isinstance(res, dict) else res
    spot_cache = {}
    trades, by_strat = [], {}
    tot = {"real_gross": 0.0, "real_net": 0.0, "bs_gross": 0.0, "bs_net": 0.0, "n": 0}
    for d in details:
        sym = d.get("sym") or ""
        if not d.get("exit_price") or not sym or "-" not in sym:
            continue
        parts = sym.split("-")
        if len(parts) < 4 or parts[-1] not in ("CE", "PE"):
            continue
        und = parts[0]; K = float(parts[-2]); opt = parts[-1]
        side = d.get("entry", "BUY"); sgn = 1.0 if side == "BUY" else -1.0
        qty = int(d.get("qty") or 0)
        ep = float(d.get("entry_price") or 0); xp = float(d.get("exit_price") or 0)
        strat = d.get("strategy") or "?"
        edate = d.get("entry_date") or date; xdate = d.get("exit_date") or date
        etime = d.get("entry_time") or "09:15"; xtime = d.get("exit_time") or "15:15"
        edt = pd.Timestamp(f"{edate} {etime}"); xdt = pd.Timestamp(f"{xdate} {xtime}")

        real_gross = sgn * (xp - ep) * qty
        real_tax = bs.calc_charges(ep, xp, qty, entry_side=side, when=edt)
        real_net = real_gross - real_tax

        bs_ok = False; bs_gross = bs_tax = bs_net = None
        if und in IDX:
            if (und, edate) not in spot_cache:
                spot_cache[(und, edate)] = spot_series(und, edate)
            if (und, xdate) not in spot_cache:
                spot_cache[(und, xdate)] = spot_series(und, xdate)
            Sin = _spot_at(spot_cache[(und, edate)], f"{edate} {etime}")
            Sout = _spot_at(spot_cache[(und, xdate)], f"{xdate} {xtime}")
            if Sin and Sout:
                exp = expiry_dt(d.get("sec_id"), edt)
                si = sigma_for(edt.date()); so = sigma_for(xdt.date())
                be = bs.bs_price(Sin, K, _T(edt, exp), si, opt=opt)
                bx = bs.bs_price(Sout, K, _T(xdt, exp), so, opt=opt)
                bs_gross = sgn * (bx - be) * qty
                bs_tax = bs.calc_charges(be, bx, qty, entry_side=side, when=edt)
                bs_net = bs_gross - bs_tax
                bs_ok = True

        trades.append({"strategy": strat, "sym": sym, "side": side, "qty": qty,
                       "entry_time": etime, "exit_time": xtime,
                       "real_gross": round(real_gross, 1), "real_tax": round(real_tax, 1),
                       "real_net": round(real_net, 1),
                       "bs_gross": None if bs_gross is None else round(bs_gross, 1),
                       "bs_tax": None if bs_tax is None else round(bs_tax, 1),
                       "bs_net": None if bs_net is None else round(bs_net, 1),
                       "bs_ok": bs_ok})
        s = by_strat.setdefault(strat, {"real_gross": 0.0, "real_net": 0.0,
                                        "bs_gross": 0.0, "bs_net": 0.0, "n": 0})
        s["real_gross"] += real_gross; s["real_net"] += real_net; s["n"] += 1
        tot["real_gross"] += real_gross; tot["real_net"] += real_net; tot["n"] += 1
        if bs_ok:
            s["bs_gross"] += bs_gross; s["bs_net"] += bs_net
            tot["bs_gross"] += bs_gross; tot["bs_net"] += bs_net

    for v in list(by_strat.values()) + [tot]:
        for k in ("real_gross", "real_net", "bs_gross", "bs_net"):
            v[k] = round(v[k], 1)
    payload = {"date": date, "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
               "totals": tot, "by_strategy": by_strat, "trades": trades}
    json.dump(payload, open(_o.path.join(OUT_DIR, f"{date}.json"), "w"), indent=1)
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--backfill", nargs=2, metavar=("FROM", "TO"))
    a = ap.parse_args()
    if a.backfill:
        d0 = datetime.date.fromisoformat(a.backfill[0]); d1 = datetime.date.fromisoformat(a.backfill[1])
        dates = [(d0 + datetime.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    else:
        dates = [a.date or datetime.date.today().isoformat()]
    for dt in dates:
        p = build_day(dt)
        t = p["totals"]
        print(f"{dt}  legs {t['n']:>3}  Real NET {t['real_net']:>10,.0f}  BS NET {t['bs_net']:>10,.0f}  "
              f"Δ {t['bs_net']-t['real_net']:>+10,.0f}", flush=True)


if __name__ == "__main__":
    main()
