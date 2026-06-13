"""Earnings avoidance — skip stocks with results inside 7 calendar days.

Earnings release is a binary-event variance spike. The scorer's
edge is built from price-action signal that earnings news invalidates
in seconds. Honest move: refuse new entries within a 7-day window
before earnings, with a graceful fallback when the calendar can't be
fetched (treat as no-block rather than block-everything).

Rule:
  next earnings date - today > 7 days  → OK
  next earnings date - today <= 7 days → downgrade
  data missing or fetch fails          → no block (logged)

Returns (downgrade: bool, reason: str).
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

_log = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 7
_WINDOW_DAYS = int(os.getenv("NSE_EARNINGS_BUFFER_DAYS", _DEFAULT_WINDOW_DAYS))


@lru_cache(maxsize=200)
def _next_earnings_date(symbol: str) -> Optional[date]:
    """Return the next upcoming earnings date for symbol.NS, or None on miss."""
    if not symbol:
        return None
    try:
        import yfinance as yf  # local import to keep module import cheap
        t = yf.Ticker(f"{symbol}.NS")
        cal = t.calendar
        if cal is None:
            return None
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date")
            if isinstance(ed, (list, tuple)) and ed:
                return ed[0].date() if hasattr(ed[0], "date") else ed[0]
            if hasattr(ed, "date"):
                return ed.date()
            return None
        if hasattr(cal, "loc"):
            try:
                ed = cal.loc["Earnings Date"].iloc[0]
                return ed.date() if hasattr(ed, "date") else ed
            except (KeyError, IndexError):
                return None
    except Exception as e:
        _log.debug("earnings lookup for %s failed: %s", symbol, e)
        return None
    return None


def earnings_clear(df, symbol: str) -> tuple[bool, str]:
    """Returns (downgrade, reason). True downgrade => earnings inside window."""
    if not symbol:
        return False, "Earnings guard: no symbol, skipped"
    # Backtest mode: skip live calendar lookups. The yfinance Ticker.calendar
    # endpoint returns *today's* upcoming earnings, which is the wrong context
    # for historical replay (would inject look-ahead bias and bias certain
    # symbols out of the backtest universe). Picker_replay sets NSE_BACKTEST_MODE=1.
    if os.environ.get("NSE_BACKTEST_MODE") == "1":
        return False, "Earnings guard: backtest mode, skipped"
    sym = symbol.replace(".NS", "").upper()
    next_ed = _next_earnings_date(sym)
    if next_ed is None:
        return False, "Earnings calendar: data unavailable, skipped"

    today = date.today()
    delta = (next_ed - today).days
    if delta < 0:
        return False, f"Earnings calendar: last reported {-delta}d ago, OK"
    if delta > _WINDOW_DAYS:
        return False, f"Earnings calendar OK (next event in {delta}d)"
    return True, (
        f"Earnings inside {_WINDOW_DAYS}d window "
        f"(next event {next_ed.isoformat()}, {delta}d away) — refusing entry"
    )
