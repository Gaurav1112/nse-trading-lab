from nse_backtest.screener import run_screener


def test_screener_returns_list(trending_ohlcv):
    out = run_screener({"TEST": trending_ohlcv}, top_n=5)
    assert isinstance(out, list)
    for setup in out:
        assert setup.risk_reward >= 1.5
        assert setup.stop_loss < setup.current_price


def test_screener_short_data_skipped(flat_ohlcv):
    short = flat_ohlcv.iloc[:30]
    out = run_screener({"SHORT": short}, top_n=5)
    assert out == []
