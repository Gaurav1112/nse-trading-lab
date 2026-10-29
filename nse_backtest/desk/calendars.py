"""Exchange trading calendars: holidays and early closes per market, for covered years only.

Data verified 2026-10-29 against primary sources:
- NSE equities (Capital Market segment) trading holidays 2026:
  https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf
  https://www.nseindia.com/resources/exchange-communication-holidays (trading table, not the clearing table)
  https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf
  2026-01-15 (Municipal Corporation Election - Maharashtra) was added after CMTR71775 and is on the live NSE page.
  NSE 2027 is not published yet, so NSE 2027 is uncovered.
- NYSE holidays and 1:00 pm ET early closes 2026-2027: https://www.nyse.com/markets/hours-calendars

Muhurat trading (NSE, Sunday 2026-11-08, 14:20-15:30 IST) is NOT supported: that Sunday is treated as a
non-trading day like any other weekend.

Outside the covered years a market is assumed to trade on every weekday. That assumption is uncertain, so
callers must check `covered()` and turn PASS into WARN with "holiday calendar not available for {year}".
Every walk over dates is bounded; nothing here can loop forever.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

HOLIDAYS: dict[str, dict[date, str]] = {
    "NSE": {
        date(2026, 1, 15): "Municipal Corporation Election - Maharashtra",
        date(2026, 1, 26): "Republic Day",
        date(2026, 3, 3): "Holi",
        date(2026, 3, 26): "Shri Ram Navami",
        date(2026, 3, 31): "Shri Mahavir Jayanti",
        date(2026, 4, 3): "Good Friday",
        date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
        date(2026, 5, 1): "Maharashtra Day",
        date(2026, 5, 28): "Bakri Id",
        date(2026, 6, 26): "Muharram",
        date(2026, 9, 14): "Ganesh Chaturthi",
        date(2026, 10, 2): "Mahatma Gandhi Jayanti",
        date(2026, 10, 20): "Dussehra",
        date(2026, 11, 10): "Diwali-Balipratipada",
        date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
        date(2026, 12, 25): "Christmas",
    },
    "US": {
        date(2026, 1, 1): "New Year's Day",
        date(2026, 1, 19): "Martin Luther King, Jr. Day",
        date(2026, 2, 16): "Washington's Birthday",
        date(2026, 4, 3): "Good Friday",
        date(2026, 5, 25): "Memorial Day",
        date(2026, 6, 19): "Juneteenth National Independence Day",
        date(2026, 7, 3): "Independence Day (observed)",
        date(2026, 9, 7): "Labor Day",
        date(2026, 11, 26): "Thanksgiving Day",
        date(2026, 12, 25): "Christmas Day",
        date(2027, 1, 1): "New Year's Day",
        date(2027, 1, 18): "Martin Luther King, Jr. Day",
        date(2027, 2, 15): "Washington's Birthday",
        date(2027, 3, 26): "Good Friday",
        date(2027, 5, 31): "Memorial Day",
        date(2027, 6, 18): "Juneteenth National Independence Day (observed)",
        date(2027, 7, 5): "Independence Day (observed)",
        date(2027, 9, 6): "Labor Day",
        date(2027, 11, 25): "Thanksgiving Day",
        date(2027, 12, 24): "Christmas Day (observed)",
    },
}

EARLY_CLOSES: dict[str, dict[date, time]] = {
    "NSE": {},
    "US": {
        date(2026, 11, 27): time(13, 0),  # Day after Thanksgiving
        date(2026, 12, 24): time(13, 0),  # Christmas Eve
        date(2027, 11, 26): time(13, 0),  # Day after Thanksgiving
    },
}

COVERED_YEARS: dict[str, frozenset[int]] = {
    "NSE": frozenset({2026}),
    "US": frozenset({2026, 2027}),
}

_MAX_WALK = 30  # no market closes for 30 days in a row; hitting this means the calendar data is broken
_FLAT_BEFORE_CLOSE = timedelta(minutes=15)


def _day(d) -> date:
    return d.date() if isinstance(d, datetime) else d


def _code(profile) -> str:
    return str(getattr(profile, "code", "")).upper()


def covered(profile, d: date) -> bool:
    """True when the holiday calendar for `d`'s year is known for this market."""
    return _day(d).year in COVERED_YEARS.get(_code(profile), frozenset())


def first_uncovered_year(profile, a: date, b: date) -> int | None:
    """The first year in [a.year, b.year] without a holiday calendar, else None. Bounded by the covered set."""
    a, b = _day(a), _day(b)
    if b < a:
        a, b = b, a
    years = COVERED_YEARS.get(_code(profile), frozenset())
    y = a.year
    for _ in range(len(years) + 1):
        if y > b.year:
            return None
        if y not in years:
            return y
        y += 1
    return y if y <= b.year else None


def holiday_name(profile, d: date) -> str | None:
    return HOLIDAYS.get(_code(profile), {}).get(_day(d))


def is_trading_day(profile, d: date) -> bool:
    """Weekday and not a listed holiday. Uncovered years: weekday only (callers WARN)."""
    d = _day(d)
    return d.weekday() < 5 and holiday_name(profile, d) is None


def is_early_close(profile, d: date) -> bool:
    return _day(d) in EARLY_CLOSES.get(_code(profile), {})


def session_close_for(profile, d: date) -> time:
    return EARLY_CLOSES.get(_code(profile), {}).get(_day(d), profile.session_close)


def flat_by_for(profile, d: date) -> time:
    """profile.flat_by, pulled to 15 minutes before an early close."""
    d = _day(d)
    if not is_early_close(profile, d):
        return profile.flat_by
    close = session_close_for(profile, d)
    early = (datetime.combine(d, close) - _FLAT_BEFORE_CLOSE).time()
    return min(profile.flat_by, early)


def _walk(profile, d: date, step: int) -> date:
    cur = _day(d)
    for _ in range(_MAX_WALK):
        cur = cur + timedelta(days=step)
        if is_trading_day(profile, cur):
            return cur
    raise RuntimeError(f"no {_code(profile)} trading day within {_MAX_WALK} days of {_day(d)}: calendar data is broken")


def previous_trading_day(profile, d: date) -> date:
    """The last trading day strictly before `d`."""
    return _walk(profile, d, -1)


def next_trading_day(profile, d: date) -> date:
    """The first trading day strictly after `d`."""
    return _walk(profile, d, 1)


def _weekdays_before(d: date) -> int:
    """Weekdays in [0001-01-01, d). 0001-01-01 is a Monday."""
    full, rem = divmod(d.toordinal() - 1, 7)
    return full * 5 + min(rem, 5)


def trading_days_between(profile, a: date, b: date) -> int:
    """Trading days strictly after `a` and strictly before `b`; 0 when b <= a + 1 day.

    Counted in closed form (weekdays minus listed weekday holidays), so long spans need no date walk.
    """
    a, b = _day(a), _day(b)
    lo = a + timedelta(days=1)
    if b <= lo:
        return 0
    n = _weekdays_before(b) - _weekdays_before(lo)
    n -= sum(1 for h in HOLIDAYS.get(_code(profile), {}) if lo <= h < b and h.weekday() < 5)
    return n
