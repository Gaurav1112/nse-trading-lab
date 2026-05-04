"""Round-2 hardening tests: MTF leverage, IPFT, ichimoku displacement, scorer regime."""
import numpy as np
import pandas as pd
import pytest

from nse_backtest.engine import run_backtest, TradeConfig, _buy_cost
from nse_backtest.strategies import sma_crossover
from nse_backtest.indicators import ichimoku


def test_ipft_charge_added_to_buy_cost():
    """IPFT (0.0001%) must be reflected in buy/sell costs."""
    cfg_no_ipft = TradeConfig(ipft_pct=0.0)
    cfg_with = TradeConfig(ipft_pct=0.000001)
    assert _buy_cost(1000, 100, cfg_with) > _buy_cost(1000, 100, cfg_no_ipft)


def test_mtf_uses_leverage_position_larger(trending_ohlcv):
    """MTF should buy more shares than DELIVERY for same capital (leverage 1/margin_pct)."""
    sd = sma_crossover(trending_ohlcv)
    res_d = run_backtest(sd.copy(), TradeConfig(initial_capital=100_000, trading_mode="DELIVERY"))
    res_m = run_backtest(sd.copy(), TradeConfig(initial_capital=100_000, trading_mode="MTF",
                                                mtf_margin_pct=0.25, mtf_interest_annual=0.18))
    # MTF leverage = 1/0.25 = 4x; positions should be substantially larger
    if res_d["trades"] and res_m["trades"]:
        max_d = max(t.shares for t in res_d["trades"])
        max_m = max(t.shares for t in res_m["trades"])
        assert max_m > max_d, f"MTF must leverage capital: delivery={max_d} mtf={max_m}"


def test_ichimoku_returns_chikou_and_shifted_senkou():
    """Ichimoku must include Chikou span and shift Senkou A/B by kijun (canonical)."""
    n = 200
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "Open": 100 + rng.normal(0, 1, n).cumsum(),
        "High": 102 + rng.normal(0, 1, n).cumsum(),
        "Low": 98 + rng.normal(0, 1, n).cumsum(),
        "Close": 100 + rng.normal(0, 1, n).cumsum(),
        "Volume": 1_000_000,
    }, index=pd.date_range("2024-01-01", periods=n, freq="B"))
    df["High"] = df[["Open", "High", "Close"]].max(axis=1)
    df["Low"] = df[["Open", "Low", "Close"]].min(axis=1)

    out = ichimoku(df)
    assert "chikou" in out
    # shifted senkou_a/b should match raw senkou values from kijun=26 bars ago
    high, low = df["High"], df["Low"]
    raw_a = ((high.rolling(9).max() + low.rolling(9).min()) / 2 +
             (high.rolling(26).max() + low.rolling(26).min()) / 2) / 2
    expected_sa = raw_a.shift(26).iloc[-1]
    if pd.notna(expected_sa) and pd.notna(out["senkou_a"]):
        assert abs(out["senkou_a"] - expected_sa) < 1e-9


def test_scorer_weights_sum_to_one():
    """Phase-4 weights must sum to 1.0; assertion guards regressions."""
    from nse_backtest.scorer import analyze_stock
    n = 250
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "Open": 100 + rng.normal(0, 1, n).cumsum(),
        "High": 0.0, "Low": 0.0,
        "Close": 100 + rng.normal(0, 1, n).cumsum(),
        "Volume": 1_000_000,
    }, index=pd.date_range("2023-01-01", periods=n, freq="B"))
    df["High"] = df[["Open", "Close"]].max(axis=1) + 1
    df["Low"] = df[["Open", "Close"]].min(axis=1) - 1
    # Should not raise an AssertionError on weights
    analyze_stock(df, symbol="TEST", run_backtests=False)
