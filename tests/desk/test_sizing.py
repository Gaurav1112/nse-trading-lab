import pytest
from nse_backtest.desk.sizing import size_position
from nse_backtest.desk.models import Limits, Mode, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.costs import round_trip_cost


def plan(entry=100.0, stop=98.0, mode=Mode.SWING, market="NSE"):
    return TradePlan("X", market, mode, entry, stop, entry + 1.5 * (entry - stop), entry + 3 * (entry - stop),
                     2.0, 15, entry, "2026-10-28", "user")


def test_position_cap_binds():
    s = size_position(plan(), Limits(capital=100_000), NSE)
    assert s.qty == 250 and s.binding_limit == "position cap"
    assert s.risk_amount == pytest.approx(250 * 2 + round_trip_cost(NSE, Mode.SWING, 100, 98, 250))
    assert s.risk_amount <= 1000


def test_max_loss_binds_and_is_tight():
    s = size_position(plan(), Limits(capital=100_000, max_position_pct=100, cash_buffer_pct=0), NSE)
    q = s.qty
    assert s.binding_limit == "max loss per trade" and q > 0
    assert q * 2 + round_trip_cost(NSE, Mode.SWING, 100, 98, q) <= 1000
    assert (q + 1) * 2 + round_trip_cost(NSE, Mode.SWING, 100, 98, q + 1) > 1000


def test_stop_too_wide_refuses():
    s = size_position(plan(stop=1.0), Limits(capital=5_000), NSE)
    assert s.qty == 0 and "too wide" in s.note and s.binding_limit == "max loss per trade"
    assert s.risk_amount == 0.0 and s.notional == 0.0 and s.costs == 0.0


def test_halve_halves_budget():
    full = size_position(plan(), Limits(capital=100_000, max_position_pct=100, cash_buffer_pct=0), NSE)
    half = size_position(plan(), Limits(capital=100_000, max_position_pct=100, cash_buffer_pct=0), NSE, halve=True)
    assert half.size_halved and half.qty < full.qty and half.risk_amount <= 500
    assert "size halved" in half.note


def test_us_market():
    s = size_position(plan(entry=50, stop=49, market="US"), Limits(capital=10_000, max_position_pct=100), US, spread_per_side=0.0003)
    assert s.qty > 0 and s.risk_amount <= 100


def test_tie_reports_earlier_limit():
    # risk R=1 so qty_risk is ~budget; make all three limits give the same qty.
    # capital 100_000, entry 100: qty_pos = 100_000*p/100/100, qty_cash = 100_000*(1-b/100)/100.
    # position cap 50% and cash buffer 50% both -> 500; risk budget huge (R tiny) so risk is not the min.
    s = size_position(plan(entry=100, stop=99.99), Limits(capital=100_000, max_position_pct=50, cash_buffer_pct=50), NSE)
    assert s.qty == 500 and s.binding_limit == "position cap"


@pytest.mark.parametrize("kw", [
    dict(limits=Limits(capital=0)), dict(limits=Limits(capital=-5)),
])
def test_bad_capital_raises(kw):
    with pytest.raises(ValueError):
        size_position(plan(), kw["limits"], NSE)


@pytest.mark.parametrize("entry,stop", [(100, 100), (100, 101), (float("nan"), 98), (100, float("inf"))])
def test_bad_prices_raise(entry, stop):
    with pytest.raises(ValueError):
        size_position(plan(entry=entry, stop=stop), Limits(capital=100_000), NSE)
