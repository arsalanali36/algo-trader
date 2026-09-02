"""
level_slots_live.py — watch loop + entry firing for _ops/level_slots.py.

Mirrors strangle_live.py's shape (self-contained _ops module, own loop thread in
monitor_daemon, PAPER hard-lock). level_slots.py is the pure state machine; this
file is the only place that touches candles / spot / broker / exit rules.

NSE (NIFTY / BANKNIFTY / F&O stocks):
  candles  Dhan intraday 1-min via brokers.DhanBroker.intraday_candles (rate-limited,
           Rule 6B) → bucketed to the slot's TF here (same IST-safe modulo as
           trader_dashboard._last_closed_candle_close)
  spot     shared_ltp_cache (ltp_poller-warmed, zero extra Dhan) → broker.quote fallback
  entry    HEDGE-FIRST: BUY wing (strategy_safety.wing_by_delta ≈ hedge_delta, IV
           back-solved from the sold leg's live premium) → SELL ATM; whole structure
           gated ONCE (risk_gate.gating_status + affordable_lots), per-leg gate=False,
           unwind on any fail (never naked). Orders ONLY via execution_gateway.
  exit     Trade Manager rule armed on the group (position_exit_rules.set_rule with
           frozen entry_spot / dir / ₹ / index-pt / index-level / confirm) → the
           central monitor (_run_position_exit_rules) does the ORDERED square-off.
           EOD 15:15 squareoff = pos_monitor default (intraday strategy).

BTC (Delta Exchange) — PAPER ONLY, INDEX-level slots only (premium candles for a
  single Delta option are not served by delta_feed):
  candles  delta_feed.candles (perpetual)   spot delta_feed.spot
  entry    delta_feed.chain marks + REAL chain deltas for the wing; fills mirrored
           into order_store via delta_ironfly_trader._record_leg (INR, broker=delta)
  exit     own check here (₹ combined MTM from marks + spot vs index levels, wick
           mode) + cash-settle at the daily 12:00 UTC expiry — the NSE Trade Manager
           monitor cannot price Delta legs (ltp_poller is NSE-only).

Rule 10: discretionary tool (user picks the level) — NOT backtested, paper-only.
"""
import os, sys, time, json, threading
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (HERE, ROOT, os.path.join(ROOT, "_core"), os.path.join(ROOT, "_data"),
          os.path.join(ROOT, "_CHARTING"), os.path.join(ROOT, "strategies", "live")):
    if p not in sys.path:
        sys.path.insert(0, p)

import level_slots as ls
import execution_gateway as gw
import risk_gate as rg
import dhan_master
import ltp_poller
import shared_ltp_cache as ltc
import position_exit_rules as per
import strategy_safety as ss
import leg_collision as lc
try:
    import market_calendar as mc
except Exception:
    mc = None
try:
    import notify
except Exception:
    notify = None
try:
    import universe
except Exception:
    universe = None
try:
    import delta_feed
except Exception:
    delta_feed = None
try:
    import telegram_notify as _tgn
except Exception:
    _tgn = None
try:
    import order_store
except Exception:
    order_store = None


def _tg(text):
    """Telegram (send_raw = mode-filter bypass; ye paper tool hai par user ko alert chahiye)."""
    if _tgn is None:
        return
    try:
        _tgn.send_raw(text)
    except Exception:
        pass

SID = "level_slot"                 # strategy id (registry 03.02) — order_store.strategy
MODE = "paper"                     # HARD LOCK — live = explicit code change + user go
SEG = "NSE_FNO"
_IDX = {"NIFTY": ("13", "IDX_I", "INDEX"), "BANKNIFTY": ("25", "IDX_I", "INDEX")}
_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "15m": 15}
_LOOP_SLEEP = 5.0
_BARS_TTL = 20.0
_bars_cache = {}                   # key -> (fetched_at, bars)
_fired_session = set()             # belt-and-suspenders double-fire guard (claim() is primary)


def _log(msg):
    print(f"[level-slots] {msg}", flush=True)


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _hm():
    return _ist_now().strftime("%H:%M")


# ─────────────────────────── data sources ───────────────────────────
def underlying_info(sym):
    """(sec_id, seg, instrument) for candles/spot of an NSE underlying, or None."""
    sym = str(sym).upper()
    if sym in _IDX:
        return _IDX[sym]
    try:
        info = dhan_master.get_equity_info(sym)
        if info:
            return (str(info[0]), info[1], info[2])
    except Exception:
        pass
    if universe is not None:
        try:
            sec = universe.equity_secid(sym)
            if sec:
                return (str(sec), "NSE_EQ", "EQUITY")
        except Exception:
            pass
    return None


def is_fno_underlying(sym):
    """True if the scrip master lists options for this symbol (index or OPTSTK)."""
    try:
        if not dhan_master._options_cache:
            dhan_master.build_cache()
        return str(sym).upper() in dhan_master._options_cache
    except Exception:
        return False


def fno_symbols():
    try:
        if not dhan_master._options_cache:
            dhan_master.build_cache()
        return sorted(dhan_master._options_cache.keys())
    except Exception:
        return []


def candle_source(s):
    """('delta', None, None, None) | ('dhan', sec_id, seg, instrument) | None"""
    sym = str(s.get("sym", "")).upper()
    if sym == "BTC":
        return ("delta", None, None, None)
    if s.get("kind") == "prem":
        c = s.get("contract") or {}
        if not c.get("sec_id"):
            return None
        return ("dhan", str(c["sec_id"]), SEG, "OPTIDX" if sym in _IDX else "OPTSTK")
    u = underlying_info(sym)
    return ("dhan", u[0], u[1], u[2]) if u else None


