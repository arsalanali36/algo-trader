"""
trade_mfe_mae.py — MFE/MAE analyser for CE Sell trades

Usage:
    python trade_mfe_mae.py

Add trades to the TRADES list at the bottom.
Each trade needs: date, direction, entry_time, exit_time, sec_id, sell_price, exit_price, lot_size

sec_id lookup (NIFTY weekly CE):
  56376 = NIFTY Jun23 24100 CE  (used for Jun 17 analysis)
  Find others: grep 'NIFTY.*<STRIKE>.*CE' data/api-scrip-master.csv
"""

import json, requests, datetime, sys
from pathlib import Path

# ── Dhan credentials ──────────────────────────────────────────────────────────
cfg = json.load(open(Path(__file__).parent / "data/config.json"))
# client_id: embedded in JWT payload (field "dhanClientId") as fallback
import base64 as _b64
def _client_id_from_jwt(token):
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(_b64.b64decode(payload)).get("dhanClientId", "")

CLIENT_ID = cfg.get("client_id") or _client_id_from_jwt(cfg["jwt_token"])
HEADERS = {
    "access-token": cfg["jwt_token"],
    "client-id":    CLIENT_ID,
    "Content-Type": "application/json",
}

LOT_SIZE = 65  # NIFTY current lot size


def fetch_intraday(sec_id: str, exchange: str, instrument: str, date_str: str):
    """Returns list of (time_str, open, high, low, close) for the given date."""
    r = requests.post(
        "https://api.dhan.co/v2/charts/intraday",
        headers=HEADERS,
        json={
            "securityId":    sec_id,
            "exchangeSegment": exchange,
            "instrument":    instrument,
            "expiryCode":    0,
            "fromDate":      date_str,
            "toDate":        date_str,
        },
    )
    d = r.json()
    if "open" not in d:
        raise ValueError(f"Dhan error for sec_id={sec_id} date={date_str}: {d}")
    bars = []
    for ts, o, h, l, c in zip(d["timestamp"], d["open"], d["high"], d["low"], d["close"]):
        t = datetime.datetime.fromtimestamp(ts)
        bars.append((t.strftime("%H:%M"), float(o), float(h), float(l), float(c)))
    return bars


