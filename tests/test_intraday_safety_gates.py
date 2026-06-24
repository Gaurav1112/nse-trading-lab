"""Tests for the intraday safety gates that would have stopped the
2026-06-19 INFY loss. The five gates: reversal candle, volume, bar age,
time-of-day, sector capitulation."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

from nse_backtest.intraday.safety_gates import (
    reversal_candle_confirmed, volume_confirmed, bar_is_fresh,
    good_time_of_day_to_enter, time_to_mis_squareoff,
    detect_sector_capitulation, confirm_intraday_long,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _bar(o, h, l, c, v=100000):
    return {"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}


def _df(*bars):
    idx = pd.date_range("2026-06-22 09:15", periods=len(bars), freq="15min", tz=IST)
    return pd.DataFrame(list(bars), index=idx)


# ── Reversal candle ────────────────────────────────────────────────────

def test_reversal_passes_on_green_with_long_lower_wick():
    df = _df(_bar(100, 100, 100, 100), _bar(100, 102, 96, 101))  # green, big wick
    ok, reason = reversal_candle_confirmed(df)
    assert ok
    assert "confirmed" in reason.lower()


def test_reversal_fails_on_red_bar():
    df = _df(_bar(100, 100, 100, 100), _bar(100, 100.5, 98, 99))
    ok, reason = reversal_candle_confirmed(df)
    assert not ok
    assert "red" in reason.lower() or "falling" in reason.lower()


def test_reversal_fails_on_green_with_no_wick():
    df = _df(_bar(100, 100, 100, 100), _bar(100, 102, 99.95, 101))  # tiny lower wick
    ok, reason = reversal_candle_confirmed(df)
    assert not ok
    assert "wick" in reason.lower() or "rejection" in reason.lower()


# ── Volume confirmation ────────────────────────────────────────────────

def test_volume_passes_on_spike():
    bars = [_bar(100, 100, 100, 100, v=10000) for _ in range(20)]
    bars.append(_bar(100, 100, 100, 100, v=20000))   # 2x average
    df = _df(*bars)
    ok, reason = volume_confirmed(df, multiplier=1.5)
    assert ok
    assert "confirmed" in reason.lower()


def test_volume_fails_on_quiet_bar():
    bars = [_bar(100, 100, 100, 100, v=10000) for _ in range(20)]
    bars.append(_bar(100, 100, 100, 100, v=5000))    # 0.5x
    df = _df(*bars)
    ok, reason = volume_confirmed(df)
    assert not ok
    assert "weak" in reason.lower() or "conviction" in reason.lower()


# ── Bar age ────────────────────────────────────────────────────────────

def test_bar_freshness_pass_for_recent():
    recent = datetime.now(tz=IST) - timedelta(minutes=5)
    ok, reason = bar_is_fresh(pd.Timestamp(recent))
    assert ok


def test_bar_freshness_fail_for_stale():
    stale = datetime.now(tz=IST) - timedelta(minutes=60)
    ok, reason = bar_is_fresh(pd.Timestamp(stale))
    assert not ok
    assert "stale" in reason.lower()


# ── Time of day ────────────────────────────────────────────────────────

def test_squareoff_returns_minutes():
    minutes, msg = time_to_mis_squareoff()
    assert isinstance(minutes, int)
    assert "squareoff" in msg.lower() or "passed" in msg.lower()


# ── Sector capitulation ────────────────────────────────────────────────

def test_sector_capitulation_fires_on_three_it_names():
    warnings = detect_sector_capitulation(["INFY", "TCS", "HCLTECH", "RELIANCE"])
    # INFY/TCS/HCLTECH are all IT
    assert "INFY" in warnings
    assert "TCS" in warnings
    assert "HCLTECH" in warnings
    assert "RELIANCE" not in warnings   # Energy, alone
    assert "IT" in warnings["INFY"]


def test_sector_capitulation_silent_with_two_names():
    warnings = detect_sector_capitulation(["INFY", "TCS"])
    assert warnings == {}    # need ≥3 by default


def test_sector_capitulation_unclassified_ignored():
    warnings = detect_sector_capitulation(["UNKNOWN1", "UNKNOWN2", "UNKNOWN3"])
    assert warnings == {}


# ── Composite confirmation ─────────────────────────────────────────────

def test_confirm_long_passed_all_when_everything_green():
    bars = [_bar(100, 100, 100, 100, v=10000) for _ in range(25)]
    bars.append(_bar(100, 103, 96, 101.5, v=25000))   # green + wick + volume
    df = _df(*bars)
    # Note: time-of-day and bar age depend on real time — those may legitimately fail
    v = confirm_intraday_long(df, df.index[-1], in_sector_capitulation=False)
    # Reversal + Volume gates should pass; Sector not-capitulation is True
    assert v.gates["Reversal candle"][0] is True
    assert v.gates["Volume"][0] is True
    assert v.gates["Not sector-wide capitulation"][0] is True


def test_confirm_long_blocked_by_sector_capitulation():
    bars = [_bar(100, 100, 100, 100, v=10000) for _ in range(25)]
    bars.append(_bar(100, 103, 96, 101.5, v=25000))
    df = _df(*bars)
    v = confirm_intraday_long(df, df.index[-1], in_sector_capitulation=True)
    assert v.gates["Not sector-wide capitulation"][0] is False
    assert "Not sector-wide capitulation" in v.failed_gates
    assert not v.passed_all
