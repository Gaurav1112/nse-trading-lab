"""Tests for the book-correlation helper."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from components.correlations import book_correlations


def _df(prices):
    n = len(prices)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": prices}, index=idx)


def test_empty_open_book_returns_empty():
    cand = _df(np.linspace(100, 120, 100))
    assert book_correlations(cand, {}) == []


def test_perfectly_correlated_positions_return_near_one():
    cand = _df(np.linspace(100, 120, 100))
    held = _df(np.linspace(50, 60, 100))  # same monotonic path
    rhos = book_correlations(cand, {"HELD": held})
    assert len(rhos) == 1
    assert rhos[0] > 0.95


def test_uncorrelated_positions_return_near_zero():
    rng = np.random.default_rng(42)
    cand = _df(100 + rng.normal(0, 1, 100).cumsum())
    held = _df(100 + rng.normal(0, 1, 100).cumsum())
    rhos = book_correlations(cand, {"HELD": held})
    # Cumulative random walks aren't truly independent — give some slack
    assert len(rhos) == 1
    assert -0.5 < rhos[0] < 0.5


def test_short_history_skipped():
    """A held position with <20 overlapping bars should be silently skipped."""
    cand = _df(np.linspace(100, 120, 100))
    short = _df([100, 101, 102])
    rhos = book_correlations(cand, {"SHORT": short})
    assert rhos == []


def test_multiple_positions_one_entry_per_position():
    """Two open positions → two correlation values, regardless of magnitude."""
    rng = np.random.default_rng(0)
    cand_rets = rng.normal(0, 0.01, 100)
    a_rets = cand_rets + rng.normal(0, 0.001, 100)   # near-identical
    b_rets = -cand_rets + rng.normal(0, 0.001, 100)  # mirrored
    cand = _df(100 * (1 + cand_rets).cumprod())
    a = _df(100 * (1 + a_rets).cumprod())
    b = _df(100 * (1 + b_rets).cumprod())
    rhos = book_correlations(cand, {"A": a, "B": b})
    assert len(rhos) == 2
    assert rhos[0] > 0.9     # A is highly correlated
    assert rhos[1] < -0.9    # B is anti-correlated