def analyse_trade(trade: dict) -> dict:
    """
    trade keys:
        date        str  "2026-06-17"
        direction   str  "SHORT" or "LONG"
        entry_time  str  "11:05"
        exit_time   str  "13:35"
        ce_sec_id   str  Dhan sec_id for the CE contract
        sell_price  float  actual CE sell price
        exit_price  float  actual CE buy-back price
        label       str  human label e.g. "Jun17 Short 24100CE"
    """
    bars = fetch_intraday(trade["ce_sec_id"], "NSE_FNO", "OPTIDX", trade["date"])
    nifty_bars = fetch_intraday("13", "IDX_I", "INDEX", trade["date"])

    entry_t = trade["entry_time"]
    exit_t  = trade["exit_time"]

    # slice bars between entry and exit (inclusive)
    ce_slice    = [(t, o, h, l, c) for t, o, h, l, c in bars       if entry_t <= t <= exit_t]
    nifty_slice = [(t, o, h, l, c) for t, o, h, l, c in nifty_bars if entry_t <= t <= exit_t]

    if not ce_slice:
        return {"label": trade["label"], "error": "No CE data in range"}

    sell_px  = trade["sell_price"]
    exit_px  = trade["exit_price"]
    actual_pl = (sell_px - exit_px) * LOT_SIZE

    # CE seller: profit when premium falls → best = min low, worst = max high
    ce_lows  = [x[3] for x in ce_slice]
    ce_highs = [x[2] for x in ce_slice]
    best_ce  = min(ce_lows)
    worst_ce = max(ce_highs)
    best_ce_t  = ce_slice[ce_lows.index(best_ce)][0]
    worst_ce_t = ce_slice[ce_highs.index(worst_ce)][0]
    max_runup_pts = sell_px - best_ce
    max_dd_pts    = max(0.0, worst_ce - sell_px)  # 0 if never went against

    # Duration
    fmt = "%H:%M"
    entry_dt = datetime.datetime.strptime(entry_t, fmt)
    exit_dt  = datetime.datetime.strptime(exit_t,  fmt)
    dur_mins = int((exit_dt - entry_dt).total_seconds() / 60)
    dur_str  = f"{dur_mins//60}h{dur_mins%60:02d}m" if dur_mins >= 60 else f"{dur_mins}m"

    # NIFTY index
    nifty_entry_px = trade.get("nifty_entry")
    nifty_exit_px  = None
    if nifty_slice and nifty_entry_px:
        nf_lows  = [x[3] for x in nifty_slice]
        nf_highs = [x[2] for x in nifty_slice]
        # exit bar close = NIFTY price at exit
        exit_bar = next((x for x in nifty_slice if x[0] == exit_t), None)
        nifty_exit_px = exit_bar[4] if exit_bar else nifty_slice[-1][4]

        if trade["direction"] == "SHORT":
            best_nf  = min(nf_lows)
            worst_nf = max(nf_highs)
            best_nf_t  = nifty_slice[nf_lows.index(best_nf)][0]
            worst_nf_t = nifty_slice[nf_highs.index(worst_nf)][0]
            nf_runup = nifty_entry_px - best_nf
            nf_dd    = max(0.0, worst_nf - nifty_entry_px)
        else:
            best_nf  = max(nf_highs)
            worst_nf = min(nf_lows)
            best_nf_t  = nifty_slice[nf_highs.index(best_nf)][0]
            worst_nf_t = nifty_slice[nf_lows.index(worst_nf)][0]
            nf_runup = best_nf - nifty_entry_px
            nf_dd    = max(0.0, nifty_entry_px - worst_nf)
    else:
        nf_runup = nf_dd = best_nf = worst_nf = None
        best_nf_t = worst_nf_t = "—"

    return {
        "label":         trade["label"],
        "date":          trade["date"],
        "direction":     trade["direction"],
        "entry_t":       entry_t,
        "exit_t":        exit_t,
        "dur_str":       dur_str,
        "sell_px":       sell_px,
        "exit_px":       exit_px,
        "actual_pl":     actual_pl,
        "best_ce":       best_ce,
        "best_ce_t":     best_ce_t,
        "worst_ce":      worst_ce,
        "worst_ce_t":    worst_ce_t,
        "max_runup_pts": max_runup_pts,
        "max_runup_inr": max_runup_pts * LOT_SIZE,
        "max_dd_pts":    max_dd_pts,
        "max_dd_inr":    max_dd_pts * LOT_SIZE,
        "nifty_entry":   nifty_entry_px,
        "nifty_exit":    nifty_exit_px,
        "nf_runup":      nf_runup,
        "nf_dd":         nf_dd,
        "best_nf":       best_nf,
        "best_nf_t":     best_nf_t,
        "worst_nf":      worst_nf,
        "worst_nf_t":    worst_nf_t,
    }


