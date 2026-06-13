"""Phase D: data freshness — Rohan's integrity guard."""
from datetime import datetime, time
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from components.data_freshness import (
    check_freshness, FreshnessStatus, FreshnessVerdict, IST,
)


def _df_with_last(date_str):
    idx = pd.bdate_range(end=date_str, periods=10)
    return pd.DataFrame({"Open": [100]*10, "High": [101]*10, "Low": [99]*10,
                         "Close": [100]*10, "Volume": [1000]*10}, index=idx)


@patch("components.data_freshness._ist_now")
def test_today_during_market_hours_is_fresh(mock_now):
    mock_now.return_value = datetime(2026, 6, 15, 11, 0, tzinfo=IST)  # Mon 11 AM IST
    df = _df_with_last("2026-06-15")
    v = check_freshness(df)
    assert v.status == FreshnessStatus.FRESH
    assert v.market_open_now is True


@patch("components.data_freshness._ist_now")
def test_yesterday_during_market_hours_is_stale(mock_now):
    mock_now.return_value = datetime(2026, 6, 15, 11, 0, tzinfo=IST)
    df = _df_with_last("2026-06-12")  # Friday — 1 business day behind on Monday
    v = check_freshness(df)
    assert v.status == FreshnessStatus.STALE


@patch("components.data_freshness._ist_now")
def test_yesterday_outside_market_hours_is_end_of_day(mock_now):
    mock_now.return_value = datetime(2026, 6, 15, 18, 0, tzinfo=IST)  # Mon evening
    df = _df_with_last("2026-06-12")
    v = check_freshness(df)
    assert v.status == FreshnessStatus.END_OF_DAY


@patch("components.data_freshness._ist_now")
def test_today_outside_market_hours_is_fresh(mock_now):
    mock_now.return_value = datetime(2026, 6, 15, 18, 0, tzinfo=IST)
    df = _df_with_last("2026-06-15")
    v = check_freshness(df)
    assert v.status == FreshnessStatus.FRESH


@patch("components.data_freshness._ist_now")
def test_weekend_friday_data_is_fresh(mock_now):
    mock_now.return_value = datetime(2026, 6, 14, 11, 0, tzinfo=IST)  # Sun
    df = _df_with_last("2026-06-12")  # Fri
    v = check_freshness(df)
    assert v.status == FreshnessStatus.FRESH
    assert v.market_open_now is False


@patch("components.data_freshness._ist_now")
def test_very_old_data_is_stale(mock_now):
    mock_now.return_value = datetime(2026, 6, 15, 11, 0, tzinfo=IST)
    df = _df_with_last("2026-05-15")  # ~1 month behind
    v = check_freshness(df)
    assert v.status == FreshnessStatus.STALE
    assert v.age_business_days > 10


def test_empty_or_none_returns_unknown():
    v = check_freshness(None)
    assert v.status == FreshnessStatus.UNKNOWN
    v = check_freshness(pd.DataFrame())
    assert v.status == FreshnessStatus.UNKNOWN
