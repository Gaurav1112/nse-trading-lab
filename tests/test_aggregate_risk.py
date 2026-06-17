"""Tests for aggregate book risk budget + portfolio kill switch."""
from __future__ import annotations

import pytest

from components.risk_governor import (
    aggregate_open_risk_pct, aggregate_risk_cap_pct,
    portfolio_kill_switch, assess,
)


def _pos(sym: str, buy: float, sl: float, qty: int, closed: bool = False) -> dict:
    p = {"symbol": sym, "buy_price": buy, "stop_loss": sl, "qty": qty,
         "invested": buy * qty}
    if closed:
        p["closed_date"] = "2026-06-01"
    return p


def test_aggregate_risk_pct_sums_only_open_positions():
    positions = [
        _pos("RELIANCE", 1300, 1200, 10),   # ₹1000 risk
        _pos("TCS", 4000, 3800, 5, closed=True),  # ignored
        _pos("INFY", 1500, 1430, 14),       # ₹980 risk
    ]
    pct = aggregate_open_risk_pct(positions, capital=100_000)
    # Total risk = 1000 + 980 = 1980 → 1.98% of 100k
    assert pct == pytest.approx(1.98, abs=0.01)


def test_aggregate_risk_pct_zero_capital_safe():
    positions = [_pos("RELIANCE", 1300, 1200, 10)]
    assert aggregate_open_risk_pct(positions, capital=0) == 0.0


def test_aggregate_risk_pct_skips_invalid_levels():
    positions = [
        _pos("BAD1", 0, 1200, 10),         # zero buy
        _pos("BAD2", 1300, 0, 10),         # zero SL
        _pos("BAD3", 1300, 1400, 10),      # SL above buy (invalid)
        _pos("GOOD", 1300, 1200, 10),
    ]
    pct = aggregate_open_risk_pct(positions, capital=100_000)
    assert pct == pytest.approx(1.0, abs=0.01)


def test_aggregate_cap_per_regime():
    assert aggregate_risk_cap_pct("TRENDING") == 6.0
    assert aggregate_risk_cap_pct("MIXED") == 3.0
    assert aggregate_risk_cap_pct("HOSTILE") == 1.0
    assert aggregate_risk_cap_pct(None) == 6.0  # unknown defaults to permissive


def test_assess_blocks_when_aggregate_over_hostile_cap():
    positions = [
        _pos("A", 1000, 950, 10),  # ₹500 risk
        _pos("B", 1000, 950, 10),  # ₹500 risk
        _pos("C", 1000, 950, 10),  # ₹500 risk — total 1500 = 1.5% of 100k > 1.0% HOSTILE cap
    ]
    v = assess(positions, journal=[], capital=100_000, regime="HOSTILE")
    assert not v.can_trade
    assert any("Aggregate book risk" in r for r in v.reasons)


def test_assess_permits_when_under_trending_cap():
    positions = [
        _pos("A", 1000, 950, 10),
        _pos("B", 1000, 950, 10),
    ]
    v = assess(positions, journal=[], capital=100_000, regime="TRENDING")
    # 1% aggregate, well under 6% TRENDING cap
    assert v.can_trade
    assert v.aggregate_risk_pct == pytest.approx(1.0, abs=0.01)
    assert v.aggregate_risk_cap_pct == 6.0


def test_portfolio_kill_switch_fires_on_8pct_drawdown():
    journal = [
        {"closed_date": "2026-06-01", "pnl": +5000},
        {"closed_date": "2026-06-02", "pnl": +3000},  # HWM = +8000
        {"closed_date": "2026-06-03", "pnl": -4000},
        {"closed_date": "2026-06-04", "pnl": -5000},  # cum = -1000, DD from HWM = 9000 = 9%
    ]
    fire, reason = portfolio_kill_switch(journal, capital=100_000)
    assert fire
    assert "kill-switch" in reason or "kill switch" in reason.lower() or "≥" in reason


def test_portfolio_kill_switch_quiet_under_threshold():
    journal = [
        {"closed_date": "2026-06-01", "pnl": +5000},
        {"closed_date": "2026-06-02", "pnl": -3000},  # 3% drawdown only
    ]
    fire, reason = portfolio_kill_switch(journal, capital=100_000)
    assert not fire


def test_kill_switch_sets_flatten_flag_on_verdict():
    journal = [
        {"closed_date": "2026-06-01", "pnl": +10000},
        {"closed_date": "2026-06-02", "pnl": -10000},  # 10% drawdown from HWM
    ]
    v = assess(positions=[], journal=journal, capital=100_000, regime="TRENDING")
    assert v.flatten_all
    assert v.flatten_reason
    assert not v.can_trade
