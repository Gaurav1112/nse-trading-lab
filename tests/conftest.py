"""Shared pytest fixtures for the nse_backtest test suite."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def trending_ohlcv() -> pd.DataFrame:
    """Synthetic deterministic trending OHLCV (~400 business days)."""
    np.random.seed(42)
    n = 400
    dates = pd.bdate_range("2022-01-03", periods=n)
    trend = np.linspace(100.0, 250.0, n)
    noise = np.random.normal(0, 1.5, n).cumsum() * 0.25
    close = trend + noise
    high = close + np.random.uniform(0.5, 2.5, n)
    low = close - np.random.uniform(0.5, 2.5, n)
    opn = close + np.random.uniform(-1.0, 1.0, n)
    vol = np.random.randint(500_000, 2_000_000, n)
    return pd.DataFrame(
        {"Open": opn, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


@pytest.fixture
def flat_ohlcv() -> pd.DataFrame:
    """Sideways/choppy series used to stress NaN-safety and boundary cases."""
    np.random.seed(1)
    n = 250
    dates = pd.bdate_range("2023-01-02", periods=n)
    base = np.full(n, 200.0) + np.random.normal(0, 1.0, n)
    high = base + np.random.uniform(0.3, 1.0, n)
    low = base - np.random.uniform(0.3, 1.0, n)
    return pd.DataFrame(
        {"Open": base, "High": high, "Low": low, "Close": base,
         "Volume": np.random.randint(100_000, 500_000, n)},
        index=dates,
    )
