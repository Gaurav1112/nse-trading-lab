"""Tests for indicator NaN-safety and basic correctness."""
import numpy as np
import pandas as pd

from nse_backtest.indicators import (
    ichimoku, keltner_channels, donchian_channels,
    parabolic_sar, detect_market_regime, cci_indicator,
)


def test_ichimoku_returns_safe_dict(trending_ohlcv):
    out = ichimoku(trending_ohlcv)
    assert "above_cloud" in out and isinstance(out["above_cloud"], (bool, np.bool_))
    assert "tenkan" in out and out["tenkan"] > 0
    assert "kijun" in out and out["kijun"] > 0


def test_ichimoku_handles_short_data():
    df = pd.DataFrame({
        "Open": [100, 101, 102],
        "High": [101, 102, 103],
        "Low":  [99, 100, 101],
        "Close":[100, 101, 102],
        "Volume":[1000, 1000, 1000],
    }, index=pd.bdate_range("2024-01-01", periods=3))
    out = ichimoku(df)
    assert out["above_cloud"] in (True, False)


def test_keltner_no_div_by_zero(flat_ohlcv):
    out = keltner_channels(flat_ohlcv)
    assert "width_pct" in out
    assert np.isfinite(out["width_pct"]) or out["width_pct"] == 0


def test_donchian_basic(trending_ohlcv):
    out = donchian_channels(trending_ohlcv)
    assert out["upper"] >= out["lower"]
    assert "width_pct" in out and np.isfinite(out["width_pct"])


def test_psar_returns_bool(trending_ohlcv):
    out = parabolic_sar(trending_ohlcv)
    assert out["bullish"] in (True, False)
    assert out["sar"] > 0


def test_market_regime_classifies(trending_ohlcv):
    out = detect_market_regime(trending_ohlcv)
    assert out["regime"] in {"TRENDING_UP", "TRENDING_DOWN", "VOLATILE", "RANGING", "UNKNOWN"}


def test_cci_in_range(trending_ohlcv):
    out = cci_indicator(trending_ohlcv)
    assert "cci" in out and np.isfinite(out["cci"])