def _bucket_1m(rows, tf_min, now_ist_epoch):
    """rows = [(t_ist_epoch, o, h, l, c)] 1-min → CLOSED tf_min bars oldest→newest.
    IST offset (330 min) is divisible by 1/3/5/15 → plain epoch modulo lands on real
    IST candle boundaries (same reasoning as _last_closed_candle_close)."""
    span = int(tf_min) * 60
    out = {}
    for t, o, h, l, c in rows:
        b = int(t) - int(t) % span
        if b + span > now_ist_epoch:
            continue                                   # still forming → skip
        cur = out.get(b)
        if cur is None:
            out[b] = {"time": b, "open": float(o), "high": float(h), "low": float(l), "close": float(c)}
        else:
            cur["high"] = max(cur["high"], float(h))
            cur["low"] = min(cur["low"], float(l))
            cur["close"] = float(c)
    return [out[k] for k in sorted(out)]


def _in_nse_session(t_ist_epoch):
    """09:15 ≤ IST wall-clock < 15:30 — drops Dhan's synthetic post-close index bar
    (verified 2026-09-02: IDX_I 1-min series ends 15:29 then one 18:41 bar = closing value)."""
    hm = (int(t_ist_epoch) % 86400) // 60
    return 555 <= hm < 930


# ─────────────────────────── on-disk 1-min lake (past days, zero Dhan) ───────────────────────────
LAKE = os.path.join(ROOT, "_TRADING_DATA")


def _lake_file(sym, date_str):
    """Per-day 1-min CSV for an underlying: Index/NIFTY/NIFTY_<d>.csv (daily_extend) or
    Equity/<SYM>/<SYM>_<d>.csv (algo-equity-daily collector, 210 F&O stocks). None if absent
    (BANKNIFTY has no per-day store yet → Dhan fallback)."""
    sym = str(sym).upper()
    for sub in ("Index", "Equity"):
        f = os.path.join(LAKE, sub, sym, f"{sym}_{date_str}.csv")
        if os.path.exists(f):
            return f
    return None


def _lake_rows(path):
    """CSV 'Datetime,Open,High,Low,Close[,Volume]' (IST wall-clock) → [(t_ist_epoch,o,h,l,c)]."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            next(fh)
            for line in fh:
                parts = line.strip().split(",")
                if len(parts) < 5:
                    continue
                d = datetime.strptime(parts[0][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                out.append((int(d.timestamp()), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])))
    except Exception as e:
        _log(f"lake read fail {path}: {e}")
        return []
    return out


def lake_history(sym, days):
    """1-min rows for the PAST `days` calendar days (today excluded — today comes live from
    Dhan) + how many weekday files were missing. Purana data disk se, Dhan poll nahi."""
    rows, missing = [], 0
    today = _ist_now().date()
    for k in range(1, int(days) + 1):
        d = today - timedelta(days=k)
        if d.weekday() >= 5:
            continue
        f = _lake_file(sym, d.isoformat())
        if f:
            rows.extend(_lake_rows(f))
        else:
            try:
                if mc is not None and not mc.is_trading_day(d):
                    continue                            # holiday, not a gap
            except Exception:
                pass
            missing += 1
    return rows, missing


def fetch_bars(s, tf=None, force=False, days=1):
    """Closed candles for the slot's source at its TF (oldest→newest, IST-shifted
    epoch 'time' like /api/trade-chart-data). [] on any failure (freeze, never guess)."""
    src = candle_source(s)
    if not src:
        return []
    tf = tf or s.get("tf") or "5m"
    tf_min = _TF_MIN.get(tf, 5)
    days = max(1, min(int(days or 1), 60))
    key = (src[0], src[1], tf_min, days)
    now = time.time()
    hit = _bars_cache.get(key)
    if hit and not force and (now - hit[0]) < _BARS_TTL:
        return hit[1]
    bars = []
    try:
        if src[0] == "delta":
            if delta_feed is None:
                return []
            raw = delta_feed.candles("BTC", tf, min(2000, max(200, days * (1440 // tf_min)))) or []
            span = tf_min * 60
            for r in raw:
                if int(r["time"]) + span > now:
                    continue                           # forming
                bars.append({"time": int(r["time"]) + 19800, "open": r["open"], "high": r["high"],
                             "low": r["low"], "close": r["close"]})
        else:
            from brokers import get_broker
            br = get_broker("dhan")
            rows = []
            dhan_days = days
            if days > 1 and s.get("kind") != "prem":
                # past days from the on-disk lake; Dhan only for what the lake lacks
                lrows, missing = lake_history(s.get("sym"), days)
                if lrows:
                    rows.extend(lrows)
                    if missing == 0:
                        dhan_days = 1                   # sirf aaj live se
            df = br.intraday_candles(src[1], src[2], src[3], days=dhan_days, interval=1)
            if (df is None or df.empty) and not rows:
                return []
            seen = {r[0] for r in rows}
            if dhan_days > 1 and rows:
                rows = []                               # lake had gaps → Dhan range is the truth
                seen = set()
            for t, o, h, l, c in (zip(df["time"], df["open"], df["high"], df["low"], df["close"]) if df is not None and not df.empty else []):
                # df.time = naive IST Timestamp → treat as UTC to get IST-shifted epoch
                te = int(t.replace(tzinfo=timezone.utc).timestamp())
                if not _in_nse_session(te) or te in seen:
                    continue           # Dhan appends a synthetic post-close bar (18:41, O=H=L=C)
                rows.append((te, o, h, l, c))
            rows.sort(key=lambda r: r[0])
            bars = _bucket_1m(rows, tf_min, int(now + 19800))
    except Exception as e:
        _log(f"bars fetch fail {s.get('id')}: {e}")
        return hit[1] if hit else []
    _bars_cache[key] = (now, bars)
    return bars


def spot_now(sym, wide=False):
    """Underlying spot: BTC → delta; index → poller cache (+REST); stock → cache/REST."""
    sym = str(sym).upper()
    if sym == "BTC":
        try:
            return float(delta_feed.spot("BTC")) if delta_feed else None
        except Exception:
            return None
    u = underlying_info(sym)
    if not u:
        return None
    try:
        v = ltc.get(u[0], max_age=(86400 if wide else 60.0))
        if v:
            return float(v)
    except Exception:
        pass
    try:
        from brokers import get_broker
        q = get_broker("dhan").quote(u[0], u[1]) or {}
        return float(q.get("ltp") or 0) or None
    except Exception:
        return None


def spot_cached(sym):
    """List-view price: cache-only (never a Dhan call) — None if nothing cached."""
    sym = str(sym).upper()
    if sym == "BTC":
        try:
            return float(delta_feed.spot("BTC")) if delta_feed else None
        except Exception:
            return None
    u = underlying_info(sym)
    if not u:
        return None
    try:
        v = ltc.get_stale(u[0], max_age=86400)
        return float(v) if v else None
    except Exception:
        return None


def price_now(s, wide=False):
    """The price the slot's LEVEL is measured in: premium for prem slots, spot otherwise."""
    if s.get("kind") == "prem" and (s.get("contract") or {}).get("sec_id"):
        sec = str(s["contract"]["sec_id"])
        try:
            v = ltc.get(sec, max_age=(86400 if wide else 60.0))
            if v:
                return float(v)
        except Exception:
            pass
        try:
            ltp_poller.request_watch([(sec, SEG)])
            from brokers import get_broker
            q = get_broker("dhan").quote(sec, SEG) or {}
            return float(q.get("ltp") or 0) or None
        except Exception:
            return None
    return spot_now(s.get("sym"), wide=wide)