def print_table(results: list):
    SEP = "-" * 155
    HDR = (
        f"{'Label':<22} {'Dir':<6} {'Dur':>6} {'Entry':>6} {'Exit':>6} "
        f"{'NF In':>9} {'NF Out':>9} {'NF Mov':>7} "
        f"{'CE Sell':>7} {'CE Buy':>7} {'P&L Rs':>8} "
        f"{'Runup pts':>10} {'Runup Rs':>9} {'@':>6} "
        f"{'DD pts':>8} {'DD Rs':>7} {'@':>6} "
        f"{'NF Run':>7} {'NF DD':>6}"
    )
    print(SEP)
    print(HDR)
    print(SEP)
    for r in results:
        if "error" in r:
            print(f"{r['label']:<22}  ERROR: {r['error']}")
            continue
        nf_ru  = f"{r['nf_runup']:>+.1f}" if r["nf_runup"]    is not None else "    -"
        nf_dd  = f"{r['nf_dd']:>+.1f}"    if r["nf_dd"]       is not None else "    -"
        nf_in  = f"{r['nifty_entry']:.1f}" if r["nifty_entry"] else "    -"
        nf_out = f"{r['nifty_exit']:.1f}"  if r["nifty_exit"]  else "    -"
        nf_mov = f"{r['nifty_exit'] - r['nifty_entry']:>+.1f}" if (r["nifty_entry"] and r["nifty_exit"]) else "    -"
        print(
            f"{r['label']:<22} {r['direction']:<6} {r['dur_str']:>6} {r['entry_t']:>6} {r['exit_t']:>6} "
            f"{nf_in:>9} {nf_out:>9} {nf_mov:>7} "
            f"{r['sell_px']:>7.2f} {r['exit_px']:>7.2f} {r['actual_pl']:>+8.0f} "
            f"{r['max_runup_pts']:>+10.2f} {r['max_runup_inr']:>+9.0f} {r['best_ce_t']:>6} "
            f"{r['max_dd_pts']:>+8.2f} {r['max_dd_inr']:>+7.0f} {r['worst_ce_t']:>6} "
            f"{nf_ru:>7} {nf_dd:>6}"
        )
    print(SEP)
    valid = [r for r in results if "error" not in r]
    if len(valid) > 1:
        total_pl     = sum(r["actual_pl"]     for r in valid)
        total_ru_inr = sum(r["max_runup_inr"] for r in valid)
        print(f"{'TOTAL':<22} {'':6} {'':6} {'':6} {'':6} {'':9} {'':9} {'':7} "
              f"{'':7} {'':7} {total_pl:>+8.0f} {'':10} {total_ru_inr:>+9.0f}")
        print(SEP)


# ── TRADES LIST — add your trades here ───────────────────────────────────────
# sec_id kaise dhundhein:
#   grep 'NIFTY.*<STRIKE>.*CE.*<EXPIRY>' data/api-scrip-master.csv
#   Column 3 (0-indexed) = sec_id

TRADES = [
    {
        "label":       "Jun17 Short 24100CE",
        "date":        "2026-06-17",
        "direction":   "SHORT",
        "entry_time":  "11:05",
        "exit_time":   "13:35",
        "ce_sec_id":   "56376",   # NIFTY Jun23 24100 CE
        "sell_price":  143.00,    # CE sell @ 11:05 open
        "exit_price":  130.45,    # CE buy  @ 13:35 open
        "nifty_entry": 24073.2,
    },
    {
        "label":       "Jun18 Short 24100CE",
        "date":        "2026-06-18",
        "direction":   "SHORT",
        "entry_time":  "10:05",
        "exit_time":   "12:05",
        "ce_sec_id":   "56376",   # NIFTY Jun23 24100 CE
        "sell_price":  137.95,    # CE sell @ 10:10 open (fill bar)
        "exit_price":  112.60,    # CE buy  @ 12:05 open
        "nifty_entry": 24117.65,
    },
    {
        "label":       "Jun18 Long  24100PE",
        "date":        "2026-06-18",
        "direction":   "LONG",
        "entry_time":  "12:25",
        "exit_time":   "14:10",
        "ce_sec_id":   "56377",   # NIFTY Jun23 24100 PE
        "sell_price":  112.20,    # PE sell @ 12:30 open (fill bar)
        "exit_price":  121.45,    # PE buy  @ 14:10 open
        "nifty_entry": 24100.95,
    },
    # ── Add more trades below ──
    # {
    #     "label":       "Jun18 Short 24150CE",
    #     "date":        "2026-06-18",
    #     "direction":   "SHORT",
    #     "entry_time":  "10:10",
    #     "exit_time":   "12:05",
    #     "ce_sec_id":   "XXXXX",
    #     "sell_price":  XXX.XX,
    #     "exit_price":  XXX.XX,
    #     "nifty_entry": XXXXX.X,
    # },
]


if __name__ == "__main__":
    import time
    results = []
    for i, t in enumerate(TRADES):
        print(f"Fetching {t['label']} ({i+1}/{len(TRADES)})...", end=" ", flush=True)
        try:
            r = analyse_trade(t)
            results.append(r)
            print("done")
        except Exception as e:
            results.append({"label": t["label"], "error": str(e)})
            print(f"ERROR: {e}")
        if i < len(TRADES) - 1:
            time.sleep(2)  # avoid DH-904 rate limit

    print()
    print_table(results)
