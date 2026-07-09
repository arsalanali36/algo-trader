"""
mfe_routes.py — MFE/MAE analysis routes.
Registered in trader_dashboard.py via: from mfe_routes import register_mfe_routes; register_mfe_routes(app, BASE_DIR)
"""
import csv
import json
import time
import datetime
import base64
import requests
from pathlib import Path
from flask import jsonify, request


def register_mfe_routes(app, BASE_DIR):
    LOT_SIZE = 65
    TRADE_LOG = BASE_DIR / "data" / "trade_log.json"

    def _get_headers():
        cfg_file = BASE_DIR / "data" / "config.json"
        cfg = json.loads(cfg_file.read_text()) if cfg_file.exists() else {}
        token = cfg.get("jwt_token", "")
        client = cfg.get("client_id", "")
        if not client:
            try:
                payload = token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                client = json.loads(base64.b64decode(payload)).get("dhanClientId", "")
            except Exception:
                pass
        return {"access-token": token, "client-id": client, "Content-Type": "application/json"}

    def _build_scrip_index(symbol, strike, opt_type):
        """Build a dict of exp_str -> sec_id from scrip master for given symbol/strike/type."""
        scrip = BASE_DIR / "data" / "api-scrip-master.csv"
        index = {}  # "16 JUN" -> sec_id
        target_name = f"{symbol}-"
        target_strike = f"-{int(strike)}-{opt_type}"
        with open(scrip, encoding="utf-8") as f:
            for row in csv.reader(f):
                if len(row) < 9:
                    continue
                name = row[5]
                desc = row[7]
                if (target_name in name
                        and target_strike in name
                        and "FINNIFTY" not in name
                        and "BANKNIFTY" not in name
                        and "MIDCAP" not in name):
                    # Extract exp_str from desc e.g. "NIFTY 16 JUN 24100 CALL" -> "16 JUN"
                    parts = desc.split()
                    if len(parts) >= 3:
                        exp_str = parts[1] + " " + parts[2]  # "16 JUN"
                        index[exp_str] = row[2]
        return index

    def _find_sec_id(symbol, strike, opt_type, trade_date_str):
        """Find sec_id for a NIFTY option.
        Order: 1) sec_id_cache.json (for expired contracts found by probe)
               2) scrip master (for active/upcoming contracts) + verify data exists
        """
        # 1. Check persistent cache (populated by generate_june_mfe.py probe)
        cache_file = BASE_DIR / "data" / "sec_id_cache.json"
        cache_key = f"{trade_date_str}_{int(strike)}_{opt_type}"
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text())
                if cache_key in cache and cache[cache_key]:
                    cached = cache[cache_key]
                    return cached, None, "CACHED"
            except Exception:
                pass

        # 2. Scrip master walk-forward
        trade_date = datetime.datetime.strptime(trade_date_str, "%Y-%m-%d").date()
        scrip_index = _build_scrip_index(symbol, strike, opt_type)
        if not scrip_index:
            return None, None, None

        for offset in range(22):
            candidate = trade_date + datetime.timedelta(days=offset)
            exp_str = candidate.strftime("%-d %b").upper()
            if exp_str in scrip_index:
                return scrip_index[exp_str], candidate.strftime("%Y-%m-%d"), exp_str

        return None, None, None

    def _fetch_bars(sec_id, exchange, instrument, date_str):
        hdrs = _get_headers()
        r = requests.post("https://api.dhan.co/v2/charts/intraday", headers=hdrs, json={
            "securityId": sec_id, "exchangeSegment": exchange, "instrument": instrument,
            "expiryCode": 0, "fromDate": date_str, "toDate": date_str,
        })
        d = r.json()
        if "open" not in d:
            return {}
        out = {}
        for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
            t = datetime.datetime.fromtimestamp(ts).strftime("%H:%M")
            out[t] = (round(float(o), 2), round(float(h), 2), round(float(l), 2), round(float(c), 2))
        return out

    def _analyse_one(tr):
        date      = tr["date"]
        entry_t   = tr["entry_time"]
        exit_t    = tr["exit_time"]
        strike    = int(tr["strike"])
        opt_type  = tr["opt_type"]
        sell_px   = float(tr["sell_price"])
        exit_px   = float(tr["exit_price"])
        direction = tr["direction"]
        nf_entry  = float(tr.get("nifty_entry") or 0) or None

        # Duration
        efmt = datetime.datetime.strptime(entry_t, "%H:%M")
        xfmt = datetime.datetime.strptime(exit_t,  "%H:%M")
        dur  = int((xfmt - efmt).total_seconds() / 60)
        dur_str = f"{dur//60}h{dur%60:02d}m" if dur >= 60 else f"{dur}m"

        sec_id, exp_date, exp_label = _find_sec_id("NIFTY", strike, opt_type, date)
        if not sec_id:
            return {"label": tr["label"], "date": date, "error": f"sec_id not found for NIFTY {strike}{opt_type} on {date}"}

        time.sleep(1)
        opt_bars = _fetch_bars(sec_id, "NSE_FNO", "OPTIDX", date)
        time.sleep(1)
        nf_bars  = _fetch_bars("13", "IDX_I", "INDEX", date)

        opt_sl = {t: v for t, v in opt_bars.items() if entry_t <= t <= exit_t}
        nf_sl  = {t: v for t, v in nf_bars.items()  if entry_t <= t <= exit_t}

        if not opt_sl:
            return {"label": tr["label"], "date": date,
                    "error": f"No option data ({date} {entry_t}-{exit_t}, expiry {exp_label})"}

        # Option seller analysis (profit = premium drop)
        opt_lows  = [v[2] for v in opt_sl.values()]
        opt_highs = [v[1] for v in opt_sl.values()]
        best_px   = min(opt_lows)
        worst_px  = max(opt_highs)
        best_t    = next(t for t, v in opt_sl.items() if v[2] == best_px)
        worst_t   = next(t for t, v in opt_sl.items() if v[1] == worst_px)
        ru_pts    = sell_px - best_px
        dd_pts    = max(0.0, worst_px - sell_px)

        # NIFTY index
        nf_exit = nf_ru = nf_dd = best_nf = best_nft = worst_nf = worst_nft = None
        if nf_sl and nf_entry:
            nf_lows  = [v[2] for v in nf_sl.values()]
            nf_highs = [v[1] for v in nf_sl.values()]
            eb = nf_sl.get(exit_t)
            nf_exit = eb[3] if eb else list(nf_sl.values())[-1][3]
            if direction == "SHORT":
                best_nf   = min(nf_lows)
                worst_nf  = max(nf_highs)
                best_nft  = next(t for t, v in nf_sl.items() if v[2] == best_nf)
                worst_nft = next(t for t, v in nf_sl.items() if v[1] == worst_nf)
                nf_ru = nf_entry - best_nf
                nf_dd = max(0.0, worst_nf - nf_entry)
            else:
                best_nf   = max(nf_highs)
                worst_nf  = min(nf_lows)
                best_nft  = next(t for t, v in nf_sl.items() if v[1] == best_nf)
                worst_nft = next(t for t, v in nf_sl.items() if v[2] == worst_nf)
                nf_ru = best_nf - nf_entry
                nf_dd = max(0.0, nf_entry - worst_nf)

        return {
            "label":         tr["label"],
            "date":          date,
            "direction":     direction,
            "entry_time":    entry_t,
            "exit_time":     exit_t,
            "duration":      dur_str,
            "opt_type":      opt_type,
            "expiry_used":   exp_label,
            "sell_price":    sell_px,
            "exit_price":    exit_px,
            "pnl":           round((sell_px - exit_px) * LOT_SIZE, 2),
            "max_runup_pts": round(ru_pts, 2),
            "max_runup_inr": round(ru_pts * LOT_SIZE, 2),
            "runup_at":      best_t,
            "max_dd_pts":    round(dd_pts, 2),
            "max_dd_inr":    round(dd_pts * LOT_SIZE, 2),
            "dd_at":         worst_t,
            "nifty_entry":   nf_entry,
            "nifty_exit":    round(nf_exit, 2) if nf_exit else None,
            "nifty_move":    round(nf_exit - nf_entry, 2) if (nf_exit and nf_entry) else None,
            "nf_runup":      round(nf_ru, 2) if nf_ru is not None else None,
            "nf_dd":         round(nf_dd, 2) if nf_dd is not None else None,
            "nf_runup_at":   best_nft,
            "nf_dd_at":      worst_nft,
        }

    # ── Routes ──────────────────────────────────────────────────────────────

    @app.route("/api/mfe/trades", methods=["GET"])
    def api_mfe_trades_get():
        trades = json.loads(TRADE_LOG.read_text()) if TRADE_LOG.exists() else []
        return jsonify(trades)

    @app.route("/api/mfe/trades", methods=["POST"])
    def api_mfe_trades_add():
        tr = request.json
        trades = json.loads(TRADE_LOG.read_text()) if TRADE_LOG.exists() else []
        trades.append(tr)
        TRADE_LOG.write_text(json.dumps(trades, indent=2))
        return jsonify({"ok": True})

    @app.route("/api/mfe/trades/<int:idx>", methods=["DELETE"])
    def api_mfe_trades_delete(idx):
        trades = json.loads(TRADE_LOG.read_text()) if TRADE_LOG.exists() else []
        if 0 <= idx < len(trades):
            trades.pop(idx)
            TRADE_LOG.write_text(json.dumps(trades, indent=2))
        return jsonify({"ok": True})

    @app.route("/api/mfe/analyse", methods=["POST"])
    def api_mfe_analyse():
        body      = request.json or {}
        from_date = body.get("from_date")
        trades    = json.loads(TRADE_LOG.read_text()) if TRADE_LOG.exists() else []
        if from_date:
            trades = [t for t in trades if t["date"] >= from_date]
        results = []
        for tr in trades:
            results.append(_analyse_one(tr))
        return jsonify(results)

    @app.route("/api/mfe/generate-backtest", methods=["POST"])
    def api_mfe_generate_backtest():
        import subprocess
        body      = request.json or {}
        from_date = body.get("from_date", "2026-06-02")
        to_date   = body.get("to_date", datetime.date.today().isoformat())
        script    = BASE_DIR / "generate_june_mfe.py"
        try:
            result = subprocess.run(
                ["python3", str(script), "--from", from_date, "--to", to_date],
                capture_output=True, text=True, cwd=str(BASE_DIR), timeout=600)
            output = result.stdout[-3000:] if result.stdout else ""
            errors = result.stderr[-500:]  if result.stderr else ""
            return jsonify({"ok": result.returncode == 0, "output": output, "errors": errors})
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "output": "", "errors": "Timeout after 10 min"})