def _leg_ltp(sec, wait=3.0):
    """Option premium for a leg: poller-warmed cache (short wait) → broker.quote."""
    try:
        ltp_poller.request_watch([(str(sec), SEG)])
    except Exception:
        pass
    t0 = time.time()
    while time.time() - t0 < wait:
        try:
            v = ltc.get(str(sec), max_age=30)
            if v:
                return float(v)
        except Exception:
            pass
        time.sleep(0.5)
    try:
        from brokers import get_broker
        q = get_broker("dhan").quote(str(sec), SEG) or {}
        return float(q.get("ltp") or 0) or None
    except Exception:
        return None


def contracts_near_atm(sym, opt, n=6):
    """Strike picker for PREMIUM slots: ATM±n contracts of the nearest expiry."""
    sym = str(sym).upper()
    spot = spot_now(sym, wide=True)
    if not spot:
        return {"ok": False, "msg": f"{sym} spot nahi mila (market band / poller idle)", "rows": []}
    rows = []
    for k in range(-n, n + 1):
        try:
            sec, tsym, lot, strike, exp = dhan_master.get_option_contract_ex(sym, spot, opt, k)
        except Exception:
            sec = None
        if sec and strike is not None:
            rows.append({"offset": k, "strike": strike, "sec_id": str(sec), "trad_sym": tsym,
                         "lot": int(lot or 0), "expiry": str(exp or "")[:10]})
    rows.sort(key=lambda r: r["strike"])
    return {"ok": True, "spot": spot, "rows": rows}


def _resolve_nse_structure(s, spot, occ, log=_log):
    """Resolve the credit-spread legs for a slot at `spot`: SELL (ATM / strike-near-level) +
    BUY wing ≈ hedge_delta (IV back-solved from the sold leg's live premium). Shared by the
    fire path AND the preview (Rule 6B — one resolver, never two). Returns (dict, None) or
    (None, err). dict: s_sec s_tsym s_off s_strike s_exp s_prem lot_sz atm_iv T bs w_sec w_tsym
    w_lot w_strike w_prem."""
    sym = str(s["sym"]).upper()
    opt_type, _dir = ls.option_side(s)
    hedge_delta = float(s.get("hedge_delta") or 0.25)
    ref_px = float(s["level"]) if str(s.get("sell_leg")) == "level" and s.get("level") else spot
    s_sec, s_tsym, lot_sz, s_off = lc.clear_leg(sym, ref_px, opt_type, 0, occ,
                                                dhan_master.get_option_contract, log=log)
    if not s_sec:
        return None, f"{opt_type} sell-leg resolve fail"
    occ.add(str(s_sec))
    _x, _xt, _xl, s_strike, s_exp = dhan_master.get_option_contract_ex(sym, ref_px, opt_type, s_off)
    lot_sz = int(lot_sz or _xl or 0)
    if not lot_sz:
        return None, f"lot size resolve nahi hua ({s_tsym}) — order NAHI bheja"
    s_prem = _leg_ltp(s_sec)
    if not s_prem:
        return None, f"{s_tsym} premium nahi mila — entry SKIP (₹0 fill kabhi nahi, TRAP #1)"
    atm_iv, T, bs = None, None, None
    try:
        import payoff
        bs = payoff._bs()
        exp_date = datetime.strptime(s_exp, "%Y-%m-%d %H:%M:%S").date() if s_exp else None
        T = payoff.tte_years(exp_date) if (bs and exp_date) else None
        if bs and T and T > 0 and s_strike:
            atm_iv = bs.implied_vol(s_prem, spot, s_strike, T, opt=opt_type)
    except Exception as e:
        log(f"IV solve fail ({e}) — wing falls back to min_strikes floor")
    try:
        w_sec, w_tsym, w_lot, w_strike = ss.wing_by_delta(
            sym, spot, opt_type, s_off, hedge_delta, atm_iv, s_exp,
            min_strikes=1, max_search=20, log=log, avoid=occ)
    except Exception as e:
        w_sec = None
        log(f"wing resolve err: {e}")
    if not w_sec:
        return None, f"{opt_type} hedge wing resolve fail — no naked, abort"
    occ.add(str(w_sec))
    w_prem = _leg_ltp(w_sec)
    if not w_prem:
        return None, f"{w_tsym} wing premium nahi mila — abort (no naked)"
    return {"opt_type": opt_type, "s_sec": str(s_sec), "s_tsym": s_tsym, "s_off": s_off,
            "s_strike": s_strike, "s_exp": s_exp, "s_prem": s_prem, "lot_sz": lot_sz,
            "atm_iv": atm_iv, "T": T, "bs": bs, "w_sec": str(w_sec), "w_tsym": w_tsym,
            "w_lot": w_lot, "w_strike": w_strike, "w_prem": w_prem}, None


