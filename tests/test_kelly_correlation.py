"""Tests for correlation-aware Kelly haircut."""
from __future__ import annotations

import pytest

from nse_backtest.features.kelly_sizing import correlation_haircut, kelly_size


def test_haircut_one_when_no_correlations():
    assert correlation_haircut([]) == 1.0


def test_haircut_one_for_uncorrelated_positions():
    """Zero correlation across the book → no shrinkage."""
    assert correlation_haircut([0.0, 0.0, 0.0]) == 1.0


def test_haircut_shrinks_with_positive_correlation():
    """Single ρ=0.5 → haircut = 1/sqrt(1.5) ≈ 0.816"""
    h = correlation_haircut([0.5])
    assert h == pytest.approx(0.816, abs=0.01)


def test_haircut_ignores_negative_correlations():
    """A negatively-correlated diversifier should NOT shrink the position."""
    h = correlation_haircut([-0.5, -0.3])
    assert h == 1.0


def test_haircut_aggregates_multiple_positive_correlations():
    """Sum of ρ in the denominator, not max — 3 positions at ρ=0.4 should shrink more."""
    h_one = correlation_haircut([0.4])
    h_three = correlation_haircut([0.4, 0.4, 0.4])
    assert h_three < h_one
    assert h_three == pytest.approx(1 / (1 + 1.2) ** 0.5, abs=0.001)


def test_kelly_applies_correlation_haircut_to_size():
    """Same edge with high book correlation → smaller position."""
    no_corr = kelly_size(
        calibrated_win_prob_pct=60.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000,
        max_risk_pct=10.0,
    )
    with_corr = kelly_size(
        calibrated_win_prob_pct=60.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000,
        max_risk_pct=10.0,
        open_book_correlations=[0.7, 0.6, 0.5],
    )
    assert with_corr.risk_pct_of_capital < no_corr.risk_pct_of_capital
    assert "ρ-haircut" in with_corr.rationale


def test_negative_correlations_dont_grow_size_beyond_uncorrelated():
    """A diversifier shouldn't pump up the size; haircut floor at 1.0."""
    no_corr = kelly_size(
        calibrated_win_prob_pct=60.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000,
        max_risk_pct=10.0,
    )
    diversifier = kelly_size(
        calibrated_win_prob_pct=60.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000,
        max_risk_pct=10.0,
        open_book_correlations=[-0.6],
    )
    assert diversifier.risk_pct_of_capital == no_corr.risk_pct_of_capital
