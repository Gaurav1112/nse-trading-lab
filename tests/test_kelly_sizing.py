"""Tests for fractional Kelly position sizing."""
from __future__ import annotations

import pytest

from nse_backtest.features.kelly_sizing import kelly_size


def test_kelly_zero_when_no_edge():
    """p <= 50% baseline => no Kelly position."""
    out = kelly_size(
        calibrated_win_prob_pct=45.0, risk_reward=2.0,
        entry_price=100.0, stop_loss=95.0, capital=100_000,
    )
    assert out.suggested_qty == 0
    assert "no edge" in out.rationale.lower() or "≤ base-rate" in out.rationale


def test_kelly_positive_when_edge_exists():
    """p=70%, R:R=2 => full Kelly ~0.55, quarter Kelly ~0.14 → capped to user max."""
    out = kelly_size(
        calibrated_win_prob_pct=70.0, risk_reward=2.0,
        entry_price=100.0, stop_loss=95.0, capital=100_000,
        max_risk_pct=2.0,
    )
    assert out.suggested_qty > 0
    # Kelly says 14%, but max_risk_pct caps it at 2% (₹2000 risk, ₹5/share → 400 shares)
    assert out.risk_pct_of_capital <= 2.0 + 1e-6
    assert out.suggested_qty == 400


def test_kelly_respects_max_risk_cap():
    """Even with crazy-high edge, never exceed user's max_risk_pct."""
    out = kelly_size(
        calibrated_win_prob_pct=95.0, risk_reward=4.0,
        entry_price=100.0, stop_loss=95.0, capital=100_000,
        max_risk_pct=1.5,
    )
    assert out.risk_pct_of_capital <= 1.5 + 1e-6


def test_kelly_uses_min_risk_floor_for_marginal_edges():
    """At p=51%, R:R=1.2, full Kelly is tiny (~0.02). Floor at 0.25%."""
    out = kelly_size(
        calibrated_win_prob_pct=51.0, risk_reward=1.2,
        entry_price=100.0, stop_loss=95.0, capital=100_000,
        max_risk_pct=2.0,
    )
    # Risk floor is 0.25% => ₹250 risk => 50 shares at ₹5/share
    assert out.suggested_qty >= 50
    assert out.risk_pct_of_capital >= 0.25 - 1e-6


def test_kelly_invalid_inputs_returns_zero():
    """Zero/negative R:R, negative risk_per_share, zero capital all yield 0 size."""
    out = kelly_size(
        calibrated_win_prob_pct=80.0, risk_reward=0.0,
        entry_price=100.0, stop_loss=95.0, capital=100_000,
    )
    assert out.suggested_qty == 0

    out2 = kelly_size(
        calibrated_win_prob_pct=80.0, risk_reward=2.0,
        entry_price=100.0, stop_loss=100.0, capital=100_000,
    )
    assert out2.suggested_qty == 0  # zero risk per share = invalid

    out3 = kelly_size(
        calibrated_win_prob_pct=80.0, risk_reward=2.0,
        entry_price=100.0, stop_loss=95.0, capital=0,
    )
    assert out3.suggested_qty == 0


def test_kelly_more_size_when_edge_higher():
    """Higher edge → larger risk_pct_of_capital (when neither hits cap)."""
    low = kelly_size(
        calibrated_win_prob_pct=52.0, risk_reward=1.2,
        entry_price=100.0, stop_loss=95.0, capital=100_000, max_risk_pct=20.0,
    )
    high = kelly_size(
        calibrated_win_prob_pct=60.0, risk_reward=2.0,
        entry_price=100.0, stop_loss=95.0, capital=100_000, max_risk_pct=20.0,
    )
    assert high.risk_pct_of_capital > low.risk_pct_of_capital
    assert high.kelly_fraction > low.kelly_fraction
