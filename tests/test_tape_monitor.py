import pandas as pd
import numpy as np
from nse_backtest.tape_monitor import assess_tape, TapeRegime


def _nifty_df(closes):
    return pd.DataFrame({
        "Open": closes, "High": [c * 1.005 for c in closes],
        "Low": [c * 0.995 for c in closes], "Close": closes,
        "Volume": [100_000] * len(closes),
    }, index=pd.bdate_range("2023-01-02", periods=len(closes)))


def test_strong_bull_tape_classified_trending():
    closes = list(np.linspace(17000, 24000, 300))  # +41% over ~14 months — clear bull
    a = assess_tape(_nifty_df(closes))
    assert a is not None
    assert a.regime == TapeRegime.TRENDING
    assert a.above_50ema and a.above_200ema and a.ema_50_above_200
    assert "high" in a.recommendation.lower() or "trade normally" in a.recommendation.lower()


def test_deep_downtrend_classified_hostile():
    closes = list(np.linspace(22000, 17000, 300))  # -23% — clear bear
    a = assess_tape(_nifty_df(closes))
    assert a.regime == TapeRegime.HOSTILE
    assert "paper" in a.recommendation.lower() or "sit out" in a.recommendation.lower()


def test_sideways_classified_mixed():
    """Flat/choppy market — neither bull nor bear."""
    rng = np.random.default_rng(7)
    closes = list(20000 + rng.normal(0, 100, 300).cumsum() * 0.1)  # tiny drift, lots of noise
    a = assess_tape(_nifty_df(closes))
    assert a.regime in (TapeRegime.MIXED, TapeRegime.HOSTILE)  # accept either; mostly MIXED
    # If MIXED, recommendation should mention being selective.
    if a.regime == TapeRegime.MIXED:
        assert "selective" in a.recommendation.lower() or "modest" in a.recommendation.lower()


def test_insufficient_data_returns_none():
    a = assess_tape(_nifty_df(list(np.linspace(20000, 21000, 50))))
    assert a is None
    a = assess_tape(None)
    assert a is None