def _leg_deltas(R, spot):
    """|Δ| of sold leg and wing from the SAME back-solved IV (no extra broker call); None if BS n/a."""
    try:
        bs, T, iv = R.get("bs"), R.get("T"), R.get("atm_iv")
        if not (bs and T and T > 0 and iv and iv > 0):
            return None, None
        ds = abs(bs.bs_delta(spot, R["s_strike"], T, iv, opt=R["opt_type"]))
        dw = abs(bs.bs_delta(spot, R["w_strike"], T, iv, opt=R["opt_type"]))
        return round(ds, 3), round(dw, 3)
    except Exception:
        return None, None


def preview_structure(s):
    """What the slot WOULD do right now (no order): legs (strike/LTP/Δ), net |Δ|/unit,
    ₹ per index point, net credit, REAL hedged margin (risk_gate.position_margin = the same
    number RMS charges) vs naked, and a ₹ projection for every enabled exit line."""
    sym = str(s.get("sym", "")).upper()
    lots = int(s.get("lots") or 1)
    ex = s.get("exit") or {}
    en = ex.get("enabled") or {}
    opt_type, dir_ = ls.option_side(s)
    out = {"ok": False, "sym": sym, "opt_type": opt_type, "dir": dir_}
    if sym == "BTC":
        ch = _btc_chain()
        spot = float((ch or {}).get("spot") or 0)
        if not ch or not spot:
            out["msg"] = "Delta chain/spot nahi"
            return out
        k = "ce" if opt_type == "CE" else "pe"
        rows = [r for r in ch["rows"] if r.get(k) and (r[k].get("ltp") or 0) > 0]
        if not rows:
            out["msg"] = "priced strikes nahi"; return out
        atm = min(rows, key=lambda r: abs(r["strike"] - spot))
        hd = float(s.get("hedge_delta") or 0.25)
        otm = [r for r in rows if (r["strike"] > atm["strike"] if opt_type == "CE" else r["strike"] < atm["strike"]) and r[k].get("delta") is not None]
        wing = min(otm, key=lambda r: abs(abs(float(r[k]["delta"])) - hd)) if otm else None
        if not wing:
            out["msg"] = "wing resolve fail"; return out
        try:
            import delta_ironfly_trader as dft
            cv, usd_inr = dft.CONTRACT_VALUE.get("BTC", 0.001), dft._usd_inr()
        except Exception:
            cv, usd_inr = 0.001, 85.0
        ds, dw = abs(float(atm[k]["delta"] or 0)), abs(float(wing[k]["delta"] or 0))
        credit = float(atm[k]["ltp"]) - float(wing[k]["ltp"])
        width = abs(atm["strike"] - wing["strike"])
        unit_inr = cv * usd_inr * lots                       # ₹ per $1 of option price
        out.update({"ok": True, "spot": spot, "cur": "$", "lot_size": None, "qty": lots,
                    "legs": [{"side": "SELL", "sym": atm[k]["symbol"], "strike": atm["strike"], "ltp": atm[k]["ltp"], "delta": round(ds, 3)},
                             {"side": "BUY", "sym": wing[k]["symbol"], "strike": wing["strike"], "ltp": wing[k]["ltp"], "delta": round(dw, 3)}],
                    "net_delta": round(ds - dw, 3), "rs_per_pt": round((ds - dw) * unit_inr, 2),
                    "credit_unit": round(credit, 2), "credit_rs": round(credit * unit_inr, 0),
                    "margin_hedged": round(max(0.0, width - credit) * unit_inr, 0), "margin_naked": None,
                    "max_loss_rs": round(max(0.0, width - credit) * unit_inr, 0)})
    else:
        spot = spot_now(sym)
        if not spot:
            out["msg"] = f"{sym} spot nahi mila (market band / poller idle)"; return out
        R, err = _resolve_nse_structure(s, spot, set(), log=lambda m: None)
        if not R:
            out["msg"] = err; return out
        ds, dw = _leg_deltas(R, spot)
        q = lots * R["lot_sz"]
        credit = R["s_prem"] - R["w_prem"]
        legs_m = [{"sec_id": R["s_sec"], "entry": "SELL", "qty": q, "sym": R["s_tsym"], "entry_price": R["s_prem"], "segment": SEG},
                  {"sec_id": R["w_sec"], "entry": "BUY", "qty": q, "sym": R["w_tsym"], "entry_price": R["w_prem"], "segment": SEG}]
        mh = mn = None
        try:
            mb = rg.margin_breakdown(legs_m)
            mh, mn = mb.get("hedged"), mb.get("standalone")
        except Exception as e:
            _log(f"margin preview fail: {e}")
        nd = (ds - dw) if (ds is not None and dw is not None) else None
        out.update({"ok": True, "spot": spot, "cur": "₹", "lot_size": R["lot_sz"], "qty": q, "atm_iv": round(R["atm_iv"], 4) if R.get("atm_iv") else None,
                    "legs": [{"side": "SELL", "sym": R["s_tsym"], "strike": R["s_strike"], "ltp": R["s_prem"], "delta": ds},
                             {"side": "BUY", "sym": R["w_tsym"], "strike": R["w_strike"], "ltp": R["w_prem"], "delta": dw}],
                    "net_delta": round(nd, 3) if nd is not None else None,
                    "rs_per_pt": round(nd * q, 2) if nd is not None else None,
                    "credit_unit": round(credit, 2), "credit_rs": round(credit * q, 0),
                    "margin_hedged": mh, "margin_naked": mn,
                    "max_loss_rs": round(max(0.0, abs(R["s_strike"] - R["w_strike"]) - credit) * q, 0)})
    # ── ₹ projection per enabled exit line (index space → ₹ via rs_per_pt; ₹ lines as typed)
    proj = []
    rpp = out.get("rs_per_pt")
    anchor = float(s.get("level") or out.get("spot") or 0)
    if en.get("rs"):
        if ex.get("rs_sl"): proj.append({"src": "rs", "side": "sl", "rs": -abs(float(ex["rs_sl"]))})
        if ex.get("rs_tg"): proj.append({"src": "rs", "side": "target", "rs": abs(float(ex["rs_tg"]))})
    if en.get("ip") and rpp:
        if ex.get("ip_sl"): proj.append({"src": "ip", "side": "sl", "pts": float(ex["ip_sl"]), "rs": -abs(float(ex["ip_sl"]) * rpp), "level": anchor - dir_ * float(ex["ip_sl"])})
        if ex.get("ip_tg"): proj.append({"src": "ip", "side": "target", "pts": float(ex["ip_tg"]), "rs": abs(float(ex["ip_tg"]) * rpp), "level": anchor + dir_ * float(ex["ip_tg"])})
    if en.get("il") and rpp and anchor:
        for side, key in (("sl", "il_sl"), ("target", "il_tg")):
            if ex.get(key):
                lv = float(ex[key]); pts = (lv - anchor) * dir_
                proj.append({"src": "il", "side": side, "pts": round(pts, 1), "rs": round(pts * rpp, 0), "level": lv})
    out["projection"] = proj
    out["note"] = "Δ-linear estimate (gamma/theta/IV change nahi gina) · anchor = key level (entry ke baad asli entry spot)"
    return out


