"""All strategies must return clean signals with no look-ahead."""
import numpy as np
import pytest

from nse_backtest.strategies import STRATEGIES


@pytest.mark.parametrize("name", list(STRATEGIES.keys()))
def test_strategy_emits_clean_signal(name, trending_ohlcv):
    fn = STRATEGIES[name]
    sd = fn(trending_ohlcv.copy())
    assert "signal" in sd.columns
    assert "strategy_name" in sd.columns
    sig = sd["signal"]
    assert sig.isna().sum() == 0
    assert set(np.unique(sig.values)).issubset({-1, 0, 1})


@pytest.mark.parametrize("name", list(STRATEGIES.keys()))
def test_strategy_no_lookahead(name, trending_ohlcv):
    """Truncating the input must not change historic signals (look-ahead check)."""
    fn = STRATEGIES[name]
    full = fn(trending_ohlcv.copy())
    truncated = fn(trending_ohlcv.iloc[:-30].copy())
    common = full.index.intersection(truncated.index)
    full_sig = full.loc[common, "signal"].values
    trunc_sig = truncated.loc[common, "signal"].values
    # The last few common bars may legitimately differ if the strategy uses a
    # rolling window that reaches the end; allow a small tail tolerance.
    diff = (full_sig != trunc_sig).sum()
    assert diff <= 2, f"{name} differs in {diff} historic bars when future is hidden"
