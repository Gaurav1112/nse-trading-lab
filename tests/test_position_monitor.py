import pandas as pd
import numpy as np
import pytest
from nse_backtest.position_monitor import daily_check, ReScoreVerdict, ReScoreAction


def _strong_uptrend_df(n=260):
    rng = np.random.default_rng(3)
    base = np.linspace(100, 180, n) + rng.normal(0, 0.4, n)
    return pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Volume": rng.integers(2_000_000, 5_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def _flat_then_drop_df(n=260):
    rng = np.random.default_rng(4)
    up = np.linspace(100, 150, 200)
    drop = np.linspace(150, 120, 60)
    close = np.concatenate([up, drop]) + rng.normal(0, 0.3, n)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": rng.integers(1_000_000, 3_000_000, n),
    }, index=pd.bdate_range("2023-01-02", periods=n))


def test_hold_verdict_on_strong_uptrend():
    df = _strong_uptrend_df()
    position = {
        "symbol": "DEMO_UP", "buy_price": 130.0, "qty": 100,
        "stop_loss": 120.0, "target": 160.0,
        "entry_date": (df.index[-30]).strftime("%Y-%m-%d"),
    }
    verdict = daily_check(position, df)
    assert isinstance(verdict, ReScoreVerdict)
    assert verdict.action in (ReScoreAction.HOLD, ReScoreAction.TIGHTEN_STOP)
    assert 0 <= verdict.current_rescore <= 100
    assert verdict.bars_held > 0


def test_exit_verdict_on_decayed_position():
    df = _flat_then_drop_df()
    position = {
        "symbol": "DEMO_DOWN", "buy_price": 148.0, "qty": 100,
        "stop_loss": 140.0, "target": 165.0,
        "entry_date": (df.index[-50]).strftime("%Y-%m-%d"),
    }
    verdict = daily_check(position, df)
    assert verdict.action == ReScoreAction.EXIT
    assert "decay" in verdict.reason.lower() or "time-stop" in verdict.reason.lower() or "below entry" in verdict.reason.lower()


def test_missing_entry_date_is_treated_as_today():
    """If a saved position lacks entry_date, treat bars_held=0 and never time-stop."""
    df = _strong_uptrend_df()
    position = {"symbol": "X", "buy_price": 130.0, "qty": 100,
                "stop_loss": 120.0, "target": 160.0}
    verdict = daily_check(position, df)
    assert verdict.bars_held == 0
    assert verdict.action != ReScoreAction.EXIT or "time-stop" not in verdict.reason.lower()