# ─────────────────────────── NSE fire ───────────────────────────
def _nse_fire(s):
    """Hedge-first credit spread on the slot's underlying. Returns (ok, msg, entry)."""
    sym = str(s["sym"]).upper()
    opt_type, dir_ = ls.option_side(s)
    lots = int(s.get("lots") or 1)
    hedge_delta = float(s.get("hedge_delta") or 0.25)
    mode = MODE
    log = _log

    if mode != "live":
        occ = set()
    else:                                                   # never share a contract (ADR-018)
        occ = lc.occupied_sec_ids(SID)

    spot = spot_now(sym)
    if not spot or spot <= 0:
        return False, f"{sym} spot abhi nahi mila — order NAHI bheja", None
    ref_px = float(s["level"]) if str(s.get("sell_leg")) == "level" else spot

    R, err = _resolve_nse_structure(s, spot, occ, log=log)
    if not R:
        return False, err, None
    s_sec, s_tsym, lot_sz, s_strike = R["s_sec"], R["s_tsym"], R["lot_sz"], R["s_strike"]
    s_prem, w_sec, w_tsym, w_strike, w_prem = R["s_prem"], R["w_sec"], R["w_tsym"], R["w_strike"], R["w_prem"]

    # ── single whole-structure gate (RMS + real basket margin + smart size-down) ──
    try:
        blocked, why, _h = rg.gating_status(SID, mode=mode)
        if blocked:
            return False, f"RMS blocked — {why}", None
    except Exception:
        pass

    def _basket_at(n):
        q = int(n) * lot_sz
        return [{"sec_id": str(s_sec), "entry": "SELL", "qty": q, "sym": s_tsym,
                 "entry_price": s_prem, "segment": SEG},
                {"sec_id": str(w_sec), "entry": "BUY", "qty": q, "sym": w_tsym,
                 "entry_price": w_prem, "segment": SEG}]
    try:
        sz, need, why = rg.affordable_lots(SID, lots, _basket_at, mode=mode)
    except Exception as e:
        sz, need, why = lots, 0, f"affordable_lots err {e} (fallback=config lots)"
    if sz < 1:
        return False, f"basket margin fit nahi even for 1 lot — {why}", None
    if sz < lots:
        log(f"smart-size {lots}→{sz} lots — basket ₹{need:,.0f} fits ({why})")
    lots = int(sz)
    q = lots * lot_sz
    gid = f"LVL_{sym}_{s['slot']}_{int(time.time())}"
    tag = "LVLSLOT"

    # ── place: BUY wing FIRST (margin drops), then SELL; unwind on fail ──
    placed = []

    def _unwind(reason):
        for p in reversed(placed):
            try:
                gw.execute_exit(SID, sym, p["sec_id"], p["trad_sym"], q, entry_side=p["side"],
                                seg=SEG, mode=mode, group_id=gid, reason=reason, tag=tag,
                                source="strategy", instrument="options", log=log)
            except Exception as ue:
                log(f"unwind FAIL {p['trad_sym']}: {ue}")

    for sec, tsym, side, xt in ((w_sec, w_tsym, "BUY", [tag, "HEDGE"]), (s_sec, s_tsym, "SELL", [tag])):
        try:
            r = gw.execute_signal(SID, sym, side, lots, lot_sz, str(sec), tsym, seg=SEG, mode=mode,
                                  source="strategy", tag=tag, group_id=gid, gate=False,
                                  extra_tags=xt, instrument="options", log=log)
        except Exception as e:
            r = {"ok": False, "reason": str(e)}
        if not r.get("ok"):
            _unwind("LVLSLOT_ABORT")
            return False, f"{side} {tsym} fail ({r.get('reason') or r.get('status')}) — abort (no naked)", None
        placed.append({"sec_id": str(sec), "trad_sym": tsym, "side": side,
                       "price": r.get("price"), "strike": (w_strike if side == "BUY" else s_strike)})

    # ── arm Trade Manager exit rule on the group (frozen entry_spot, OR-fire, confirm) ──
    ex = s.get("exit") or {}
    en = ex.get("enabled") or {}
    target_rs = float(ex.get("rs_tg") or 0) if en.get("rs") else 0.0
    sl_rs = -abs(float(ex.get("rs_sl") or 0)) if en.get("rs") else 0.0
    extra = {"entry_spot": spot, "dir": dir_, "tf": s.get("tf") or "5m",
             "confirm_mode": ex.get("confirm_mode") or "close",
             "confirm_min": ex.get("confirm_min") or 2,
             "enabled": {"rs": bool(en.get("rs")), "pp": False,
                         "ip": bool(en.get("ip")), "il": bool(en.get("il"))}}
    if en.get("ip"):
        extra["idx_pt_tg"] = ex.get("ip_tg")
        extra["idx_pt_sl"] = ex.get("ip_sl")
    if en.get("il"):
        extra["idx_px_tg"] = ex.get("il_tg")
        extra["idx_px_sl"] = ex.get("il_sl")
    rule_key = None
    try:
        rule_key = per.rule_key(gid, [])
        per.set_rule(rule_key, gid, [], target_rs=target_rs, sl_rs=sl_rs, mode=mode, **extra)
        log(f"armed exit rule {rule_key}: rs {target_rs:+.0f}/{sl_rs:+.0f} ip={extra.get('idx_pt_sl')}/{extra.get('idx_pt_tg')} "
            f"il={extra.get('idx_px_sl')}/{extra.get('idx_px_tg')} confirm={extra['confirm_mode']}@{extra['tf']}")
    except Exception as e:
        log(f"arm exit rule FAIL: {e} — EOD squareoff still protects")

    credit = (placed[1]["price"] or 0) - (placed[0]["price"] or 0)
    entry = {"group_id": gid, "rule_key": rule_key, "spot": spot, "ts": int(time.time()),
             "legs": placed, "lots": lots, "lot_size": lot_sz, "credit": round(credit, 2),
             "opt_type": opt_type, "dir": dir_, "mode": mode}
    msg = (f"[{mode.upper()}] SELL {s_tsym} @{placed[1]['price']} + BUY {w_tsym} @{placed[0]['price']} "
           f"×{lots}L (net {credit:+.2f}/unit)")
    _tg(f"🎯 <b>Level Slot ENTRY</b> {s['id']} [{mode.upper()}]\n"
        f"level {s.get('level')} ±{s.get('zone') or 0} · {'resistance → Bear Call' if opt_type == 'CE' else 'support → Bull Put'} · spot {spot:.1f}\n"
        f"SELL {s_tsym} @{placed[1]['price']}\nBUY {w_tsym} @{placed[0]['price']}\n"
        f"{lots} lot × {lot_sz} · net credit {credit:+.2f}/unit (₹{credit * q:+,.0f})\n"
        f"exit: ₹{ex.get('rs_sl') or '—'}/{ex.get('rs_tg') or '—'}{' ON' if en.get('rs') else ' off'} · "
        f"idx-pt {ex.get('ip_sl') or '—'}/{ex.get('ip_tg') or '—'}{' ON' if en.get('ip') else ' off'} · "
        f"idx-lvl {ex.get('il_sl') or '—'}/{ex.get('il_tg') or '—'}{' ON' if en.get('il') else ' off'} · {extra['confirm_mode']}@{extra['tf']}")
    return True, msg, entry


