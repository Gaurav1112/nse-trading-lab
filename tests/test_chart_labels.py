"""Regression: every chart helper in components/charts.py must render with
explicit axis titles + descriptive metadata so they're never anonymous again.

Triggered by the 2026-06-26 user report: 'diagram are getting render without
labels in all topics'. Across the entire app every chart had only `title=`
set, no axis_title, no subplot row titles. That's the floor for any
usable analytical chart — these tests enforce it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from components.charts import make_candlestick, make_equity_curve, make_sector_heat


def _fake_ohlcv(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2026-05-01", periods=n, freq="B")
    return pd.DataFrame({
        "Open": np.linspace(100, 120, n),
        "High": np.linspace(101, 122, n),
        "Low":  np.linspace(99, 118, n),
        "Close": np.linspace(100, 121, n),
        "Volume": np.linspace(1e6, 2e6, n),
    }, index=idx)


# ── make_candlestick ───────────────────────────────────────────────────

def test_candlestick_has_y_axis_titles_per_row():
    fig = make_candlestick(_fake_ohlcv(), title="Test")
    assert fig.layout.yaxis.title.text == "Price (₹)"
    assert fig.layout.yaxis2.title.text == "RSI"
    assert fig.layout.yaxis3.title.text == "Volume"


def test_candlestick_has_subplot_row_titles():
    fig = make_candlestick(_fake_ohlcv(), title="Test")
    texts = [a.text for a in fig.layout.annotations if a.text]
    assert any("Price" in t for t in texts)
    assert any("RSI" in t for t in texts)
    assert any("Volume" in t for t in texts)


def test_candlestick_has_x_axis_title_on_bottom():
    fig = make_candlestick(_fake_ohlcv(), title="Test")
    # Bottom (volume) subplot owns the x-axis
    assert fig.layout.xaxis3.title.text == "Date"


def test_candlestick_main_title_passed_through():
    fig = make_candlestick(_fake_ohlcv(), title="RELIANCE.NS")
    assert fig.layout.title.text == "RELIANCE.NS"


# ── make_equity_curve ──────────────────────────────────────────────────

def test_equity_curve_axes_labeled():
    eq = pd.Series(np.cumsum(np.random.randn(30)) + 100,
                   index=pd.date_range("2026-05-01", periods=30, freq="B"))
    fig = make_equity_curve([
        {"equity_curve": eq, "config": {"strategy_name": "Demo"}}
    ])
    assert fig.layout.xaxis.title.text == "Date"
    assert "Equity" in fig.layout.yaxis.title.text


def test_equity_curve_has_title():
    fig = make_equity_curve([])
    assert "equity curves" in fig.layout.title.text.lower()


# ── make_sector_heat ───────────────────────────────────────────────────

def test_sector_heat_axes_labeled():
    fig = make_sector_heat({"IT": 1.2, "Bank": -0.8, "Auto": 0.5})
    assert fig.layout.xaxis.title.text == "Sector"
    assert "%" in fig.layout.yaxis.title.text


def test_sector_heat_title_describes_unit():
    fig = make_sector_heat({"IT": 1.0})
    # Title must include % so user knows the axis is percent change
    assert "%" in fig.layout.title.text


def test_sector_heat_renders_with_empty_or_none_values():
    """Helper must be defensive — sector_perf often has None for unfetched sectors."""
    fig = make_sector_heat({"IT": None, "Bank": 0.5})
    assert fig is not None
    assert fig.layout.yaxis.title.text  # still labelled
