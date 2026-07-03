#!/usr/bin/env python3
r"""
fno_universe.py — the NSE F&O stock universe (~200 liquid names), derived
straight from the Dhan scrip master already on disk.

Why this list = "top ~200 liquid": SEBI only admits a stock to F&O once it
clears liquidity thresholds (median quarter-sigma order size, market-wide
position limit, average daily delivery value). So "every stock that has stock
FUTURES/OPTIONS in the scrip master" IS the objective liquid-tradable set —
no external ranking or guessing. It's also exactly the set the options
strategies can trade (they buy ATM options on the underlying).

Extraction: FUTSTK rows' SEM_TRADING_SYMBOL is "RELIANCE-Jul2026-FUT" -> the
part before the first '-' is the clean NSE equity symbol, which matches the
NSE_EQ rows' SEM_TRADING_SYMBOL 1:1. We keep only symbols that also resolve to
a real NSE_EQ security id (so download_equity_history.py can fetch them).

Output: data/fno_universe.json = {"generated": ISO, "count": N,
"symbols": [...], "sec_ids": {sym: sec_id}}  — the canonical list every other
tool reads (downloader, universe scanner, backtest).

Usage:
  python fno_universe.py            # rebuild the list, print summary, write JSON
  python fno_universe.py --print    # just print the current symbols, no rewrite
"""
import csv
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# strips the "-Jul2026-FUT" tail from a FUTSTK trading symbol, leaving the full
# underlying INCLUDING internal hyphens (BAJAJ-AUTO, NAM-INDIA, M&M)
_FUT_SUFFIX = re.compile(r"-[A-Za-z]{3}\d{4}-FUT$")
# NSE test scrips (011NSETEST, ...) carry real FUTSTK+EQ rows but aren't stocks
_TEST_SYM = re.compile(r"NSETEST|^\d+[A-Z]*TEST", re.I)

BASE = Path(__file__).resolve().parent
SCRIP_MASTER = BASE / "data" / "api-scrip-master.csv"
OUT_FILE = BASE / "data" / "fno_universe.json"


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def extract_fno_symbols():
    """Return (sorted_symbols, {sym: nse_eq_sec_id}) from the scrip master.

    A symbol makes the list only if it has stock F&O (FUTSTK) AND a resolvable
    NSE_EQ equity sec_id — both read from the same scrip master, so they can't
    silently drift apart."""
    if not SCRIP_MASTER.exists():
        raise FileNotFoundError(f"scrip master missing: {SCRIP_MASTER}")

    fno_syms = set()
    eq_secid = {}
    with open(SCRIP_MASTER, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            inst = (row.get("SEM_INSTRUMENT_NAME") or "").strip()
            trad = (row.get("SEM_TRADING_SYMBOL") or "").strip()
            if inst == "FUTSTK" and trad:
                # "RELIANCE-Jul2026-FUT" -> "RELIANCE";
                # "BAJAJ-AUTO-Jul2026-FUT" -> "BAJAJ-AUTO" (keeps internal hyphen)
                und = _FUT_SUFFIX.sub("", trad).strip()
                if und and not _TEST_SYM.search(und):
                    fno_syms.add(und)
            elif (row.get("SEM_EXM_EXCH_ID") == "NSE"
                  and row.get("SEM_SEGMENT") == "E"
                  and row.get("SEM_SERIES") == "EQ"):
                sid = (row.get("SEM_SMST_SECURITY_ID") or "").strip()
                if trad and sid:
                    eq_secid[trad] = sid

    # keep only F&O names that also resolve to a tradable NSE_EQ sec_id
    resolved, unresolved = {}, []
    for s in sorted(fno_syms):
        if s in eq_secid:
            resolved[s] = eq_secid[s]
        else:
            unresolved.append(s)
    return sorted(resolved.keys()), resolved, unresolved


def build(write=True):
    symbols, sec_ids, unresolved = extract_fno_symbols()
    if write:
        OUT_FILE.write_text(json.dumps({
            "generated": _ist_now().strftime("%Y-%m-%d %H:%M:%S IST"),
            "count": len(symbols),
            "symbols": symbols,
            "sec_ids": sec_ids,
        }, indent=2))
    return symbols, sec_ids, unresolved


def get_fno_symbols():
    """Cached list for other modules; rebuilds from scrip master if JSON absent."""
    if OUT_FILE.exists():
        try:
            return json.loads(OUT_FILE.read_text()).get("symbols", [])
        except Exception:
            pass
    return build(write=False)[0]


def main():
    if "--print" in sys.argv:
        syms = get_fno_symbols()
        print(f"{len(syms)} F&O symbols:")
        print(",".join(syms))
        return
    symbols, sec_ids, unresolved = build(write=True)
    print(f"F&O universe rebuilt: {len(symbols)} tradable stocks -> {OUT_FILE.name}")
    print(f"  (all have both stock-futures AND an NSE_EQ sec_id)")
    if unresolved:
        print(f"  skipped {len(unresolved)} F&O names with no NSE_EQ match: "
              f"{','.join(unresolved[:15])}{'...' if len(unresolved) > 15 else ''}")
    print(f"  sample: {','.join(symbols[:12])} ...")


if __name__ == "__main__":
    main()
