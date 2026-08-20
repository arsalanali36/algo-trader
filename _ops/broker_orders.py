"""broker_orders.py — DISPLAY-ONLY broker order/trade book (Zerodha) + CSV match.

Powers the Broker Orders page (/broker-orders). Zerodha ka LIVE order book +
trade book (fills) laata hai, har order/trade ko app ke `order_store` se JOIN
karta hai (strategy/mode by broker_order_id), aur app-side RMS-blocked entries
(skipped_store) bhi surface karta hai jo broker tak pahunche hi nahi. Plus:
uploaded Zerodha CSV ko live broker trades se MATCH karke "exact match ho raha
ki nahi" dikhata hai.

Koi order/risk/execution path NAHI — pure READ. Kite order/trade book
`KiteBroker.orders()`/`.trades()` se; CSV parse `reconcile_csv.parse_zerodha_
tradebook` se reuse (Rule 6B). Fail-safe: har error swallow → {ok:False,error}.
"""
import datetime

import _paths  # noqa: F401  (sys.path bootstrap)


def _ist_today():
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    return now.strftime("%Y-%m-%d")


def _norm_ts(instr):
    """Kite tradingsymbol -> app trad_sym via reconcile_csv (structured, no guess)."""
    try:
        import reconcile_csv
        root, ts = reconcile_csv.kite_to_trad_sym(instr)
        return ts or instr
    except Exception:
        return instr


def _strategy_maps(date):
    """order_store ke aaj ke LIVE rows se broker_order_id -> {strategy, mode, source}.

    SIRF exact broker_order_id link — trad_sym fallback NAHI (wo guess hai aur
    galat attribute karta hai). LIVE-only: broker order book = real Zerodha
    account; PAPER strategies kabhi real order nahi dete, to unhe attribute karna
    = paper/manual mix (galat). Real order jiska app me broker_order_id nahi =
    genuinely manual/external → 'unmatched'."""
    by_oid = {}
    try:
        import order_store
        for r in order_store.query(date=date, limit=8000):
            if (r.get("mode") or "").lower() == "paper":
                continue                       # real order != paper strategy
            oid = str(r.get("broker_order_id") or "").strip()
            if not oid:
                continue                       # no exact link → can't attribute
            by_oid.setdefault(oid, {"strategy": r.get("strategy") or "",
                                    "mode": r.get("mode") or "",
                                    "source": r.get("source") or ""})
    except Exception as e:
        print("[broker_orders] strategy_maps fail:", e, flush=True)
    return by_oid


def _label(strategy_id):
    try:
        import strategy_registry as sr
        return sr.label(strategy_id, with_name=True)
    except Exception:
        return str(strategy_id or "")


def _attach_strategy(oid, by_oid):
    """Exact broker_order_id match only. Returns (label, mode, matched)."""
    info = by_oid.get(str(oid or "").strip())
    if not info:
        return "", "", False
    return _label(info["strategy"]), info["mode"], True