# ─────────────────────────── BTC (Delta, paper) ───────────────────────────
def _btc_chain(expiry_code=None):
    try:
        return delta_feed.chain("BTC", expiry_code=expiry_code, n=14) if delta_feed else None
    except Exception:
        return None


def _btc_fire(s):
    if s.get("kind") == "prem":
        return False, "BTC premium-level slot v1 me supported nahi (Delta option candles nahi) — Index slot use karo", None
    ch = _btc_chain()
    if not ch or not ch.get("rows"):
        return False, "Delta chain nahi mili", None
    spot = float(ch.get("spot") or 0)
    if not spot:
        return False, "BTC spot nahi mila", None
    opt_type, dir_ = ls.option_side(s)
    side_key = "ce" if opt_type == "CE" else "pe"
    rows = [r for r in ch["rows"] if r.get(side_key) and (r[side_key].get("ltp") or 0) > 0]
    if not rows:
        return False, "chain me priced strikes nahi", None
    atm = min(rows, key=lambda r: abs(r["strike"] - spot))
    hd = float(s.get("hedge_delta") or 0.25)
    otm = [r for r in rows if (r["strike"] > atm["strike"] if opt_type == "CE" else r["strike"] < atm["strike"])]
    wing = None
    for r in otm:
        d = r[side_key].get("delta")
        if d is None:
            continue
        if wing is None or abs(abs(float(d)) - hd) < abs(abs(float(wing[side_key]["delta"])) - hd):
            wing = r
    if wing is None:
        return False, "wing (delta) resolve fail — no naked", None
    lots = int(s.get("lots") or 1)
    try:
        import delta_ironfly_trader as dft
        cv = dft.CONTRACT_VALUE.get("BTC", 0.001)
        usd_inr = dft._usd_inr()
    except Exception:
        dft, cv, usd_inr = None, 0.001, 85.0
    gid = f"LVL_BTC_{s['slot']}_{int(time.time())}"
    legs = [{"symbol": wing[side_key]["symbol"], "side": "BUY", "entry_fill": float(wing[side_key]["ltp"]),
             "strike": wing["strike"], "delta": wing[side_key].get("delta")},
            {"symbol": atm[side_key]["symbol"], "side": "SELL", "entry_fill": float(atm[side_key]["ltp"]),
             "strike": atm["strike"], "delta": atm[side_key].get("delta")}]
    if dft is not None:
        for lg in legs:
            dft._record_leg(lg, cv=cv, lots=lots, group_id=gid, action="entry", mode="paper",
                            strategy=SID, log=_log)
    credit = legs[1]["entry_fill"] - legs[0]["entry_fill"]
    entry = {"group_id": gid, "btc": True, "spot": spot, "ts": int(time.time()), "legs": legs,
             "lots": lots, "cv": cv, "usd_inr": usd_inr, "expiry": ch.get("expiry"),
             "expiry_date": ch.get("expiry_date"), "credit": round(credit, 2),
             "opt_type": opt_type, "dir": dir_, "mode": "paper", "open": True}
    msg = (f"[PAPER/DELTA] SELL {legs[1]['symbol']} @${legs[1]['entry_fill']:.1f} + BUY {legs[0]['symbol']} "
           f"@${legs[0]['entry_fill']:.1f} ×{lots} lots (net ${credit:+.1f}/BTC)")
    _tg(f"🎯 <b>Level Slot ENTRY</b> {s['id']} [PAPER/DELTA]\nlevel {s.get('level')} ±{s.get('zone') or 0} · spot ${spot:,.0f}\n{msg}")
    return True, msg, entry


