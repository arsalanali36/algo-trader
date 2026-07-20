"""
market_calendar.py — NSE trading-day / market-open SINGLE SOURCE OF TRUTH.

Kyun: har strategy apne loop me alag-alag "market khula hai kya" check karti thi —
kuch sirf time-of-day dekhti thi (weekday/holiday nahi). VRP condor isi wajah se
**Saturday** ko roll kar gaya (entry+exit at stale weekend prices → phantom P&L,
BS toota). Ab weekend + holiday dono ek jagah; koi bhi trader/gateway yahi poochta.

Holiday list = NSE/BSE trading holidays (CODE7 `MARKET_HOLIDAYS` se ported).
**Har saal December me agle saal ki NSE holiday list yahan add karo** (warna nayi
holiday ek normal trading-day maani jayegi → us din entry ho jayegi).

Import (flat, _paths bootstrap ke baad):  `import market_calendar as mc`
    mc.is_trading_day(d)            # weekday AND not a holiday
    mc.is_market_open(now, open_hm, close_hm)   # trading-day AND time in window
    mc.is_holiday(d)               # -> holiday name or None
"""

from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

# NSE trading holidays (weekday holidays; weekends handled separately by weekday()).
# date "YYYY-MM-DD" -> reason.  UPDATE ANNUALLY.
MARKET_HOLIDAYS = {
    # 2025
    "2025-02-26": "Mahashivratri", "2025-03-14": "Holi",
    "2025-03-31": "Id-Ul-Fitr (Ramadan Eid)", "2025-04-10": "Shri Ram Navami",
    "2025-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2025-04-18": "Good Friday",
    "2025-05-01": "Maharashtra Day", "2025-08-15": "Independence Day",
    "2025-08-27": "Ganesh Chaturthi", "2025-10-02": "Mahatma Gandhi Jayanti / Dussehra",
    "2025-10-20": "Diwali-Laxmi Puja", "2025-10-21": "Diwali-Balipratipada",
    "2025-11-05": "Prakash Gurpurb Sri Guru Nanak Dev", "2025-12-25": "Christmas",
    # 2026
    "2026-01-15": "Municipal Corp. Election – Maharashtra", "2026-01-26": "Republic Day",
    "2026-03-03": "Holi", "2026-03-26": "Shri Ram Navami",
    "2026-03-31": "Shri Mahavir Jayanti", "2026-04-03": "Good Friday",
    "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id", "2026-06-26": "Muharram",
    "2026-09-14": "Ganesh Chaturthi", "2026-10-02": "Mahatma Gandhi Jayanti",
    "2026-10-20": "Dussehra", "2026-11-10": "Diwali-Balipratipada",
    "2026-11-24": "Prakash Gurpurb Sri Guru Nanak Dev", "2026-12-25": "Christmas",
}


def _to_str(d):
    """Accept a date / datetime / 'YYYY-MM-DD' str → 'YYYY-MM-DD'."""
    if isinstance(d, str):
        return d[:10]
    if isinstance(d, (_date, _dt)):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


def _to_date(d):
    if isinstance(d, _dt):
        return d.date()
    if isinstance(d, _date):
        return d
    return _date.fromisoformat(_to_str(d))


def ist_now():
    """Naive IST datetime (UTC+5:30). Same convention traders use."""
    return _dt.now(_tz.utc).replace(tzinfo=None) + _td(hours=5, minutes=30)


def is_holiday(d):
    """Holiday name for that date, else None. (Does NOT count weekends — use
    is_trading_day for the full 'can we trade today' answer.)"""
    return MARKET_HOLIDAYS.get(_to_str(d))


def is_trading_day(d=None):
    """True only on a normal NSE trading day: Mon–Fri AND not a listed holiday.
    d = date/datetime/str; default = today (IST)."""
    dd = _to_date(d) if d is not None else ist_now().date()
    if dd.weekday() >= 5:            # Sat/Sun
        return False
    return _to_str(dd) not in MARKET_HOLIDAYS


def is_market_open(now=None, open_hm=(9, 15), close_hm=(15, 30)):
    """True when `now` (naive IST datetime; default = ist_now()) is a trading day
    AND its time is within [open_hm, close_hm). Callers pass their own window so
    each strategy keeps its entry/exit band but shares the trading-day gate."""
    n = now or ist_now()
    if not is_trading_day(n.date()):
        return False
    t = (n.hour, n.minute)
    return tuple(open_hm) <= t < tuple(close_hm)
