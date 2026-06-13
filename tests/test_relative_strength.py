import pandas as pd
import numpy as np
from nse_backtest.features.relative_strength import rs_vs_nifty_boost


def _df_with_returns(start_price=100.0, returns_pct=None, n=120):
    """Build OHLCV where Close grows by the given list of daily returns_pct."""
    if returns_pct is None:
        returns_pct = [0.005] * n
    n = len(returns_pct)
    prices = [start_price]
    for r in returns_pct:
        prices.append(prices[-1] * (1 + r))
    close = np.array(prices[1:])
    return pd.DataFrame({
        "Open": close, "High": close * 1.005, "Low": close * 0.995,
        "Close": close, "Volume": np.full(n, 1_000_000),
    }, index=pd.bdate_range("2024-01-02", periods=n))


def test_outperforming_stock_gets_boost():
    """Stock returns 1%/day, Nifty returns 0.2%/day → outperforms by >5% on 20d and 60d → +10."""
    stock = _df_with_returns(returns_pct=[0.010] * 120)
    nifty = _df_with_returns(returns_pct=[0.002] * 120)
    boost, reason = rs_vs_nifty_boost(stock, nifty)
    assert boost == 10
    assert "outperform" in reason.lower() or "rs" in reason.lower()


def test_in_line_stock_gets_no_boost():
    """Stock and Nifty both return 0.3%/day → underperform threshold → 0 boost."""
    stock = _df_with_returns(returns_pct=[0.003] * 120)
    nifty = _df_with_returns(returns_pct=[0.003] * 120)
    boost, _ = rs_vs_nifty_boost(stock, nifty)
    assert boost == 0


def test_underperforming_stock_gets_no_boost_and_negative_reason():
    """Stock underperforms Nifty → 0 boost (we don't penalize, just no add)."""
    stock = _df_with_returns(returns_pct=[0.001] * 120)
    nifty = _df_with_returns(returns_pct=[0.005] * 120)
    boost, reason = rs_vs_nifty_boost(stock, nifty)
    assert boost == 0
    assert "underperform" in reason.lower() or "lag" in reason.lower()


def test_missing_nifty_data_returns_zero_boost():
    """If nifty_df has too few bars, return 0 boost and a warning reason — don't crash."""
    stock = _df_with_returns(n=120)
    nifty = _df_with_returns(n=10)
    boost, reason = rs_vs_nifty_boost(stock, nifty)
    assert boost == 0
    assert "insufficient" in reason.lower() or "unavailable" in reason.lower()


def test_v2_engine_boost_applied_via_analyze_swing(monkeypatch):
    """When NSE_SCORER_ENGINE=v2 and nifty_df is provided, the stock score should
    be at least as high as the v1 score when outperforming."""
    from nse_backtest.trading_modes import analyze_swing

    stock = _df_with_returns(returns_pct=[0.010] * 260)
    nifty = _df_with_returns(returns_pct=[0.002] * 260)

    monkeypatch.setenv("NSE_SCORER_ENGINE", "v1")
    v1 = analyze_swing(stock, "RS_TEST", nifty_df=nifty)

    monkeypatch.setenv("NSE_SCORER_ENGINE", "v2")
    v2 = analyze_swing(stock, "RS_TEST", nifty_df=nifty)

    assert v2.score >= v1.score, f"v2 ({v2.score}) should be >= v1 ({v1.score}) for outperformer"
