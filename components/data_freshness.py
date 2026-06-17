"""Data freshness check — Rohan Iyer's integrity guard.

Single entry point: check_freshness(df) returns (is_fresh, status, last_bar_ts).

Rules (NSE-aware):
  - Outside market hours: last bar must be today OR last trading day (Mon-Fri).
  - Inside market hours (9:15-15:30 IST Mon-Fri): last bar must be today.
  - Weekend: last bar must be the most recent Friday or later.

Returns one of these statuses:
  FRESH        — green badge, normal operation
  END_OF_DAY   — amber, last close from yesterday/Friday — normal outside market hours
  STALE        — red, last bar is older than expected; block signals
  UNKNOWN      — gray, can't determine
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Final, Optional
from zoneinfo import ZoneInfo

import pandas as pd


IST = ZoneInfo("Asia/Kolkata")


class FreshnessStatus:
    FRESH: Final[str] = "FRESH"
    END_OF_DAY: Final[str] = "END_OF_DAY"
    STALE: Final[str] = "STALE"
    UNKNOWN: Final[str] = "UNKNOWN"


@dataclass
class FreshnessVerdict:
    status: str
    last_bar_ts: Optional[pd.Timestamp]
    last_bar_date_str: str          # "2026-06-13" or "—"
    age_business_days: int           # business days from last bar to now-in-IST
    market_open_now: bool
    color: str
    message: str


def _ist_now() -> datetime:
    return datetime.now(IST)


def _is_market_hours(now_ist: datetime) -> bool:
    """NSE cash market: 9:15-15:30 IST, Mon-Fri."""
    if now_ist.weekday() >= 5:  # Sat or Sun
        return False
    t = now_ist.time()
    return time(9, 15) <= t <= time(15, 30)


def _business_days_between(start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Business days between two timestamps (date-aware)."""
    if start is None or end is None or pd.isna(start) or pd.isna(end):
        return 0
    s = pd.Timestamp(start).normalize()
    e = pd.Timestamp(end).normalize()
    if s > e:
        return 0
    return int(pd.bdate_range(s, e).size - 1)


def _last_trading_day(now_ist: datetime) -> pd.Timestamp:
    """The most recent trading day relative to now (could be today if weekday)."""
    today = pd.Timestamp(now_ist.date())
    # If today is a weekday, today counts; if Sat/Sun, go back to Friday.
    while today.weekday() >= 5:
        today -= pd.Timedelta(days=1)
    return today


def check_freshness(df: Optional[pd.DataFrame]) -> FreshnessVerdict:
    """Inspect df.index to determine how fresh the OHLCV data is."""
    if df is None or len(df) == 0:
        return FreshnessVerdict(
            status=FreshnessStatus.UNKNOWN, last_bar_ts=None,
            last_bar_date_str="—", age_business_days=0,
            market_open_now=False, color="#7A93AA",
            message="No data — cannot assess freshness",
        )

    last_ts = pd.Timestamp(df.index[-1])
    now = _ist_now()
    market_open = _is_market_hours(now)
    last_trade_day = _last_trading_day(now)

    # Convert last_ts to a comparable date in IST
    if last_ts.tzinfo is None:
        last_date = last_ts.normalize()
    else:
        last_date = last_ts.tz_convert(IST).normalize()

    age = _business_days_between(last_date, last_trade_day)
    date_str = last_date.strftime("%Y-%m-%d")

    if market_open:
        if age == 0:
            status, color, msg = (
                FreshnessStatus.FRESH, "#00FF87",
                f"Data fresh as of {date_str} (market open)",
            )
        elif age == 1:
            # During market hours, age=1 (yesterday's EOD) is the normal
            # behaviour for free EOD feeds — yfinance doesn't have today's
            # intraday data. Tell the user honestly instead of suggesting
            # they can "fix" it.
            status, color, msg = (
                FreshnessStatus.END_OF_DAY, "#FFB800",
                f"Yesterday's close ({date_str}) shown — yfinance is an EOD feed and only "
                f"updates after market close. Today's live CMP comes from NSE's quote API at save time.",
            )
        else:
            status, color, msg = (
                FreshnessStatus.STALE, "#FF4D4D",
                f"Data is {age} business days behind during market hours — feed appears broken. "
                f"Try refreshing the page; if it persists, yfinance may be rate-limiting.",
            )
    else:
        if age == 0:
            status, color, msg = (
                FreshnessStatus.FRESH, "#00FF87",
                f"Data fresh as of {date_str} (market closed)",
            )
        elif age == 1:
            status, color, msg = (
                FreshnessStatus.END_OF_DAY, "#FFB800",
                f"End-of-day from previous session ({date_str}) — normal outside market hours",
            )
        else:
            status, color, msg = (
                FreshnessStatus.STALE, "#FF4D4D",
                f"Data is {age} business days behind — feed appears broken",
            )

    return FreshnessVerdict(
        status=status, last_bar_ts=last_ts, last_bar_date_str=date_str,
        age_business_days=age, market_open_now=market_open,
        color=color, message=msg,
    )