def fetch(broker="kite", date=None):
    """LIVE (today's) broker order book + trade book + app-blocked entries,
    strategy-annotated. Returns {ok, broker, date, today_only, note, orders,
    trades, blocked, summary, error}.

    NOTE: Kite `orders()`/`trades()` sirf AAJ ka book dete hain (historical API
    nahi) — `date` param ignore hota hai, hamesha aaj. Purane din ke liye CSV
    match use karo."""
    date = _ist_today()   # broker book today-only → strategy join bhi today
    out = {"ok": False, "broker": broker, "date": date, "today_only": True,
           "note": "Broker order/trade book = aaj ka (Kite historical nahi deta). Purane din ke liye niche CSV match karo.",
           "orders": [], "trades": [], "blocked": [], "summary": {}, "error": ""}
    try:
        from brokers import get_broker
        bk = get_broker(broker)
    except Exception as e:
        out["error"] = "broker init fail: %s" % e
        return out

    by_oid = _strategy_maps(date)

    # ── Order book (executed / rejected / cancelled / open) ──
    try:
        raw_orders = bk.orders() if hasattr(bk, "orders") else []
    except Exception as e:
        raw_orders = []
        out["error"] = "orders() fail: %s" % e
    orders = []
    for o in raw_orders:
        ts = _norm_ts(o.get("tradingsymbol", ""))
        lbl, mode, matched = _attach_strategy(o.get("order_id"), by_oid)
        o2 = dict(o)
        o2["trad_sym"] = ts
        o2["strategy"] = lbl
        o2["mode"] = mode
        o2["matched"] = matched
        orders.append(o2)

    # ── Trade book (fills) ──
    try:
        raw_trades = bk.trades() if hasattr(bk, "trades") else []
    except Exception as e:
        raw_trades = []
    trades = []
    for t in raw_trades:
        try:
            ts = _norm_ts(t.get("tradingsymbol", ""))
            lbl, mode, matched = _attach_strategy(t.get("order_id"), by_oid)
            px = float(t.get("average_price") or t.get("price") or 0)
            trades.append({
                "trade_id": str(t.get("trade_id") or ""),
                "order_id": str(t.get("order_id") or ""),
                "time": str(t.get("fill_timestamp") or t.get("exchange_timestamp")
                            or t.get("order_timestamp") or ""),
                "tradingsymbol": t.get("tradingsymbol") or "",
                "trad_sym": ts,
                "exchange": t.get("exchange") or "",
                "side": str(t.get("transaction_type") or "").upper(),
                "product": t.get("product") or "",
                "qty": int(t.get("quantity") or t.get("filled_quantity") or 0),
                "price": px if px > 0 else None,
                "strategy": lbl,
                "mode": mode,
                "matched": matched,
            })
        except Exception:
            continue

    # ── App-side RMS-blocked entries (never reached the broker) ──
    blocked = []
    try:
        import skipped_store
        for s in skipped_store.query(date_from=date, date_to=date):
            blocked.append({
                "time": (s.get("ts") or "")[11:19],
                "strategy": _label(s.get("strategy")),
                "symbol": s.get("symbol") or "",
                "trad_sym": s.get("trad_sym") or "",
                "side": str(s.get("side") or "").upper(),
                "opt_type": s.get("opt_type") or "",
                "lots": s.get("intended_lots"),
                "mode": s.get("mode") or "",
                "reason": s.get("block_reason") or "",
                "detail": s.get("block_detail") or "",
            })
    except Exception as e:
        print("[broker_orders] blocked read fail:", e, flush=True)

    # ── Summary ──
    n_complete = sum(1 for o in orders if o.get("status") == "COMPLETE")
    n_reject = sum(1 for o in orders if o.get("status") in ("REJECTED", "CANCELLED"))
    n_mis = sum(1 for o in orders if str(o.get("product")).upper() == "MIS")
    n_nrml = sum(1 for o in orders if str(o.get("product")).upper() in ("NRML", "CNC"))
    out["summary"] = {
        "orders": len(orders), "complete": n_complete, "rejected": n_reject,
        "mis": n_mis, "nrml": n_nrml, "trades": len(trades),
        "matched": sum(1 for o in orders if o.get("matched")),
        "blocked": len(blocked),
    }
    out["orders"] = orders
    out["trades"] = trades
    out["blocked"] = blocked
    out["ok"] = True
    return out


_BAD_STATUS = {"rejected", "cancelled", "canceled", "failed", "expired", "blocked"}

# tags jo APP ki apni RMS / auto-exit action batate hain (manual/external NAHI)
_RMS_TAG_KEYS = (
    "RMS_MAXLOSS", "RMS_PROFIT_TARGET", "KILL_FLOOR", "POS_MONITOR_EXIT",
    "DEFAULT_TSL_SL", "DEFAULT_TSL_TARGET", "ATR_TRAILING", "RSI_MIDLINE_EXIT",
    "SL_HIT", "TP_HIT", "SL_PCT", "TP_PCT", "EOD_315_SQUAREOFF", "EXPIRY_EOD",
    "EXPIRY_ITM", "GROUP_SL", "GROUP_TARGET", "TRAILING_LOCK", "PER_INSTRUMENT_LOCK",
)


def _origin(strategy, source, tags):
    """Order ka asli 'kaun/kyun' — strategy + source + tags se. Live rows aksar
    broker-reconcile se aati hain (strategy='manual'); asli origin tags me hota
    hai. Returns (label, kind)  kind: strategy|rms|manual."""
    strat = (strategy or "").lower()
    blob = " ".join(str(t) for t in (tags or [])).upper()
    # 1) asli strategy (manual/unknown/default nahi)
    if strat and strat not in ("manual", "unknown", "default", ""):
        return _label(strategy), "strategy"
    # 2) app ki RMS / auto-exit action (tags se) — reason ke saath
    hit = None
    for k in _RMS_TAG_KEYS:
        if k in blob:
            hit = k
            break
    if hit or "POS_MONITOR" in blob or "RMS_" in blob:
        pretty = (hit or "RMS").replace("_", " ").title()
        return "🤖 RMS / " + pretty, "rms"
    # 3) external/manual (broker-mirror ya seedha manual)
    if "EXTERNALLY_RECORDED" in blob or "BROKER_MIRROR" in blob:
        return "✋ Manual / external", "manual"
    if (source or "").lower() in ("manual", "manual_trigger"):
        return "✋ Manual", "manual"
    return "✋ Manual", "manual"