def _btc_mtm_inr(entry, marks):
    tot = 0.0
    for lg in entry["legs"]:
        m = marks.get(lg["symbol"])
        if m is None:
            return None                                    # freeze on missing mark
        diff = (lg["entry_fill"] - m) if lg["side"] == "SELL" else (m - lg["entry_fill"])
        tot += diff * entry["cv"] * entry["usd_inr"] * entry["lots"]
    return tot


def _btc_check_exits(s):
    """Own exit engine for BTC paper spreads (wick mode)."""
    e = s.get("entry") or {}
    if not e.get("btc") or not e.get("open"):
        return
    ch = _btc_chain(e.get("expiry"))
    spot = float((ch or {}).get("spot") or 0) or spot_now("BTC")
    marks = {}
    for r in (ch or {}).get("rows") or []:
        for k in ("ce", "pe"):
            if r.get(k) and r[k].get("symbol") and r[k].get("ltp") is not None:
                marks[r[k]["symbol"]] = float(r[k]["ltp"])
    ex = s.get("exit") or {}
    en = ex.get("enabled") or {}
    reason = None
    mtm = _btc_mtm_inr(e, marks)
    if en.get("rs") and mtm is not None:
        r = per.check_exit(mtm, float(ex.get("rs_tg") or 0), -abs(float(ex.get("rs_sl") or 0)))
        if r:
            reason = f"LVLSLOT_{r.upper()}_RS"
    if not reason and spot:
        trigs = per.trigger_levels({"enabled": {"ip": bool(en.get("ip")), "il": bool(en.get("il"))},
                                    "dir": e.get("dir", 1), "entry_spot": e.get("spot"),
                                    "idx_pt_tg": ex.get("ip_tg"), "idx_pt_sl": ex.get("ip_sl"),
                                    "idx_px_tg": ex.get("il_tg"), "idx_px_sl": ex.get("il_sl")})
        for t in trigs:
            if per.is_beyond(spot, t["level"], t["side"], int(e.get("dir", 1))):
                reason = f"LVLSLOT_{t['side'].upper()}_{t['src'].upper()}"
                break
    # cash-settle at expiry (12:00 UTC)
    if not reason and e.get("expiry_date"):
        try:
            exp_d = datetime.strptime(str(e["expiry_date"])[:10], "%Y-%m-%d").date()
            if datetime.now(timezone.utc) >= datetime(exp_d.year, exp_d.month, exp_d.day, 12, 0, tzinfo=timezone.utc):
                reason = "LVLSLOT_EXPIRY_SETTLE"
                for lg in e["legs"]:                       # intrinsic at settlement
                    intr = max(0.0, (spot - lg["strike"]) if e["opt_type"] == "CE" else (lg["strike"] - spot))
                    marks[lg["symbol"]] = intr
                mtm = _btc_mtm_inr(e, marks)
        except Exception:
            pass
    if not reason:
        return
    try:
        import delta_ironfly_trader as dft
        for lg in e["legs"]:
            if lg["symbol"] in marks:
                lg["exit_fill"] = marks[lg["symbol"]]
                dft._record_leg(lg, cv=e["cv"], lots=e["lots"], group_id=e["group_id"], action="exit",
                                mode="paper", strategy=SID, log=_log)
    except Exception as ex_:
        _log(f"BTC exit record fail: {ex_}")
    e["open"] = False
    e["exit_reason"] = reason
    e["exit_mtm_inr"] = mtm
    e["exit_ts"] = int(time.time())
    with ls._LOCK:
        d = ls._read()
        row = d["slots"].get(s["id"])
        if row:
            row["entry"] = e
            row["status"] = "exited"
            ls._ev(row, f"EXIT {reason} — MTM ₹{(mtm or 0):+,.0f}")
            ls._write(d)
    _log(f"{s['id']} BTC EXIT {reason} MTM ₹{(mtm or 0):+,.0f}")
    pts = sum(((lg["entry_fill"] - lg.get("exit_fill", lg["entry_fill"])) if lg["side"] == "SELL" else (lg.get("exit_fill", lg["entry_fill"]) - lg["entry_fill"])) for lg in e["legs"])
    _tg(f"🏁 <b>Level Slot EXIT</b> {s['id']} [PAPER/DELTA] — {reason}\nnet ${pts:+.1f}/BTC · ₹{(mtm or 0):+,.0f} · spot ${spot:,.0f}")
    if notify:
        try:
            notify.info(f"Level slot {s['id']} exit {reason} ₹{(mtm or 0):+,.0f}", source="level_slot")
        except Exception:
            pass


