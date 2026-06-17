"""Tests for ATR-based volatility targeting in Kelly."""
from __future__ import annotations

import os

import pytest

from nse_backtest.features.kelly_sizing import atr_vol_adjustment, kelly_size


def test_atr_neutral_when_missing(monkeypatch):
    monkeypatch.delenv("NSE_BACKTEST_MODE", raising=False)
    assert atr_vol_adjustment(None, 100) == 1.0
    assert atr_vol_adjustment(0, 100) == 1.0
    assert atr_vol_adjustment(2.0, 0) == 1.0


def test_atr_neutral_in_backtest_mode(monkeypatch):
    """Picker_replay sets NSE_BACKTEST_MODE=1; vol target should not double-count."""
    monkeypatch.setenv("NSE_BACKTEST_MODE", "1")
    assert atr_vol_adjustment(2.0, 100) == 1.0


def test_atr_shrinks_high_vol(monkeypatch):
    """Stock with ATR/price 5% (high vol) → multiplier < 1.0 to size DOWN."""
    monkeypatch.delenv("NSE_BACKTEST_MODE", raising=False)
    mult = atr_vol_adjustment(atr=5.0, entry_price=100.0, target_vol_pct=2.0)
    assert mult < 1.0
    assert mult == pytest.approx(0.4, abs=0.01)


def test_atr_grows_low_vol(monkeypatch):
    """Stock with ATR/price 1% (low vol) → multiplier > 1.0 to size UP."""
    monkeypatch.delenv("NSE_BACKTEST_MODE", raising=False)
    mult = atr_vol_adjustment(atr=1.0, entry_price=100.0, target_vol_pct=2.0)
    assert mult > 1.0
    assert mult == pytest.approx(2.0, abs=0.01)


def test_atr_clipped_to_sensible_range(monkeypatch):
    monkeypatch.delenv("NSE_BACKTEST_MODE", raising=False)
    # Tiny ATR (very-low-vol stock) — would otherwise compute very high mult
    mult = atr_vol_adjustment(atr=0.001, entry_price=100.0, target_vol_pct=2.0)
    assert mult <= 4.0
    # Huge ATR (very-high-vol stock)
    mult2 = atr_vol_adjustment(atr=50.0, entry_price=100.0, target_vol_pct=2.0)
    assert mult2 >= 0.25


def test_kelly_atr_param_affects_size(monkeypatch):
    monkeypatch.delenv("NSE_BACKTEST_MODE", raising=False)
    no_atr = kelly_size(
        calibrated_win_prob_pct=70.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000, max_risk_pct=10.0,
    )
    high_vol = kelly_size(
        calibrated_win_prob_pct=70.0, risk_reward=2.0,
        entry_price=100, stop_loss=95, capital=100_000, max_risk_pct=10.0,
        atr=5.0,  # 5% vol → shrink
    )
    assert high_vol.risk_pct_of_capital < no_atr.risk_pct_of_capital
    assert "ATR-vol" in high_vol.rationale