def _exit_reason_from_tags(tags):
    """Exit reason ONLY from explicit exit-reason TAGS (prefix match), reusing
    order_store's canonical `_EXIT_REASON_PREFIXES` (Rule 6B — single source).
    Tag-only on purpose: those prefixes are stamped ONLY on exit legs, so a manual
    ENTRY (source=manual) never gets mislabelled as a close here. '' if none."""
    try:
        import order_store
        prefixes = order_store._EXIT_REASON_PREFIXES
    except Exception:
        return ""
    for t in (tags or []):
        for p in prefixes:
            if str(t).startswith(p):
                return str(t)
    return ""


def _order_bad(status, tags, price):
    """galat / problem order? -> (bad, why). Blocked/rejected/cancelled/₹0-fill."""
    st = (status or "").lower()
    if st in _BAD_STATUS:
        if st == "blocked":
            for t in (tags or []):
                if "CAPITAL_BLOCKED" in str(t):
                    return True, "⛔ RMS capital block"
            return True, "⛔ blocked (RMS)"
        if st == "rejected":
            return True, "⚠ broker rejected"
        if st in ("cancelled", "canceled"):
            return True, "✕ cancelled"
        return True, st
    try:
        p = float(price or 0)
    except Exception:
        p = 0
    if p == 0 and st in ("paper", "filled", "open", "complete", "traded"):
        return True, "⚠ ₹0 price (fill fail?)"
    return False, ""


def fetch_app(date=None, mode=None):
    """App ke APNE order records (order_store) — PAPER + REAL, kisi bhi date ke.
    Har row = ek order jo fire hua (entry/exit), status ke saath. `mode`:
    'live'/'real' | 'paper' | None(all). galat orders (rejected/cancelled/blocked/
    ₹0-fill) flag hote hain. Returns {ok,date,mode,orders,strategies,summary,error}."""
    date = date or _ist_today()
    md = (mode or "").lower()
    if md == "real":
        md = "live"
    out = {"ok": False, "date": date, "mode": md or "all",
           "orders": [], "strategies": [], "summary": {}, "error": ""}
    try:
        import order_store
        import json as _json
        rows, strat = [], set()
        for r in order_store.query(date=date, limit=20000):
            m = (r.get("mode") or "").lower()
            if md == "live" and m != "live":
                continue
            if md == "paper" and m == "live":
                continue
            try:
                tags = _json.loads(r.get("tags") or "[]")
            except Exception:
                tags = []
            bad, why = _order_bad(r.get("status"), tags, r.get("price"))
            lbl, kind = _origin(r.get("strategy"), r.get("source"), tags)
            if lbl:
                strat.add(lbl)
            exit_reason = _exit_reason_from_tags(tags)
            # status-level externally-closed = position closed at broker outside
            # the app's exit path — surface it if no explicit exit tag was found.
            if not exit_reason and str(r.get("status") or "").upper() == "EXTERNALLY_CLOSED":
                exit_reason = "EXTERNALLY_CLOSED"
            rows.append({
                "time": (r.get("ts") or "")[11:19],
                "mode": ("Real" if m == "live" else "Paper"),
                "raw_mode": m,
                "side": str(r.get("side") or "").upper(),
                "trad_sym": r.get("trad_sym") or r.get("symbol") or "",
                "strategy": lbl,
                "origin_kind": kind,
                "qty": r.get("qty"),
                "price": r.get("price"),
                "status": str(r.get("status") or "").upper(),
                "source": r.get("source") or "",
                "broker": r.get("broker") or "",
                "bad": bad,
                "why": why,
                "exit_reason": exit_reason,
            })
        # ── App-side decisions jo broker tak pahunche hi NAHI: smart-size
        # skip/size-down + RMS-blocked entries (skipped_store). Inhe usi table me
        # surface karo taaki "entry kyun nahi lagi / kam lots pe kyun lagi" ek
        # nazar me dikhe — poochna na pade. bad=True → Note me reason (red).
        try:
            import skipped_store
            for s in skipped_store.query(date_from=date, date_to=date):
                sm = (s.get("mode") or "").lower()
                if md == "live" and sm != "live":
                    continue
                if md == "paper" and sm == "live":
                    continue
                # skipped_store: block_reason = CATEGORY (size_skip/size_down/…),
                # block_detail = full human text. Note me detail dikhao, status
                # category se.
                cat = (s.get("block_reason") or "").lower()
                reason = s.get("block_detail") or s.get("block_reason") or ""
                status = ("SKIPPED" if cat == "size_skip"
                          else "REDUCED" if cat == "size_down" else "BLOCKED")
                lbl2, kind2 = _origin(s.get("strategy"), "", [])
                if lbl2:
                    strat.add(lbl2)
                rows.append({
                    "time": (s.get("ts") or "")[11:19],
                    "mode": ("Real" if sm == "live" else "Paper"),
                    "raw_mode": sm,
                    "side": str(s.get("side") or "").upper(),
                    "trad_sym": s.get("trad_sym") or s.get("symbol") or "",
                    "strategy": lbl2 or _label(s.get("strategy")),
                    "origin_kind": kind2 or "rms",
                    "qty": s.get("intended_qty"),
                    "price": s.get("entry_premium"),
                    "status": status,
                    "source": "decision",
                    "broker": "",
                    "bad": True,
                    "why": reason,
                    "exit_reason": "",
                })
        except Exception as _se:
            print("[broker_orders] fetch_app skipped read fail:", _se, flush=True)

        n_live = sum(1 for x in rows if x["raw_mode"] == "live")
        n_bad = sum(1 for x in rows if x["bad"])
        out["orders"] = rows
        out["strategies"] = sorted(s for s in strat if s)
        out["summary"] = {
            "total": len(rows), "real": n_live, "paper": len(rows) - n_live,
            "bad": n_bad, "good": len(rows) - n_bad,
            "rejected": sum(1 for x in rows if x["status"] in ("REJECTED", "CANCELLED")),
            "blocked": sum(1 for x in rows if x["status"] == "BLOCKED"),
            "skipped": sum(1 for x in rows if x["status"] == "SKIPPED"),
            "reduced": sum(1 for x in rows if x["status"] == "REDUCED"),
        }
        out["ok"] = True
    except Exception as e:
        out["error"] = str(e)
        print("[broker_orders] fetch_app fail:", e, flush=True)
    return out


