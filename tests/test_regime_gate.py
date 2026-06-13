import pandas as pd
import numpy as np
import pytest
from nse_backtest.features.regime_gate import regime_block


def _nifty_df(closes):
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.005 for c in closes],
        "Low": [c * 0.995 for c in closes], "Close": closes,
        "Volume": [100_000] * len(closes),
    }, index=pd.bdate_range("2024-01-02", periods=len(closes)))


def test_uptrending_nifty_does_not_block():
    closes = list(np.linspace(20000, 22000, 70))  # smooth uptrend
    block, reason = regime_block(_nifty_df(closes))
    assert block is False
    assert "supportive" in reason.lower()


def test_downtrending_nifty_blocks():
    closes = list(np.linspace(22000, 20000, 70))  # smooth downtrend
    block, reason = regime_block(_nifty_df(closes))
    assert block is True
    assert "non-trending" in reason.lower() or "below" in reason.lower()


def test_volatility_expansion_blocks(monkeypatch):
    """Calm for 60 days, then volatile for 20 days → block."""
    rng = np.random.default_rng(7)
    calm = 20000 + rng.normal(0, 5, 60).cumsum()
    spike = calm[-1] + rng.normal(0, 100, 20).cumsum()
    closes = list(np.concatenate([calm, spike]))
    block, reason = regime_block(_nifty_df(closes))
    assert block is True
    assert "vol" in reason.lower()


def test_missing_nifty_data_does_not_block():
    """Insufficient data → return False so v1 behavior is preserved."""
    block, _ = regime_block(None)
    assert block is False
    block, _ = regime_block(_nifty_df(list(np.linspace(20000, 21000, 30))))
    assert block is False


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("REGIME_GATE_ENABLED", "0")
    closes = list(np.linspace(22000, 20000, 70))  # downtrend that would block
    block, _ = regime_block(_nifty_df(closes))
    assert block is False
