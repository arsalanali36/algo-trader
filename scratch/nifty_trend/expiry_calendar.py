"""
expiry_calendar.py — NIFTY / BANKNIFTY weekly + monthly EXPIRY-DAY schedule (one-time research).

WHY THIS FILE EXISTS
--------------------
SEBI/NSE have changed the index-options expiry DAY over the years. A single hardcoded weekday
(`bs_option._next_weekly_expiry` used to assume Thursday) mis-prices time-to-expiry -> premium
+ theta across a multi-year backtest (KNOWN_ISSUES.md #1). This file is the SINGLE PLACE that
holds the historical schedule, so the research is done ONCE and never repeated.

STATUS: ✅ FILLED from official NSE/SEBI circulars (2026-07-10, home machine). No guessing —
user rule: "jo official document me day/date mile, wahi expiry lena."

─────────────────────────────────────────────────────────────────────────────
VERIFIED HISTORY (sources at bottom)
─────────────────────────────────────────────────────────────────────────────
NIFTY weekly expiry weekday:
  • THURSDAY  — from inception through 2025-08-28 (last Thursday weekly expiry).
                (NIFTY weekly options launched 11-Feb-2019; before that only monthly existed —
                 see WEEKLY_LAUNCH note below, it's a DATA caveat, not a weekday change.)
  • TUESDAY   — effective 2025-09-01 (first Tuesday weekly expiry = 2025-09-02). Current.
                SEBI circular SEBI/HO/MRD/MRD-TPD-1/P/CIR/2025/76 (26-May-2025) limited every
                exchange's equity-derivatives expiry to Tuesday OR Thursday; NSE chose Tuesday
                (NSE circular 111/2025, 25-Jun-2025), BSE chose Thursday.

NIFTY monthly expiry weekday:
  • last THURSDAY of the month — through Aug 2025.
  • last TUESDAY  of the month — effective 2025-09-01 (same SEBI/NSE change above).

⚠️ The "Monday expiry" that was ANNOUNCED (NSE circular 04-Mar-2025, effective 05-Apr-2025)
   was PUT ON HOLD on 27-Mar-2025 (SEBI consultation) and NEVER took effect. So there is NO
   Monday row — Thursday ran uninterrupted until the Tuesday switch on 2025-09-01.

BANKNIFTY: weekly options were DISCONTINUED. SEBI (01-Oct-2024) limited weekly index options to
   ONE benchmark per exchange; NSE kept only NIFTY weekly. Last BANKNIFTY weekly expiry =
   13-Nov-2024; no BANKNIFTY weekly from 20-Nov-2024. BANKNIFTY monthly follows the same
   Thu->Tue(last) switch on 2025-09-01. (Backtests are NIFTY-only; BNF is forward-collector-only.)

HOW THE LOOKUP WORKS
--------------------
Each list is (effective_from ISO date, weekday_int, "source/note"), sorted ascending.
`weekly_expiry_weekday(d)` / `monthly_expiry_weekday(d)` return the weekday whose rule was in
force on date `d`. weekday_int: Mon=0 Tue=1 Wed=2 Thu=3 Fri=4.

WIRED INTO: `bs_option._next_weekly_expiry(ts)` -> calls `weekly_expiry_weekday(ts.date())`
instead of a hardcoded weekday. Re-run `build_final.py` after any change here.
"""

import datetime as _dt

MON, TUE, WED, THU, FRI = 0, 1, 2, 3, 4

# NIFTY weekly options launched here. Before this, weekly ATM straddles/verticals did not exist
# as tradable instruments (only monthly) — a DATA caveat for any pre-2019 backtest segment,
# separate from the weekday schedule. Flagged so the strategy layer can decide to gate pre-2019.
NIFTY_WEEKLY_LAUNCH = "2019-02-11"

# ─────────────────────────────────────────────────────────────────────────────
# NIFTY WEEKLY expiry weekday over time. Sorted ascending by effective_from.
# ─────────────────────────────────────────────────────────────────────────────
NIFTY_WEEKLY_EXPIRY = [
    ("2018-01-01", THU, "inception default — NIFTY weekly = Thursday (weeklies live from 2019-02-11)"),
    ("2025-09-01", TUE, "SEBI cir 2025/76 (26-May-2025) + NSE cir 111/2025 (25-Jun-2025); "
                        "last Thu weekly 28-Aug-2025, first Tue weekly 02-Sep-2025"),
]

# NIFTY MONTHLY expiry weekday (last <weekday> of the month). Same SEBI/NSE change.
NIFTY_MONTHLY_EXPIRY = [
    ("2018-01-01", THU, "inception default — monthly = last Thursday"),
    ("2025-09-01", TUE, "SEBI cir 2025/76 + NSE cir 111/2025 — monthly = last Tuesday from Sep-2025"),
]

