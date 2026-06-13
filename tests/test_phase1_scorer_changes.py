import pandas as pd
import numpy as np
from nse_backtest.scorer import analyze_stock


def _bullish_df(n=260):
    import numpy as np
    rng = np.random.default_rng(7)
    base = 100 * (1 + np.linspace(0, 0.6, n)) + rng.normal(0, 0.4, n).cumsum() * 0.3
    return pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Volume": rng.integers(2_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def test_backtest_dimension_removed_from_runtime_score():
    """run_backtests=False used to silently inject 50/100 for 15% of weight.
    After Phase 1, the backtest dimension is NOT part of the runtime final_score."""
    df = _bullish_df()
    out = analyze_stock(df, "TEST", run_backtests=False)

    expected = (
        out.trend_score * 0.30
        + out.momentum_score * 0.23
        + out.volume_score * 0.18
        + out.volatility_score * 0.12
        + out.risk_score * 0.17
    )
    assert abs(out.final_score - expected) < 0.5, (
        f"final_score {out.final_score:.2f} does not match renormalized 5-dim sum {expected:.2f}; "
        f"backtest dimension may still be silently contributing."
    )


def test_backtest_score_is_zero_when_skipped():
    """When run_backtests=False, backtest_score stays at 0 (not the old 50 fallback)
    — this makes the bypass observable instead of silently noisy."""
    df = _bullish_df()
    out = analyze_stock(df, "TEST", run_backtests=False)
    assert out.backtest_score == 0


def test_momentum_decay_penalty_fires_on_flatlined_uptrend():
    """A stock that ran up hard for 60 bars then went sideways for 5 bars
    should get a momentum penalty — that's the bagholder signal."""
    n = 260
    rng = np.random.default_rng(42)
    trend = np.linspace(100, 180, 255)
    flat = np.full(5, 180.0)
    close = np.concatenate([trend, flat]) + rng.normal(0, 0.3, n)
    df = pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close,
        "Volume": np.concatenate([rng.integers(3_000_000, 5_000_000, 255),
                                  rng.integers(800_000, 1_200_000, 5)]),
    }, index=pd.bdate_range("2023-01-02", periods=n))

    from nse_backtest.scorer import score_momentum
    s, reasons = score_momentum(df)
    assert any("Momentum decaying" in r for r in reasons), (
        f"expected decay reason; got reasons={reasons}"
    )


def test_momentum_decay_does_not_fire_on_clean_uptrend():
    """A still-accelerating uptrend must NOT get the penalty."""
    n = 260
    rng = np.random.default_rng(11)
    close = np.linspace(100, 200, n) + rng.normal(0, 0.3, n)
    df = pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close,
        "Volume": rng.integers(3_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))

    from nse_backtest.scorer import score_momentum
    s, reasons = score_momentum(df)
    assert not any("Momentum decaying" in r for r in reasons)
