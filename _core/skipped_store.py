"""Skipped / RMS-blocked entry-signal recorder.

Jab bhi `execution_gateway.execute_signal()` ek entry ko RMS-gate se BLOCK karta
hai (profit-target / loss-cap / max-trades / capital / max-premium / concentration
/ liquidity), wo signal kabhi order_store me nahi jaata — na uska koi price, na
P&L. Pehle wo sirf log me ek `[ENTRY SKIP]` line chhodta tha (har cycle repeat,
no contract, no price) -> "block na hote to kya hota" jaisa koi what-if data se
prove NAHI ho sakta tha (blocked entries ka outcome kahin store hi nahi hota).

Ye store us "jo trade ham NAHI le paaye" ka structured record rakhta hai --
**entry-intent + us second ka premium (est_price)** -- taaki baad me OFFLINE
replay (`_ops/skipped_replay.py`) real expired-option premium + strategy ke apne
exit-rule se uska hypothetical entry->exit->P&L nikaal sake.

Design:
- Separate DB `data/skipped_signals.db` -- order_store ka `orders` table bilkul
  UNTOUCHED (koi lock-contention / P&L-corruption risk nahi).
- Recording FAIL-SAFE hai: koi bhi error swallow + loud log, caller ko KABHI
  raise nahi (order-decision recording ki wajah se kabhi na ruke).
- Dedup: DB-level `UNIQUE(date,strategy,symbol,side,trad_sym)` + INSERT OR IGNORE
  -> log ka har-30s repeat spam ek hi row banata hai (v1 me same-contract-same-day
  dobara-signal under-count acceptable -- hum "trade liya hi nahi" ginte hain, tick
  count nahi).

Har naye strategy ke liye ye APNE-AAP chalta hai: execute_signal ek hi choke-point
hai jahan se har strategy ki entry block hoti hai (Rule 6B single-gate).
"""
import os
import sqlite3
import datetime

_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "skipped_signals.db",
)

# reason-string -> category (single source; JS/replay dono yahi padhte hain)
_REASON_MAP = [
    ("smart_size_skip", "size_skip"),   # cash/margin short — even 1 lot fit nahi
    ("smart_size_down", "size_down"),   # lots reduce kiye taaki fit ho jaye
    ("profit target", "profit_target"),
    ("daily loss", "loss_cap"),
    ("loss cap", "loss_cap"),
    ("maxloss", "loss_cap"),
    ("max trade", "max_trades"),
    ("trades/day", "max_trades"),
    ("trades per day", "max_trades"),
    ("concentration", "concentration"),
    ("premium", "max_premium"),      # "premium X > max cap"
    ("liquid", "liquidity"),
    ("funds", "funds"),
    ("cash_margin", "capital"),   # CASH_MARGIN_SHORT — option-sell needs >=50% cash
    ("cash-capacity", "capital"),
    ("capital", "capital"),
    ("market_closed", "market_closed"),
    ("market band", "market_closed"),
]


def categorize(reason):
    r = (reason or "").lower()
    for sub, cat in _REASON_MAP:
        if sub in r:
            return cat
    return "other"


def _conn():
    c = sqlite3.connect(_DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    os.makedirs(os.path.dirname(_DB), exist_ok=True)
    c = _conn()
    c.execute(
        """CREATE TABLE IF NOT EXISTS skipped_signals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, date TEXT, strategy TEXT, mode TEXT,
            symbol TEXT, side TEXT, opt_type TEXT,
            trad_sym TEXT, sec_id TEXT,
            intended_lots INTEGER, intended_qty INTEGER,
            entry_premium REAL,
            block_reason TEXT, block_detail TEXT,
            signal_id TEXT UNIQUE)"""
    )
    c.commit()
    c.close()


def _ist_now():
    # IST = UTC + 5:30 (naive, dashboard-wide convention -- no tz object)
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def _opt_type(trad_sym):
    """Sirf -CE / -PE suffix se (universally reliable -- expiry/strike parse NAHI,
    TRAP #13/#140 se bachte hue). Na mile to None."""
    s = (trad_sym or "").upper()
    if s.endswith("CE"):
        return "CE"
    if s.endswith("PE"):
        return "PE"
    return None


def record_skip(*, strategy, symbol, side, trad_sym=None, sec_id=None,
                intended_lots=None, lot_size=None, entry_premium=None,
                block_reason="", mode="paper"):
    """Ek blocked entry-signal record karo. FAIL-SAFE: koi bhi error swallow +
    loud log, caller ko kabhi raise nahi. Returns True/False (side-effect only)."""
    try:
        now = _ist_now()
        ts = now.strftime("%Y-%m-%d %H:%M:%S")
        date = now.strftime("%Y-%m-%d")
        lots = int(intended_lots) if intended_lots else None
        qty = (lots * int(lot_size)) if (lots and lot_size) else None
        # dedup key -- ek signal (date,strategy,symbol,side,contract) = ek row
        sig = "%s|%s|%s|%s|%s" % (date, strategy, symbol, side, trad_sym or "")
        init_db()
        c = _conn()
        c.execute(
            """INSERT OR IGNORE INTO skipped_signals
               (ts,date,strategy,mode,symbol,side,opt_type,trad_sym,sec_id,
                intended_lots,intended_qty,entry_premium,
                block_reason,block_detail,signal_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts, date, str(strategy), str(mode or "paper"), str(symbol),
             str(side), _opt_type(trad_sym), trad_sym,
             (str(sec_id) if sec_id is not None else None),
             lots, qty,
             (float(entry_premium) if entry_premium else None),
             categorize(block_reason), (block_reason or "")[:300], sig),
        )
        c.commit()
        c.close()
        return True
    except Exception as e:
        try:
            print("[skipped_store] record FAILED (non-fatal): %s" % e, flush=True)
        except Exception:
            pass
        return False


def query(date_from=None, date_to=None, strategy=None):
    """Read-only fetch for replay / dashboard. Returns list of dicts."""
    try:
        init_db()
        c = _conn()
        q = "SELECT * FROM skipped_signals WHERE 1=1"
        a = []
        if date_from:
            q += " AND date>=?"; a.append(date_from)
        if date_to:
            q += " AND date<=?"; a.append(date_to)
        if strategy:
            q += " AND strategy=?"; a.append(strategy)
        rows = [dict(r) for r in c.execute(q + " ORDER BY ts", a)]
        c.close()
        return rows
    except Exception as e:
        try:
            print("[skipped_store] query FAILED: %s" % e, flush=True)
        except Exception:
            pass
        return []


def daily_counts(date_from=None, date_to=None):
    """Per-day per-strategy per-reason skip counts (quick sanity / UI)."""
    out = {}
    for r in query(date_from, date_to):
        k = (r["date"], r["strategy"], r["block_reason"])
        out[k] = out.get(k, 0) + 1
    return out
