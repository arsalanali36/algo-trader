"""
atm_straddle_server.py  —  Real-time ATM Straddle Chart Backend
Run:   python atm_straddle_server.py
Chart: http://127.0.0.1:5001
"""

import json
import pathlib
import sys
import logging
from datetime import datetime, timedelta, timezone, date

import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import dhan_master
from brokers.dhan_broker import DhanBroker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("straddle_server")

app      = Flask(__name__)
CORS(app)

BASE_DIR    = pathlib.Path(__file__).parent
CONFIG_FILE = BASE_DIR / "data" / "config.json"

INDEX_SECIDS = {
    "NIFTY":     "13",
    "BANKNIFTY": "25",
    "FINNIFTY":  "26000",
}

OHLC_URL = "https://api.dhan.co/v2/marketfeed/ohlc"

def get_broker() -> DhanBroker:
    cfg = json.loads(CONFIG_FILE.read_text())
    return DhanBroker({"jwt_token": cfg["jwt_token"], "client_id": cfg["client_id"]})

def ist_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def today_ist() -> date:
    return ist_now().date()

def filter_today(df):
    """Keep only today's candles starting 09:15 IST."""
    if df is None or df.empty:
        return df
    today = today_ist()
    market_open = datetime(today.year, today.month, today.day, 9, 15, 0)
    mask = df["time"] >= market_open
    return df[mask].reset_index(drop=True)


def get_open_spot(b: DhanBroker, sec_id: str) -> float:
    """
    Fetch today's OPENING price for an index via Dhan OHLC REST endpoint.
    Dhan response format: {"data": {"IDX_I": {"13": {"ohlc": {"open": X}}}}}
    This is what Sensibull uses to lock the ATM at market open (9:15 AM).
    Falls back to None if OHLC endpoint is unavailable.
    """
    try:
        r = requests.post(
            OHLC_URL,
            json={"IDX_I": [int(sec_id)]},
            headers=b._hdrs(),
            timeout=5,
        )
        if r.status_code == 200:
            data = (r.json().get("data") or {}).get("IDX_I") or {}
            for sid, v in data.items():
                # Dhan actual format: {"ohlc": {"open": x, "high": x, ...}, "last_price": x}
                ohlc = v.get("ohlc") or {}
                op   = float(ohlc.get("open") or v.get("open") or 0)
                if op > 0:
                    log.info("OHLC open for %s: %.2f", idx if 'idx' in dir() else sec_id, op)
                    return op
    except Exception as e:
        log.warning("OHLC fetch failed (%s) — falling back to LTP", e)
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  /  — serve chart HTML
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR), "atm_straddle_chart.html")