# ─────────────────────────── NSE exit detection (group closed by Trade Manager / EOD) ───────────────────────────
def _check_nse_exit(s):
    """Entered NSE slot: when its group has no open leg left, read the completed round-trips
    from order_store (the single ledger — no P&L math of our own), compute net premium POINTS
    + ₹, mark the slot exited, Telegram it. Freeze if the ledger can't be read."""
    e = s.get("entry") or {}
    gid = e.get("group_id")
    if not gid or e.get("btc") or e.get("closed") or order_store is None:
        return
    try:
        if order_store.open_legs_in_group(gid):
            return                                          # still open
        today = _ist_now().date()
        det = order_store.trades_for_range((today - timedelta(days=7)).isoformat(), today.isoformat()).get("details") or []
        rows = [d for d in det if str(d.get("group_id") or "") == gid]
    except Exception as ex_:
        _log(f"{s['id']} exit-check ledger read fail: {ex_}")
        return
    if not rows:
        return                                              # flat but no completed rows yet → wait
    pnl = sum(float(d.get("pnl") or 0) for d in rows)
    pts = 0.0
    for d in rows:
        ep, xp = float(d.get("entry_price") or 0), float(d.get("exit_price") or 0)
        pts += (ep - xp) if str(d.get("entry")).upper() == "SELL" else (xp - ep)
    reasons = sorted({str(d.get("exit_reason") or d.get("reason") or "") for d in rows} - {""})
    e["closed"] = True
    e["exit_pnl"] = round(pnl, 2)
    e["exit_pts"] = round(pts, 2)
    e["exit_reason"] = ", ".join(reasons) or "closed"
    e["exit_ts"] = int(time.time())
    with ls._LOCK:
        d = ls._read()
        row = d["slots"].get(s["id"])
        if row:
            row["entry"] = e
            row["status"] = "exited"
            ls._ev(row, f"EXIT {e['exit_reason']} — {pts:+.2f} pt · ₹{pnl:+,.0f}")
            ls._write(d)
    legs_txt = "\n".join(f"{d.get('entry')} {d.get('sym')} {d.get('entry_price')} → {d.get('exit_price')}" for d in rows)
    _tg(f"🏁 <b>Level Slot EXIT</b> {s['id']} [{str(e.get('mode', 'paper')).upper()}] — {e['exit_reason']}\n"
        f"{legs_txt}\nnet <b>{pts:+.2f} pt</b> · <b>₹{pnl:+,.0f}</b> (gross, {int(e.get('lots') or 0)} lot)")
    _log(f"{s['id']} EXIT {e['exit_reason']} {pts:+.2f}pt ₹{pnl:+,.0f}")
    if notify:
        try:
            notify.info(f"Level slot {s['id']} exit {e['exit_reason']} {pts:+.2f}pt ₹{pnl:+,.0f}", source="level_slot")
        except Exception:
            pass


# ─────────────────────────── fire dispatch ───────────────────────────
def fire_slot(s):
    if str(s.get("sym", "")).upper() == "BTC":
        return _btc_fire(s)
    return _nse_fire(s)


def _nse_session_open():
    if mc is not None:
        try:
            if not mc.is_trading_day():
                return False
        except Exception:
            pass
    hm = _ist_now().hour * 60 + _ist_now().minute
    return (9 * 60 + 15) <= hm <= (15 * 60 + 30)


def _no_entry_now():
    try:
        _sq, ne = rg.exit_time_config()
        h, m = rg.hm_tuple(ne)
        now = _ist_now()
        return (now.hour, now.minute) >= (h, m)
    except Exception:
        return False


def watch_once():
    """One evaluation pass over every armed slot (+ BTC exit checks). Returns #fired."""
    fired = 0
    nse_open = _nse_session_open()
    for s in ls.active_slots():
        sid = s["id"]
        is_btc = str(s.get("sym", "")).upper() == "BTC"
        if not is_btc and not nse_open:
            continue
        try:
            bars = fetch_bars(s)
            if not bars:
                continue
            s2, fire, changed = ls.advance(s, bars, _hm(), entry_confirm=s.get("entry_confirm") or "close")
            if changed:
                ls.apply_runtime(sid, s2)
            if not fire:
                continue
            if not is_btc and _no_entry_now():
                ls.set_status(sid, "expired", "break aaya par no-entry time ke baad — skip")
                continue
            if sid in _fired_session or not ls.claim(sid):
                continue
            _fired_session.add(sid)
            _log(f"FIRE {sid}: {s2.get('last_msg')}")
            try:
                ok, msg, entry = fire_slot(s2)
            except Exception as fe:
                ok, msg, entry = False, f"exception: {fe}", None
            ls.set_result(sid, ok, msg, entry)
            fired += 1
            _log(f"{sid} result: {'OK' if ok else 'FAIL'} — {msg}")
            if notify:
                try:
                    (notify.info if ok else notify.error)(
                        f"Level slot {sid} {'ENTERED' if ok else 'FAILED'}: {msg}", source="level_slot")
                except Exception:
                    pass
        except Exception as e:
            _log(f"{sid} watch error: {e}")
    # exits: NSE groups closed by Trade Manager/EOD → report; BTC paper → own engine
    try:
        for s in ls.list_slots():
            if s.get("status") != "entered":
                continue
            if str(s.get("sym", "")).upper() == "BTC":
                _btc_check_exits(s)
            else:
                _check_nse_exit(s)
    except Exception as e:
        _log(f"exit check error: {e}")
    return fired


def watch_loop():
    _log(f"watch loop started (~{_LOOP_SLEEP:.0f}s, {MODE} hard-lock)")
    ls.ensure_fixed()
    while True:
        try:
            watch_once()
        except Exception as e:
            _log(f"loop error: {e}")
        time.sleep(_LOOP_SLEEP)
