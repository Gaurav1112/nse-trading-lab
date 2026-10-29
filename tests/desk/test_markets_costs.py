import pytest
from datetime import time
from nse_backtest.desk.markets import NSE, US, get_profile, universe, US_LARGE_CAP_50
from nse_backtest.desk.costs import round_trip_cost
from nse_backtest.desk.models import Mode, TradePlan
from nse_backtest.data import NIFTY50_SYMBOLS
from nse_backtest.picker_replay import _delivery_costs_pct


def test_get_profile_case_insensitive_and_rejects_unknown():
    assert get_profile("nse") is NSE and get_profile("US") is US
    with pytest.raises(ValueError):
        get_profile("LSE")


@pytest.mark.parametrize("prof,raw,want", [
    (NSE, "reliance", "RELIANCE.NS"), (NSE, "INFY.NS", "INFY.NS"),
    (NSE, "^NSEI", "^NSEI"), (US, "aapl", "AAPL"), (US, "BRK-B", "BRK-B"),
])
def test_yf_symbol(prof, raw, want):
    assert prof.yf_symbol(raw) == want


def test_money_uses_market_currency():
    assert NSE.money(1234.5) == "₹1,234.50"
    assert US.money(-3) == "-$3.00"


def test_sessions():
    assert NSE.session_open == time(9, 15) and US.flat_by == time(15, 55)


def test_universe():
    assert universe(NSE) == list(NIFTY50_SYMBOLS)
    assert len(US_LARGE_CAP_50) == 50 == len(set(US_LARGE_CAP_50))
    assert universe(US) == US_LARGE_CAP_50


def test_nse_swing_cost_matches_existing_delivery_model():
    got = round_trip_cost(NSE, Mode.SWING, 100.0, 104.0, 50, 0.00075)
    assert got == pytest.approx(_delivery_costs_pct(100.0, 104.0, 50, 0.00075) / 100 * 104.0 * 50)


def test_nse_intraday_cost_formula():
    buy, sell = 100.0 * 100, 101.0 * 100
    brk = min(20, 0.0003 * buy) + min(20, 0.0003 * sell)
    txn = (buy + sell) * (0.0000297 + 0.000001 + 0.0000001)
    want = brk + 0.00025 * sell + 0.00003 * buy + txn + 0.18 * (brk + txn) + (buy + sell) * 0.00075
    assert round_trip_cost(NSE, Mode.INTRADAY, 100.0, 101.0, 100) == pytest.approx(want)


def test_us_cost_formula_caps_taf():
    buy, sell = 50.0 * 100_000, 51.0 * 100_000
    want = 0.0000278 * sell + 8.30 + (buy + sell) * 0.0003
    assert round_trip_cost(US, Mode.SWING, 50.0, 51.0, 100_000, 0.0003) == pytest.approx(want)


def test_zero_qty_costs_nothing():
    assert round_trip_cost(NSE, Mode.SWING, 100, 100, 0) == 0.0


def test_tradeplan_properties():
    p = TradePlan("X", "NSE", Mode.SWING, 100, 98, 103, 106, 2, 15, 100, "2026-10-28", "user")
    assert p.risk_per_share == 2 and p.rr_t1 == 1.5



def test_d4_named_shared_constants():
    import inspect
    from nse_backtest.desk import costs, service, sizing, timemachine
    assert costs.DEFAULT_SPREAD == 0.00075
    assert inspect.signature(sizing.size_position).parameters["spread_per_side"].default is costs.DEFAULT_SPREAD
    assert inspect.signature(costs.round_trip_cost).parameters["spread_per_side"].default is costs.DEFAULT_SPREAD
    assert "DEFAULT_SPREAD" in inspect.getsource(service.check_trade) and "0.00075" not in inspect.getsource(service)
    assert timemachine._INTRADAY_LOOKBACK_BARS == 40 * 25
