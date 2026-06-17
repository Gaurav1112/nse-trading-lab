"""Tests for cross-sectional momentum ranking (F4)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nse_backtest.features.cross_sectional import (
    momentum_score, rank_universe, cross_sectional_boost,
)


def _df_with_returns(daily_drift_pct: float, n: int = 300, seed: int = 0):
    rng = np.random.default_rng(seed)
    rets = rng.normal(daily_drift_pct / 100, 0.01, n)
    closes = 100 * (1 + rets).cumprod()
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": closes}, index=idx)


def test_momentum_score_returns_none_on_short_history():
    df = _df_with_returns(0.0, n=100)
    assert momentum_score(df) is None


def test_momentum_score_higher_drift_higher_return():
    """Use deterministic price paths so the test isn't subject to RNG noise."""
    def _deterministic(start: float, end: float, n: int = 300) -> pd.DataFrame:
        closes = np.linspace(start, end, n)
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        return pd.DataFrame({"Close": closes}, index=idx)
    a = _deterministic(100, 200)   # +100% over the path
    b = _deterministic(100, 100)   # flat
    c = _deterministic(100, 60)    # -40%
    ms_a = momentum_score(a)
    ms_b = momentum_score(b)
    ms_c = momentum_score(c)
    assert ms_a.return_12m_ex_1m_pct > ms_b.return_12m_ex_1m_pct > ms_c.return_12m_ex_1m_pct


def test_rank_universe_assigns_quintiles():
    dfs = {
        f"SYM_{i:02d}": _df_with_returns(drift, seed=i)
        for i, drift in enumerate(np.linspace(-0.05, 0.05, 50))
    }
    rankings = rank_universe(dfs)
    assert len(rankings) == 50
    # Top of universe should be quintile 5; bottom should be 1
    quintiles = sorted(rankings.values(), key=lambda x: x.return_12m_ex_1m_pct)
    assert quintiles[-1].quintile == 5
    assert quintiles[0].quintile == 1
    # Quintile 5 picks should equal top ~20%
    q5 = [r for r in rankings.values() if r.quintile == 5]
    assert 8 <= len(q5) <= 12


def test_cross_sectional_boost_top_quintile():
    dummy = momentum_score(_df_with_returns(0.05, seed=4))
    dummy.quintile = 5
    delta, reason = cross_sectional_boost(dummy)
    assert delta > 0
    assert "top" in reason.lower()


def test_cross_sectional_boost_bottom_quintile_penalty():
    dummy = momentum_score(_df_with_returns(-0.05, seed=5))
    dummy.quintile = 1
    delta, reason = cross_sectional_boost(dummy)
    assert delta < 0
    assert "bottom" in reason.lower()


def test_cross_sectional_boost_middle_neutral():
    dummy = momentum_score(_df_with_returns(0.0, seed=6))
    dummy.quintile = 3
    delta, reason = cross_sectional_boost(dummy)
    assert delta == 0.0
    assert "neutral" in reason.lower()


def test_cross_sectional_boost_handles_missing_input():
    delta, reason = cross_sectional_boost(None)
    assert delta == 0.0
    assert "insufficient" in reason.lower()


def test_rank_universe_handles_short_histories_gracefully():
    dfs = {
        "FULL": _df_with_returns(0.02, n=300, seed=7),
        "SHORT": _df_with_returns(0.02, n=100, seed=8),
    }
    rankings = rank_universe(dfs)
    assert "FULL" in rankings
    assert "SHORT" not in rankings
