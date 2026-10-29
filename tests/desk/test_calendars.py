from datetime import date, datetime, time

import pytest

from nse_backtest.desk import calendars as cal
from nse_backtest.desk.calendars import (
    COVERED_YEARS, EARLY_CLOSES, HOLIDAYS, covered, flat_by_for, holiday_name, is_early_close, is_trading_day,
    next_trading_day, previous_trading_day, session_close_for, trading_days_between,
)
from nse_backtest.desk.markets import NSE, US


def test_tables_shape():
    assert COVERED_YEARS["NSE"] == frozenset({2026}) and COVERED_YEARS["US"] == frozenset({2026, 2027})
    assert len(HOLIDAYS["NSE"]) == 16 and all(d.year == 2026 and d.weekday() < 5 for d in HOLIDAYS["NSE"])
    assert len(HOLIDAYS["US"]) == 20
    assert EARLY_CLOSES["NSE"] == {}
    assert EARLY_CLOSES["US"] == {date(2026, 11, 27): time(13, 0), date(2026, 12, 24): time(13, 0),
                                  date(2027, 11, 26): time(13, 0)}


def test_nse_trading_days():
    assert is_trading_day(NSE, date(2026, 1, 15)) is False
    assert holiday_name(NSE, date(2026, 1, 15)) == "Municipal Corporation Election - Maharashtra"
    assert is_trading_day(NSE, date(2026, 10, 29)) is True
    assert holiday_name(NSE, date(2026, 10, 29)) is None
    assert is_trading_day(NSE, date(2026, 11, 8)) is False  # Muhurat Sunday: not supported
    assert holiday_name(NSE, date(2026, 11, 10)) == "Diwali-Balipratipada"


def test_us_trading_days_and_early_close():
    assert is_trading_day(US, date(2026, 11, 26)) is False
    assert holiday_name(US, date(2026, 11, 26)) == "Thanksgiving Day"
    assert is_trading_day(US, date(2026, 11, 27)) is True
    assert is_early_close(US, date(2026, 11, 27)) is True
    assert session_close_for(US, date(2026, 11, 27)) == time(13, 0)
    assert flat_by_for(US, date(2026, 11, 27)) == time(12, 45)
    assert session_close_for(US, date(2026, 11, 30)) == US.session_close
    assert flat_by_for(US, date(2026, 11, 30)) == US.flat_by
    assert is_early_close(US, date(2026, 11, 30)) is False
    assert is_trading_day(US, date(2027, 12, 24)) is False
    assert holiday_name(US, date(2027, 6, 18)) == "Juneteenth National Independence Day (observed)"


def test_uncovered_year_falls_back_to_weekdays():
    assert covered(NSE, date(2027, 1, 26)) is False
    assert is_trading_day(NSE, date(2027, 1, 26)) is True  # Republic Day 2027 unknown: weekday rule
    assert is_trading_day(NSE, date(2027, 1, 30)) is False  # Saturday
    assert covered(NSE, date(2026, 6, 1)) is True and covered(US, date(2027, 6, 1)) is True
    assert covered(US, date(2028, 1, 3)) is False


def test_unknown_market_is_uncovered():
    from dataclasses import replace
    odd = replace(NSE, code="XX")
    assert covered(odd, date(2026, 10, 20)) is False
    assert is_trading_day(odd, date(2026, 10, 20)) is True


def test_previous_and_next_across_dussehra():
    assert previous_trading_day(NSE, date(2026, 10, 21)) == date(2026, 10, 19)
    assert next_trading_day(NSE, date(2026, 10, 19)) == date(2026, 10, 21)
    assert previous_trading_day(NSE, date(2026, 10, 19)) == date(2026, 10, 16)  # over the weekend
    assert next_trading_day(US, date(2026, 11, 25)) == date(2026, 11, 27)


def test_trading_days_between():
    assert trading_days_between(NSE, date(2026, 10, 16), date(2026, 10, 21)) == 1
    assert trading_days_between(NSE, date(2026, 10, 28), date(2026, 10, 29)) == 0
    assert trading_days_between(NSE, date(2026, 10, 29), date(2026, 10, 28)) == 0
    assert trading_days_between(NSE, date(2026, 10, 15), date(2026, 10, 21)) == 2
    assert trading_days_between(US, date(2026, 11, 25), date(2026, 11, 30)) == 1  # 27th only
    # long spans are counted exactly, not by walking: Jan-Dec 2026 NSE has 251 weekdays - 16 holidays
    assert trading_days_between(NSE, date(2025, 12, 31), date(2027, 1, 1)) == 261 - 16


def test_datetime_inputs_are_normalised():
    assert is_trading_day(NSE, datetime(2026, 10, 20, 10, 0)) is False
    assert holiday_name(NSE, datetime(2026, 10, 20, 10, 0)) == "Dussehra"


def test_walks_are_bounded(monkeypatch):
    monkeypatch.setattr(cal, "is_trading_day", lambda p, d: False)
    with pytest.raises(RuntimeError):
        previous_trading_day(NSE, date(2026, 10, 21))
    with pytest.raises(RuntimeError):
        next_trading_day(NSE, date(2026, 10, 21))
