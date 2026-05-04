"""Engine semantics + cost-model tests."""
import pytest

from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.strategies import sma_crossover, STRATEGIES
supertrend_strategy = STRATEGIES["supertrend"]


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        TradeConfig(trading_mode="MARGIN_FUNDING")


def test_validates_required_cols(trending_ohlcv):
    df = trending_ohlcv.copy().drop(columns=["Volume"])
    df["signal"] = 0
    df["strategy_name"] = "x"
    with pytest.raises(ValueError):
        run_backtest(df, TradeConfig())


def test_engine_runs_and_returns_keys(trending_ohlcv):
    sd = sma_crossover(trending_ohlcv)
    res = run_backtest(sd, TradeConfig(initial_capital=100_000))
    for k in ("trades", "equity_curve", "buy_hold_curve", "config"):
        assert k in res
    assert len(res["equity_curve"]) == len(trending_ohlcv)


def test_costs_non_negative(trending_ohlcv):
    sd = supertrend_strategy(trending_ohlcv)
    res = run_backtest(sd, TradeConfig(initial_capital=100_000))
    for t in res["trades"]:
        assert t.costs >= 0
        assert t.shares >= 1


def test_intraday_mode_uses_intraday_costs(trending_ohlcv):
    """INTRADAY mode must produce different per-trade cost than DELIVERY for the same trades."""
    sd = sma_crossover(trending_ohlcv)
    delivery = run_backtest(sd, TradeConfig(initial_capital=100_000, trading_mode="DELIVERY"))
    intraday = run_backtest(sd, TradeConfig(initial_capital=100_000, trading_mode="INTRADAY"))
    # Same signals, same fills, but cost structure differs (no DP charge intraday, lower STT, etc.)
    if delivery["trades"] and intraday["trades"]:
        assert delivery["trades"][0].costs != intraday["trades"][0].costs


def test_mtf_mode_accrues_interest(trending_ohlcv):
    sd = sma_crossover(trending_ohlcv)
    res = run_backtest(sd, TradeConfig(initial_capital=100_000, trading_mode="MTF"))
    has_interest = any(t.interest > 0 for t in res["trades"] if t.exit_date is not None)
    if res["trades"]:
        assert has_interest, "MTF mode must accrue interest on at least one closed trade"


def test_stop_loss_caps_individual_loss(trending_ohlcv):
    """A 1% SL must keep per-trade losses bounded by SL + slippage + gap."""
    sd = sma_crossover(trending_ohlcv)
    res = run_backtest(sd, TradeConfig(initial_capital=100_000, stop_loss_pct=0.01))
    losses = [t.pnl_pct for t in res["trades"] if t.pnl_pct < 0]
    if losses:
        assert min(losses) > -0.10
