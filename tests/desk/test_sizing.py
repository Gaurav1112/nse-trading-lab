import math
from dataclasses import replace

import pytest
from nse_backtest.desk.sizing import size_position
from nse_backtest.desk.models import Limits, Mode, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.costs import round_trip_cost
from nse_backtest.desk import fills


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


def test_price_cap_refusal_names_the_real_limit():
    s = size_position(plan(entry=5000, stop=4990), Limits(capital=10_000), NSE)
    assert s.qty == 0 and s.binding_limit == "position cap"
    assert "Price too high" in s.note
    assert "Stop too wide" not in s.note


@pytest.mark.parametrize("kw", [
    dict(limits=Limits(capital=0)), dict(limits=Limits(capital=-5)),
    dict(limits=Limits(capital=float("inf"))), dict(limits=Limits(capital=float("nan"))),
])
def test_bad_capital_raises(kw):
    with pytest.raises(ValueError):
        size_position(plan(), kw["limits"], NSE)


@pytest.mark.parametrize("entry,stop", [(100, 100), (100, 101), (float("nan"), 98), (100, float("inf"))])
def test_bad_prices_raise(entry, stop):
    with pytest.raises(ValueError):
        size_position(plan(entry=entry, stop=stop), Limits(capital=100_000), NSE)


@pytest.mark.parametrize("entry,stop", [(100, 0), (100, -5), (1, -1)])
def test_nonpositive_stop_raises(entry, stop):
    with pytest.raises(ValueError):
        size_position(plan(entry=entry, stop=stop), Limits(capital=100_000), NSE)


def _linear_risk_qty(profile, mode, entry, stop, budget, spread=0.00075):
    # The brief's linear rule, transcribed literally; F1 A1: intraday risks up to the stop-limit's limit.
    if mode is Mode.INTRADAY:
        entry = fills.entry_limit(entry, stop, profile.tick)
    R = entry - stop
    q = math.floor(budget / R)
    while q > 0 and q * R + round_trip_cost(profile, mode, entry, stop, q, spread) > budget:
        q -= 1
    return q


_GRID = [
    (profile, mode, entry, d, capital)
    for profile, mode in [(NSE, Mode.SWING), (NSE, Mode.INTRADAY), (US, Mode.SWING)]
    for entry in [37.5, 100, 2450]
    for d in [0.3, 2, 40]
    for capital in [5_000, 100_000, 2_500_000]
    if entry - d > 0
]


@pytest.mark.parametrize("profile,mode,entry,d,capital", _GRID)
def test_binary_search_equals_linear_rule(profile, mode, entry, d, capital):
    stop = entry - d
    budget = capital * 1.0 / 100  # risk_per_trade_pct default
    expected_risk = _linear_risk_qty(profile, mode, entry, stop, budget)
    # With 100% position cap and 0% cash buffer, only the risk limit can differ from the cap.
    expected = min(expected_risk, math.floor(capital / entry))
    s = size_position(plan(entry=entry, stop=stop, mode=mode, market=profile.code),
                      Limits(capital=capital, max_position_pct=100, cash_buffer_pct=0), profile)
    assert s.qty == expected


# ---- H5a: shorts size by the worst fill (the sell limit below the trigger) ----
from nse_backtest.desk.costs import side_cost  # noqa: E402
from nse_backtest.desk.models import Side  # noqa: E402
from nse_backtest.desk.sizing import loss_at_stop, worst_entry, worst_risk  # noqa: E402

# Fictional short: sell trigger 100, sell limit 99.50, stop 102, T1 97, T2 94.
SHORT = TradePlan("X", "NSE", Mode.INTRADAY, 100.0, 102.0, 97.0, 94.0, 1.0, 18, 100.0, "2026-10-29T09:30:00",
                  "opening-range mid", 99.5, Side.SHORT)
WIDE = Limits(capital=100_000, max_position_pct=100.0)  # so the risk budget binds


def test_short_side_cost_buys_at_the_exit_and_sells_at_the_entry():
    # a short sells first (STT on the sell = the entry) and buys back at the exit (stamp on the buy)
    assert side_cost(NSE, Mode.INTRADAY, 100.0, 102.0, 10, side=Side.SHORT) == \
        pytest.approx(round_trip_cost(NSE, Mode.INTRADAY, 102.0, 100.0, 10))
    assert side_cost(NSE, Mode.INTRADAY, 100.0, 102.0, 10) == \
        pytest.approx(round_trip_cost(NSE, Mode.INTRADAY, 100.0, 102.0, 10))


def test_short_worst_fill_and_worst_risk():
    assert worst_entry(SHORT, NSE) == 99.5
    assert worst_risk(SHORT, NSE) == pytest.approx(2.5)  # stop - entry_limit
    assert worst_risk(replace(SHORT, entry_limit=None), NSE) == pytest.approx(2.5)  # computed 99.50


def test_short_sizing_hand_computed():
    # Per share at the worst fill (sell 99.50, buy back 102): risk 2.50 + cost 0.25771432
    #   brokerage 0.0003*(102+99.5) = 0.06045, STT 0.00025*99.5 = 0.024875, stamp 0.00003*102 = 0.00306,
    #   txn 201.5*0.0000308 = 0.0062062, GST 0.18*(0.06045+0.0062062) = 0.01199812, spread 201.5*0.00075 = 0.151125
    # budget 1% of 1,00,000 = 1,000 -> floor(1000 / 2.75771432) = 362 shares, loss 362 * 2.75771432 = 998.29
    s = size_position(SHORT, WIDE, NSE)
    assert s.qty == 362 and s.binding_limit == "max loss per trade"
    assert s.risk_amount == pytest.approx(998.2926, abs=0.01)
    assert s.costs == pytest.approx(93.2926, abs=0.01)
    assert s.notional == pytest.approx(36_200.0)
    assert loss_at_stop(SHORT, NSE, 362) == pytest.approx(998.2926, abs=0.01)
    assert loss_at_stop(SHORT, NSE, 362, entry=100.0) == pytest.approx(817.5442, abs=0.01)  # at the trigger


def test_short_sizing_rejects_a_stop_below_entry():
    with pytest.raises(ValueError, match="stop above entry"):
        size_position(replace(SHORT, stop=99.0), WIDE, NSE)


def test_long_sizing_unchanged_by_side():
    p = plan(mode=Mode.INTRADAY)
    assert worst_risk(p, NSE) == pytest.approx(fills.entry_limit(100.0, 98.0, NSE.tick) - 98.0)