# ─────────────────────────────────────────────────────────────────────────────
#  /api/status
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    return jsonify({
        "status":      "ok",
        "time_ist":    ist_now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "cache_ready": bool(dhan_master._options_cache),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  /api/spot
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/spot")
def api_spot():
    idx    = request.args.get("idx", "NIFTY").upper()
    sec_id = INDEX_SECIDS.get(idx)
    if not sec_id:
        return jsonify({"error": "unknown index"}), 400
    try:
        b   = get_broker()
        q   = b.quote(sec_id, "IDX_I")
        ltp = q.get("ltp")
        return jsonify({"ltp": ltp, "idx": idx})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  /api/premium
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/premium")
def api_premium():
    ce_id = request.args.get("ce_secid")
    pe_id = request.args.get("pe_secid")
    if not ce_id or not pe_id:
        return jsonify({"error": "missing ce_secid or pe_secid"}), 400
    try:
        b = get_broker()
        q_ce = b.quote(ce_id, "NSE_FNO")
        q_pe = b.quote(pe_id, "NSE_FNO")
        ce_ltp = float(q_ce.get("ltp") or 0)
        pe_ltp = float(q_pe.get("ltp") or 0)
        return jsonify({
            "ce_ltp": ce_ltp,
            "pe_ltp": pe_ltp,
            "straddle_ltp": round(ce_ltp + pe_ltp, 2)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
#  /api/expiries
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/expiries")
def api_expiries():
    idx = request.args.get("idx", "NIFTY").upper()
    if not dhan_master._options_cache:
        dhan_master.build_cache()
    sym_cache = dhan_master._options_cache.get(idx, {})
    today     = today_ist()
    result, seen = [], set()
    for exp_str in sorted(sym_cache.keys()):
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            continue
        if exp_date < today or exp_date in seen:
            continue
        seen.add(exp_date)
        is_weekly = exp_date.weekday() in (1, 2)  # Tue/Wed
        result.append({
            "date":   exp_date.isoformat(),
            "label":  exp_date.strftime("%d %b"),
            "weekly": is_weekly,
        })
    result.sort(key=lambda x: x["date"])
    return jsonify(result[:6])


# ─────────────────────────────────────────────────────────────────────────────
#  /api/straddle  — main endpoint
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/straddle")
def api_straddle():
    import pandas as pd

    idx    = request.args.get("idx",    "NIFTY").upper()
    expiry = request.args.get("expiry", "")          # YYYY-MM-DD
    tf     = max(1, int(request.args.get("tf", "1")))

    b = get_broker()

    # ── 1. Get spot prices ────────────────────────────────────────────────────
    spot_secid = INDEX_SECIDS.get(idx, "13")

    # live_spot = current LTP (for display in side panel)
    try:
        live_spot = float(b.quote(spot_secid, "IDX_I").get("ltp") or 24000)
    except Exception:
        live_spot = 24000.0

    # open_spot = today's OPENING price (for ATM lock — like Sensibull)
    open_spot = get_open_spot(b, spot_secid)
    if open_spot is None:
        # OHLC not available — fall back to live LTP
        open_spot = live_spot
        log.warning("OHLC unavailable, using live LTP %.2f for ATM", open_spot)
    else:
        log.info("ATM will be locked at open_spot=%.2f (live=%.2f)", open_spot, live_spot)
    if not dhan_master._options_cache:
        dhan_master.build_cache()
    sym_cache = dhan_master._options_cache.get(idx, {})

    ce_secid = pe_secid = ce_sym = pe_sym = atm_strike = None
    target_date = None
    if expiry:
        try:
            target_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        except Exception:
            pass

    today = today_ist()
    for exp_str in sorted(sym_cache.keys()):
        try:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").date()
        except Exception:
            continue
        if target_date:
            if exp_date != target_date:
                continue
        else:
            if exp_date < today:
                continue

        contracts  = sym_cache[exp_str]
        strikes_ce = sorted(set(c["strike"] for c in contracts if c["type"] == "CE"))
        if not strikes_ce:
            continue
        atm = min(strikes_ce, key=lambda x: abs(x - open_spot))

        for c in contracts:
            if c["strike"] == atm and c["type"] == "CE":
                ce_secid = c["sec_id"]; ce_sym = c["trad_sym"]; atm_strike = int(atm)
            if c["strike"] == atm and c["type"] == "PE":
                pe_secid = c["sec_id"]; pe_sym = c["trad_sym"]
        if ce_secid and pe_secid:
            break

    if not ce_secid or not pe_secid:
        return jsonify({"error": "ATM contracts not found"}), 404

    log.info("ATM=%d | CE=%s PE=%s tf=%dm spot=%.2f", atm_strike, ce_sym, pe_sym, tf, live_spot)

    # ── 3. Fetch CE + PE candles (today only, >= 09:15) ───────────────────────
    try:
        df_ce = b.intraday_candles(ce_secid, "NSE_FNO", "OPTIDX", days=1, interval=tf)
        df_pe = b.intraday_candles(pe_secid, "NSE_FNO", "OPTIDX", days=1, interval=tf)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    # Filter to today's session only
    df_ce = filter_today(df_ce)
    df_pe = filter_today(df_pe)

    if df_ce.empty or df_pe.empty:
        return jsonify({"error": "No candle data for today's session — market may be closed"}), 404

    # ── 4. Merge CE + PE ──────────────────────────────────────────────────────
    df_ce = df_ce.set_index("time")
    df_pe = df_pe.set_index("time")
    merged = df_ce[["close", "volume"]].join(
        df_pe[["close", "volume"]], lsuffix="_ce", rsuffix="_pe", how="inner"
    )
    merged["straddle"] = merged["close_ce"] + merged["close_pe"]

    # ── 5. NIFTY index candles for today (normalized to straddle scale) ────────
    # Sensibull shows NIFTY on SAME axis as straddle — we normalize:
    #   nifty_scaled = straddle_open + (nifty - nifty_open) * scale_factor
    nifty_col = None
    try:
        df_idx = b.intraday_candles(spot_secid, "IDX_I", "INDEX", days=1, interval=tf)
        df_idx = filter_today(df_idx)
        if not df_idx.empty:
            df_idx = df_idx.set_index("time")[["close"]].rename(columns={"close": "nifty_raw"})
            merged = merged.join(df_idx, how="left")
            merged["nifty_raw"] = merged["nifty_raw"].ffill().bfill().fillna(live_spot)

            # Normalize NIFTY to straddle scale (same visual axis)
            nifty_open    = float(merged["nifty_raw"].iloc[0])
            straddle_open = float(merged["straddle"].iloc[0])
            nifty_range   = float(merged["nifty_raw"].max() - merged["nifty_raw"].min())
            straddle_range = float(merged["straddle"].max() - merged["straddle"].min())
            scale = (straddle_range / nifty_range) if nifty_range > 0 else 1.0

            merged["nifty_scaled"] = (
                straddle_open + (merged["nifty_raw"] - nifty_open) * scale
            ).round(2)
            nifty_col = "nifty_scaled"
    except Exception as e:
        log.warning("NIFTY index candle failed (%s) — skipping", e)

    if nifty_col is None:
        merged["nifty_scaled"] = float(live_spot)  # flat fallback

    # ── 6. VWAP ───────────────────────────────────────────────────────────────
    total_vol      = merged["volume_ce"] + merged["volume_pe"]
    cum_pv         = (merged["straddle"] * total_vol).cumsum()
    cum_v          = total_vol.cumsum()
    merged["vwap"] = (cum_pv / cum_v.replace(0, 1)).round(2)

    # ── 7. Build response ─────────────────────────────────────────────────────
    records = []
    for ts, row in merged.iterrows():
        records.append({
            "t":     ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "ltp":   round(float(row["straddle"]), 2),
            "vwap":  round(float(row["vwap"]), 2),
            "ce":    round(float(row["close_ce"]), 2),
            "pe":    round(float(row["close_pe"]), 2),
            # nifty_raw for tooltip, nifty_scaled for chart line (same axis as straddle)
            "nifty":        round(float(merged.get("nifty_raw", merged["nifty_scaled"])[ts]
                                         if "nifty_raw" in merged.columns else live_spot), 2),
            "nifty_scaled": round(float(row["nifty_scaled"]), 2),
        })

    return jsonify({
        "idx":        idx,
        "atm_strike": atm_strike,
        "ce_sym":     ce_sym,
        "pe_sym":     pe_sym,
        "ce_secid":   ce_secid,
        "pe_secid":   pe_secid,
        "live_spot":  round(live_spot, 2),
        "tf":         tf,
        "candles":    records,
    })


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log.info("Building Dhan scrip master cache...")
    dhan_master.download_master_if_needed()
    dhan_master.build_cache()
    log.info("ATM Straddle Server → http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