# BANKNIFTY: weeklies discontinued (SEBI 01-Oct-2024, single weekly/exchange -> NIFTY only).
BANKNIFTY_WEEKLY_DISCONTINUED_FROM = "2024-11-20"   # last BNF weekly expiry was 2024-11-13
BANKNIFTY_WEEKLY_EXPIRY = [
    ("2018-01-01", THU, "inception default - BANKNIFTY weekly = Thursday"),
    ("2023-09-04", WED, "NSE cir 119/2023 (12-Jul-2023): weekly Thu->Wed effective trade date "
                        "04-Sep-2023; first Wednesday weekly 06-Sep-2023; monthly unchanged"),
]
BANKNIFTY_MONTHLY_EXPIRY = [
    ("2018-01-01", THU, "inception default — monthly = last Thursday"),
    ("2025-09-01", TUE, "SEBI cir 2025/76 + NSE cir 111/2025 — monthly = last Tuesday from Sep-2025"),
]


def _weekday_in_force(schedule, d):
    """schedule = sorted list of (iso_date, weekday, note). Return the weekday whose rule was in
    force on date `d` (a datetime.date)."""
    wd = schedule[0][1]
    for iso, weekday, _note in schedule:
        if _dt.date.fromisoformat(iso) <= d:
            wd = weekday
        else:
            break
    return wd


def weekly_expiry_weekday(d):
    """NIFTY weekly-expiry weekday in force on date `d` (datetime.date). Mon=0..Fri=4."""
    return _weekday_in_force(NIFTY_WEEKLY_EXPIRY, d)


def monthly_expiry_weekday(d):
    """NIFTY monthly-expiry weekday in force on date `d`. Mon=0..Fri=4."""
    return _weekday_in_force(NIFTY_MONTHLY_EXPIRY, d)


def banknifty_monthly_expiry_weekday(d):
    return _weekday_in_force(BANKNIFTY_MONTHLY_EXPIRY, d)


def banknifty_weekly_expiry_weekday(d):
    """BNF weekly weekday in force on `d`; None once weeklies were discontinued (2024-11-20+)."""
    if d >= _dt.date.fromisoformat(BANKNIFTY_WEEKLY_DISCONTINUED_FROM):
        return None
    return _weekday_in_force(BANKNIFTY_WEEKLY_EXPIRY, d)


def _last_weekday_of_month(year, month, weekday):
    import calendar as _cal
    last = _dt.date(year, month, _cal.monthrange(year, month)[1])
    off = (last.weekday() - weekday) % 7
    return last - _dt.timedelta(days=off)


def banknifty_next_expiry(d):
    """Nearest BANKNIFTY option expiry DATE on/after `d`: weekly (Thu/Wed schedule) while
    weeklies existed; the monthly last-<weekday> after discontinuation (2024-11-20+)."""
    wd = banknifty_weekly_expiry_weekday(d)
    if wd is not None:
        ahead = (wd - d.weekday()) % 7
        return d + _dt.timedelta(days=ahead)
    mwd = banknifty_monthly_expiry_weekday(d)
    exp = _last_weekday_of_month(d.year, d.month, mwd)
    if exp < d:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        exp = _last_weekday_of_month(y, m, mwd)
    return exp


def is_banknifty_expiry_day(d):
    return banknifty_next_expiry(d) == d


def is_fully_filled():
    """True once the schedule carries the real Thursday->Tuesday transition (not just the skeleton
    default row). Callers/tests can warn 'expiry schedule still skeleton' when this is False."""
    return len(NIFTY_WEEKLY_EXPIRY) >= 2


# ─────────────────────────────────────────────────────────────────────────────
# SOURCES (official circulars + primary-reporting confirmations, pulled 2026-07-10)
#   • SEBI/HO/MRD/MRD-TPD-1/P/CIR/2025/76 (26-May-2025) — expiry limited to Tue or Thu.
#   • NSE circular 111/2025 (25-Jun-2025) — NIFTY weekly -> Tuesday, effective 01-Sep-2025.
#   • NSE circular (04-Mar-2025) Monday move + (27-Mar-2025) WITHHELD — never effective.
#   • SEBI (01-Oct-2024) single-weekly-per-exchange -> BANKNIFTY/FINNIFTY/MIDCAP weeklies
#     discontinued; last BANKNIFTY weekly 13-Nov-2024, none from 20-Nov-2024.
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    checks = ("2018-06-01", "2019-06-06", "2024-06-06", "2025-08-28", "2025-09-02", "2026-07-09")
    names = ("Mon", "Tue", "Wed", "Thu", "Fri")
    for iso in checks:
        d = _dt.date.fromisoformat(iso)
        w = weekly_expiry_weekday(d)
        m = monthly_expiry_weekday(d)
        print(f"{iso}: weekly={names[w]}({w})  monthly={names[m]}({m})")
    print("fully_filled:", is_fully_filled())
