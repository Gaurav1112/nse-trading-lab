"""Unit tests for Wave A safety gates (MTF, liquidity, gap, earnings)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nse_backtest.features.mtf_confirmation import mtf_confirms
from nse_backtest.features.liquidity_filter import is_liquid_enough
from nse_backtest.features.gap_guard import gap_within_tolerance


def _daily_df(close_pattern, volume_pattern=None, open_pattern=None, start="2024-01-01"):
    n = len(close_pattern)
    idx = pd.date_range(start=start, periods=n, freq="B")
    df = pd.DataFrame({
        "Close": close_pattern,
        "Open":  open_pattern  if open_pattern  is not None else close_pattern,
        "High":  [c * 1.01 for c in close_pattern],
        "Low":   [c * 0.99 for c in close_pattern],
        "Volume": volume_pattern if volume_pattern is not None else [1_000_000] * n,
    }, index=idx)
    return df


# --- MTF ---

def test_mtf_passes_on_clear_weekly_uptrend():
    n = 400
    rising = np.linspace(100, 200, n)
    df = _daily_df(rising)
    downgrade, reason = mtf_confirms(df)
    assert not downgrade
    assert "confirms" in reason.lower()


def test_mtf_fails_when_weekly_below_ema():
    n = 400
    # Stock rallies for first 300 days, then drops below the long-term mean.
    pattern = list(np.linspace(100, 250, 300)) + list(np.linspace(250, 80, 100))
    df = _daily_df(pattern)
    downgrade, reason = mtf_confirms(df)
    assert downgrade
    assert "disconfirmation" in reason.lower()


def test_mtf_skips_when_insufficient_history():
    df = _daily_df(np.linspace(100, 110, 30))
    downgrade, reason = mtf_confirms(df)
    assert not downgrade
    assert "insufficient" in reason.lower()


# --- Liquidity ---

def test_liquidity_passes_when_volume_x_price_above_threshold():
    n = 60
    close = [1500.0] * n
    vol = [1_000_000] * n  # 150 cr/day, well above 50 cr threshold
    df = _daily_df(close, volume_pattern=vol)
    downgrade, reason = is_liquid_enough(df)
    assert not downgrade
    assert "OK" in reason


def test_liquidity_fails_for_thin_stock():
    n = 60
    close = [50.0] * n
    vol = [10_000] * n  # 50 lakh/day = 0.05 cr, way below threshold
    df = _daily_df(close, volume_pattern=vol)
    downgrade, reason = is_liquid_enough(df)
    assert downgrade
    assert "too thin" in reason.lower()


def test_liquidity_skips_when_no_volume_column():
    df = _daily_df([100] * 30)
    df = df.drop(columns=["Volume"])
    downgrade, reason = is_liquid_enough(df)
    assert not downgrade
    assert "skipped" in reason.lower()


# --- Gap guard ---

def test_gap_passes_when_open_close_to_prev_close():
    # prev close 100, today open 100.5 → +0.5% gap, under 2% threshold
    df = _daily_df([100, 102], open_pattern=[100, 100.5])
    downgrade, reason = gap_within_tolerance(df)
    assert not downgrade
    assert "Gap OK" in reason


def test_gap_fails_on_large_open_gap():
    # prev close 100, today open 105 → +5% gap, above 2% tolerance
    df = _daily_df([100, 102], open_pattern=[100, 105])
    downgrade, reason = gap_within_tolerance(df)
    assert downgrade
    assert "too large" in reason.lower()


def test_gap_skips_when_insufficient_history():
    df = _daily_df([100], open_pattern=[100])
    downgrade, reason = gap_within_tolerance(df)
    assert not downgrade
    assert "insufficient" in reason.lower()


# --- Earnings (smoke-only; lru_cache + yfinance offline-safe) ---

def test_earnings_gate_skips_gracefully_when_calendar_unavailable():
    """The earnings_clear() function must never crash and must default to
    no-block when calendar data can't be fetched (offline test environment).
    """
    from nse_backtest.features.earnings_guard import earnings_clear
    df = _daily_df([100] * 30)
    downgrade, reason = earnings_clear(df, "DEFINITELY_NOT_A_REAL_SYMBOL_XYZ")
    assert not downgrade
    assert any(t in reason.lower() for t in ["unavailable", "skipped", "ok"])


def test_earnings_gate_empty_symbol_short_circuits():
    from nse_backtest.features.earnings_guard import earnings_clear
    df = _daily_df([100] * 30)
    downgrade, reason = earnings_clear(df, "")
    assert not downgrade
    assert "no symbol" in reason.lower()
