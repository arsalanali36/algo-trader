"""
expiry_calendar.py — NIFTY / BANKNIFTY weekly + monthly EXPIRY-DAY schedule (one-time research).

WHY THIS FILE EXISTS
--------------------
SEBI/NSE have changed the index-options expiry DAY several times. A single hardcoded weekday
(`bs_option._next_weekly_expiry` currently assumes Thursday) mis-prices time-to-expiry → premium
+ theta across a multi-year backtest (KNOWN_ISSUES.md #1). This file is the SINGLE PLACE that
holds the historical schedule, so the research is done ONCE and never repeated.

⚠️ STATUS: SKELETON — the exact transition DATES must be filled from OFFICIAL NSE/SEBI circulars.
Do NOT trust premium/theta near expiry until the TODO entries below are verified + filled.
User rule (2026-07-10): "jo official document me day/date mile, wahi expiry lena" — no guessing.

HOW TO FILL (home machine)
--------------------------
1. Pull each NSE/SEBI circular that changed the expiry weekday (weekly AND monthly, NIFTY AND
   BANKNIFTY — BANKNIFTY weeklies were discontinued at some point too; note that).
2. For each change add a tuple: (effective_from "YYYY-MM-DD", weekday_int, "circular no / note").
   weekday_int: Mon=0 Tue=1 Wed=2 Thu=3 Fri=4.
3. Keep the list sorted ascending by date. `weekly_expiry_weekday(d)` returns the rule in force on d.
4. Then wire it into bs_option._next_weekly_expiry(ts) to use weekly_expiry_weekday(ts.date())
   instead of the hardcoded weekday=3, and re-run build_final.py.

Anchors I'm reasonably sure of (STILL VERIFY): NIFTY weekly was THURSDAY for years; CURRENT
(2026) NIFTY weekly = TUESDAY (user-confirmed). The exact Thursday→Tuesday transition date(s) —
and any Wed/other interim — are the TODOs.
"""

import datetime as _dt

MON, TUE, WED, THU, FRI = 0, 1, 2, 3, 4

# ─────────────────────────────────────────────────────────────────────────────
# NIFTY WEEKLY expiry weekday over time. Sorted ascending by effective_from.
# Each: (effective_from ISO date, weekday_int, "source / note")
# ⚠️ DATES BELOW EXCEPT THE FIRST ARE PLACEHOLDERS — fill/verify from official circulars.
# ─────────────────────────────────────────────────────────────────────────────
NIFTY_WEEKLY_EXPIRY = [
    ("2018-01-01", THU, "historical default — NIFTY weekly = Thursday (VERIFY start)"),
    # ("YYYY-MM-DD", ???, "NSE/SEBI circular no — Thursday -> ? change"),   # TODO fill
    # ("YYYY-MM-DD", TUE, "NSE circular — NIFTY weekly -> Tuesday (current). VERIFY exact date"),  # TODO fill
    # As of 2026 the CURRENT weekday is TUESDAY (user-confirmed) — add the row with its real
    # effective date once the circular is pulled. Until then weekly_expiry_weekday() returns
    # Thursday for all dates, which is WRONG for the recent window (documented, deliberate).
]

# NIFTY MONTHLY expiry weekday (usually last <weekday> of the month). Same drill.
NIFTY_MONTHLY_EXPIRY = [
    ("2018-01-01", THU, "historical default — monthly = last Thursday (VERIFY)"),
    # ("YYYY-MM-DD", TUE, "circular — monthly -> last Tuesday (VERIFY)"),   # TODO fill
]

# BANKNIFTY: weeklies were DISCONTINUED (SEBI single-weekly-per-exchange). Record when + the
# monthly weekday history here when filling. Backtests are NIFTY-only for now.
BANKNIFTY_WEEKLY_DISCONTINUED_FROM = None   # TODO: "YYYY-MM-DD" from circular
BANKNIFTY_MONTHLY_EXPIRY = [
    ("2018-01-01", THU, "historical default (VERIFY)"),
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
    return _weekday_in_force(NIFTY_MONTHLY_EXPIRY, d)


def is_fully_filled():
    """True once the schedule has at least the Thursday→Tuesday transition entered (a cheap guard
    so callers/tests can warn 'expiry schedule still skeleton'). Update the threshold when filling."""
    return len(NIFTY_WEEKLY_EXPIRY) >= 2


if __name__ == "__main__":
    for iso in ("2018-06-01", "2024-06-01", "2026-07-09"):
        d = _dt.date.fromisoformat(iso)
        print(iso, "-> weekly expiry weekday =", weekly_expiry_weekday(d),
              "(0=Mon..4=Fri)", "" if is_fully_filled() else "  ⚠️ SKELETON — fill from circulars")
