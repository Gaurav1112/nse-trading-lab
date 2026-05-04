import pandas as pd

from nse_backtest.scorer import (
    analyze_stock, score_trend, score_momentum, score_volatility, score_volume, score_risk,
)


def test_score_dimensions_in_range(trending_ohlcv):
    for fn in (score_trend, score_momentum, score_volatility, score_volume):
        s, reasons = fn(trending_ohlcv)
        assert 0 <= s <= 100
        assert isinstance(reasons, list)
    s, reasons, levels = score_risk(trending_ohlcv)
    assert 0 <= s <= 100
    assert levels["stop_loss"] < trending_ohlcv["Close"].iloc[-1]
    assert levels["target_1"] > trending_ohlcv["Close"].iloc[-1]


def test_analyze_stock_full_pipeline(trending_ohlcv):
    out = analyze_stock(trending_ohlcv, "TEST", run_backtests=True)
    assert 0 <= out.final_score <= 100
    assert out.verdict in {"GO", "WAIT", "AVOID"}
    assert out.confidence in {"HIGH", "MEDIUM", "LOW"}
    assert out.stop_loss < out.current_price < out.target_1 < out.target_2


def test_zero_volume_does_not_crash(trending_ohlcv):
    df = trending_ohlcv.copy()
    df["Volume"] = 0
    s, _ = score_volume(df)
    assert 0 <= s <= 100


def test_constant_price_does_not_crash():
    n = 250
    df = pd.DataFrame({
        "Open": [100.0] * n, "High": [100.0] * n,
        "Low": [100.0] * n, "Close": [100.0] * n,
        "Volume": [1000] * n,
    }, index=pd.bdate_range("2024-01-01", periods=n))
    out = analyze_stock(df, "FLAT", run_backtests=False)
    assert 0 <= out.final_score <= 100
