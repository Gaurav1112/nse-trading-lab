"""Tests for the P&L snapshot tracker."""
from __future__ import annotations

from datetime import datetime, timedelta

from components.pnl_tracker import snapshot


def _trade(d: str, ret_pct: float, pnl: float = 0.0) -> dict:
    return {"closed_date": d, "net_return_pct": ret_pct, "pnl": pnl}


def test_empty_journal_returns_zeros():
    s = snapshot([], capital=100_000)
    assert s.n_closed == 0
    assert s.win_rate_pct == 0
    assert s.expectancy_pct == 0
    assert s.cumulative_returns_pct == []
    assert any("No closed trades" in n for n in s.notes)


def test_single_winning_trade():
    s = snapshot([_trade("2026-05-01", 3.5, pnl=1750)], capital=100_000)
    assert s.n_closed == 1
    assert s.win_rate_pct == 100.0
    assert s.expectancy_pct == 3.5


def test_mixed_trades_compute_expectancy():
    j = [
        _trade("2026-05-01", 5.0, pnl=2500),
        _trade("2026-05-05", -2.5, pnl=-1250),
        _trade("2026-05-10", 4.0, pnl=2000),
    ]
    s = snapshot(j, capital=100_000)
    assert s.n_closed == 3
    assert s.win_rate_pct == 66.67
    assert abs(s.expectancy_pct - (5.0 - 2.5 + 4.0) / 3) < 0.01


def test_rolling_sharpe_present_when_enough_data():
    today = datetime.now()
    j = [_trade((today - timedelta(days=i)).strftime("%Y-%m-%d"), r, pnl=0)
         for i, r in enumerate([2, -1, 3, 1, -2, 2, 3, -1, 1, 2])]
    s = snapshot(j, capital=100_000)
    assert s.rolling_30d_sharpe != 0.0


def test_small_sample_warning():
    j = [_trade("2026-05-01", 1.0, pnl=500)]
    s = snapshot(j, capital=100_000)
    assert any("too small" in n.lower() for n in s.notes)


def test_large_drawdown_warning_fires():
    today = datetime.now().strftime("%Y-%m-%d")
    j = [_trade(today, -5.0, pnl=-5000)]  # -5% in last 30d on 100k capital
    s = snapshot(j, capital=100_000)
    assert any("review" in n.lower() for n in s.notes)
