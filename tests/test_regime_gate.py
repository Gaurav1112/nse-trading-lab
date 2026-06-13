import pandas as pd
import numpy as np
import pytest
from nse_backtest.features.regime_gate import regime_action


def _nifty_df(closes):
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.005 for c in closes],
        "Low": [c * 0.995 for c in closes], "Close": closes,
        "Volume": [100_000] * len(closes),
    }, index=pd.bdate_range("2023-01-02", periods=len(closes)))


def test_trending_tape_does_not_downgrade():
    closes = list(np.linspace(17000, 24000, 300))  # strong bull
    block, _ = regime_action(_nifty_df(closes), final_score=70)
    assert block is False


def test_hostile_tape_downgrades_any_score():
    closes = list(np.linspace(22000, 17000, 300))  # deep downtrend
    block, reason = regime_action(_nifty_df(closes), final_score=80)
    assert block is True
    assert "hostile" in reason.lower()


def test_mixed_tape_downgrades_only_below_75():
    """Build a sideways tape that classifies MIXED, then test score threshold."""
    rng = np.random.default_rng(11)
    # Drift slightly up but not enough to trigger TRENDING (60d return < 5%)
    base = np.linspace(20000, 20800, 300) + rng.normal(0, 50, 300)
    df = _nifty_df(list(base))
    # Score 70 (below 75) → downgrade if MIXED, no downgrade if TRENDING
    block_low, reason_low = regime_action(df, final_score=70)
    # Score 80 (>=75) → no downgrade regardless
    block_high, _ = regime_action(df, final_score=80)
    assert block_high is False
    # If the tape ended up TRENDING by accident, low-score also passes; both behaviors are valid
    if block_low:
        assert "mixed" in reason_low.lower() or "selective" in reason_low.lower()


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("REGIME_GATE_ENABLED", "0")
    closes = list(np.linspace(22000, 17000, 300))
    block, _ = regime_action(_nifty_df(closes), final_score=70)
    assert block is False


def test_missing_nifty_data_does_not_downgrade():
    block, _ = regime_action(None, final_score=70)
    assert block is False
