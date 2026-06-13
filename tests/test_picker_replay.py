import pandas as pd
import numpy as np
import pytest
from nse_backtest.picker_replay import (
    replay_picker, simulate_trade, BacktestReport, TradeOutcome,
)


def _winning_df(n=300):
    """A clean uptrend that should hit T1 cleanly.

    Steeper slope (100→500) ensures T1 (+8% from late-window entry) is reachable
    within max_hold=15 bars — otherwise simulate_trade exits END_OF_REPLAY and the
    T1/TRAIL/T2 contract isn't exercised.
    """
    rng = np.random.default_rng(5)
    close = np.linspace(100, 500, n) + rng.normal(0, 0.5, n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": rng.integers(2_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def test_simulate_trade_records_t1_hit_and_trail_exit():
    df = _winning_df()
    # Enter early where remaining runway compounds enough to hit T1 in <=15 bars.
    # Late entry on a linspace gives shrinking % gains per bar and won't trip T1.
    entry_idx = 50
    entry_date = df.index[entry_idx]
    future = df.iloc[entry_idx:]
    outcome = simulate_trade(
        symbol="TEST",
        entry_date=entry_date,
        entry_price=float(future["Close"].iloc[0]),
        stop_loss=float(future["Close"].iloc[0]) * 0.95,
        target_1=float(future["Close"].iloc[0]) * 1.08,
        target_2=float(future["Close"].iloc[0]) * 1.15,
        atr=float(future["Close"].iloc[0]) * 0.02,
        future_data=future, max_hold=15,
    )
    assert isinstance(outcome, TradeOutcome)
    assert outcome.gross_return_pct > 0
    assert outcome.exit_reason in ("T1_PARTIAL_BE", "TRAIL_STOP", "TARGET_2")


def test_replay_picker_handles_empty_universe():
    """No symbols → empty report, not a crash."""
    report = replay_picker(symbol_data={}, start="2024-01-01", end="2024-03-01")
    assert isinstance(report, BacktestReport)
    assert report.total_trades == 0
    assert report.expectancy_pct == 0


def test_replay_picker_on_single_winning_symbol():
    """Replay one strong-uptrend symbol, expect at least one GO and a positive expectancy."""
    df = _winning_df()
    symbol_data = {"WIN": df}
    report = replay_picker(
        symbol_data=symbol_data,
        start=df.index[200].strftime("%Y-%m-%d"),
        end=df.index[270].strftime("%Y-%m-%d"),
        min_score=55, max_hold=10,
    )
    assert report.total_trades >= 1
    assert report.win_rate >= 0.5