def _net_from_fills(fills):
    """[{trad_sym,side,qty}] -> {trad_sym: {net, buys, sells, n}}"""
    agg = {}
    for f in fills:
        ts = f.get("trad_sym") or ""
        if not ts:
            continue
        d = agg.setdefault(ts, {"net": 0, "buys": 0, "sells": 0, "n": 0})
        q = int(f.get("qty") or 0)
        if str(f.get("side")).upper() == "BUY":
            d["net"] += q; d["buys"] += q
        else:
            d["net"] -= q; d["sells"] += q
        d["n"] += 1
    return agg


def csv_match(csv_text, broker="kite"):
    """Uploaded Zerodha tradebook CSV ko LIVE broker trades se per-contract MATCH.
    Returns {ok, rows, exact_match, summary, error}. Display-only — koi write nahi.
    rows: [{trad_sym, csv_net, broker_net, csv_fills, broker_fills, match, note}]"""
    res = {"ok": False, "rows": [], "exact_match": False, "summary": {}, "error": ""}
    # 1) parse CSV
    try:
        import reconcile_csv
        # parse_zerodha_tradebook -> (fills, unresolved, date)
        csv_fills, _unresolved, _cdate = reconcile_csv.parse_zerodha_tradebook(csv_text or "")
    except Exception as e:
        res["error"] = "CSV parse fail: %s" % e
        return res
    if not csv_fills:
        res["error"] = "CSV me koi trade row nahi mili (Zerodha tradebook export do)."
        return res
    csv_agg = _net_from_fills(csv_fills)

    # 2) live broker trades (normalized)
    broker_fills = []
    try:
        from brokers import get_broker
        bk = get_broker(broker)
        for t in (bk.trades() if hasattr(bk, "trades") else []):
            broker_fills.append({
                "trad_sym": _norm_ts(t.get("tradingsymbol", "")),
                "side": str(t.get("transaction_type") or "").upper(),
                "qty": int(t.get("quantity") or 0),
            })
    except Exception as e:
        res["error"] = "broker trades fetch fail: %s" % e
        return res
    broker_agg = _net_from_fills(broker_fills)

    # 3) per-contract compare
    rows, n_match, n_mismatch = [], 0, 0
    for ts in sorted(set(csv_agg) | set(broker_agg)):
        c = csv_agg.get(ts, {"net": 0, "n": 0})
        b = broker_agg.get(ts, {"net": 0, "n": 0})
        match = (c["net"] == b["net"]) and (ts in csv_agg) and (ts in broker_agg)
        if match:
            n_match += 1
            note = "exact match"
        else:
            n_mismatch += 1
            if ts not in broker_agg:
                note = "CSV me hai, broker trade-book me nahi"
            elif ts not in csv_agg:
                note = "broker pe hai, CSV me nahi"
            else:
                note = "net qty alag (CSV %d vs broker %d)" % (c["net"], b["net"])
        rows.append({
            "trad_sym": ts,
            "csv_net": c["net"], "broker_net": b["net"],
            "csv_fills": c["n"], "broker_fills": b["n"],
            "match": match, "note": note,
        })

    res["rows"] = rows
    res["exact_match"] = (n_mismatch == 0 and n_match > 0)
    res["summary"] = {
        "contracts": len(rows), "matched": n_match, "mismatched": n_mismatch,
        "csv_fills": len(csv_fills), "broker_fills": len(broker_fills),
    }
    res["ok"] = True
    return res
