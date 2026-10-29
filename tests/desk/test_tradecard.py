from dataclasses import replace

import pytest

from nse_backtest.desk.costs import round_trip_cost
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action, Check, DeskVerdict, Limits, Mode, Odds, Sizing, Status, TradePlan
from nse_backtest.desk.tradecard import build_card
from nse_backtest.desk.verdict import ticket_lines

PLAN = TradePlan("ABC", "NSE", Mode.SWING, 100, 95, 110, 115, 2, 15, 100, "2026-10-28T00:00:00", "user")
SIZING = Sizing(10, "risk", 50.0, 1_000.0, 5.0, False, "10 shares")
ODDS = Odds(500, 19, 0.4, 0.4, 0.2, 0.3, 0, None, None, (), None, None, None, None, None, None, None)
LIMITS = Limits(capital=10_000)
OK = (Check("regime", Status.PASS, ""),)


def verdict(action=Action.TAKE, odds=ODDS, checks=OK, plan=PLAN, sizing=SIZING):
    ticket = ticket_lines(plan, sizing, NSE) if action is Action.TAKE else ()
    return DeskVerdict(action, "h", plan, sizing, odds, checks, ticket, None, 100.0, "x" * 64, "2026-10-29T10:00:00")


def test_money_math_take():
    c = build_card(verdict(), NSE, LIMITS)
    cost1 = round_trip_cost(NSE, Mode.SWING, 100, 110, 10)
    cost2 = round_trip_cost(NSE, Mode.SWING, 100, 115, 10)
    assert (c.qty, c.qty_is_override, c.risk_per_share) == (10, False, 5)
    assert c.cost == pytest.approx(cost1)
    assert c.max_loss == pytest.approx(50 + cost1)
    assert c.profit_t1 == pytest.approx(100 - cost1)
    assert c.profit_t2 == pytest.approx(150 - cost2)
    assert c.rr_t1 == 2.0
    assert c.expected_value == pytest.approx(0.3 * 50)
    assert c.notional == 1_000 and c.pct_capital_deployed == pytest.approx(10.0)
    assert c.pct_capital_at_risk == pytest.approx(0.5)
    assert c.breaches == () and c.reasons == ()
    assert c.ticket == ticket_lines(PLAN, SIZING, NSE) and c.ticket_header == ""
    assert "gap" in c.gap_note and c.entry_limit is None


def test_no_odds_means_no_expected_value():
    assert build_card(verdict(odds=None), NSE, LIMITS).expected_value is None


def test_override_qty_breaches():
    c = build_card(verdict(), NSE, LIMITS, qty=40)
    assert c.qty == 40 and c.qty_is_override
    assert "risk per trade 2.0% of capital (limit 1.0%)" in c.breaches
    assert "position 40.0% of capital (limit 25.0%)" in c.breaches
    assert any("exceeds the daily loss limit (2.0%)" in b for b in c.breaches)
    assert c.sizing.qty == 40 and c.ticket[0] == "BUY 40 ABC CNC LIMIT 100.00"


def test_override_within_limits_has_no_breach():
    assert build_card(verdict(), NSE, LIMITS, qty=15).breaches == ()


@pytest.mark.parametrize("bad", [0, -1, 2.5])
def test_bad_qty_rejected(bad):
    with pytest.raises(ValueError):
        build_card(verdict(), NSE, LIMITS, qty=bad)


def test_skip_is_gated_until_override_confirmed():
    checks = OK + (Check("gap", Status.BLOCK, "too wide"), Check("liquidity", Status.WARN, "thin"))
    v = verdict(Action.SKIP, checks=checks)
    c = build_card(v, NSE, LIMITS)
    assert c.ticket == () and c.ticket_header == ""
    assert c.reasons == ("BLOCK gap — too wide", "WARN liquidity — thin")
    c2 = build_card(v, NSE, LIMITS, override_confirmed=True)
    assert c2.ticket == ticket_lines(PLAN, SIZING, NSE)
    assert c2.ticket_header == "YOUR OVERRIDE — the desk said SKIP"


def test_intraday_entry_limit_copied():
    plan = replace(PLAN, mode=Mode.INTRADAY, entry_limit=100.5)
    assert build_card(verdict(plan=plan), NSE, LIMITS).entry_limit == 100.5


ZERO = Sizing(0, "risk", 0.0, 0.0, 0.0, False, "0 shares: stop too wide")


def test_zero_qty_card_has_no_money_and_no_ticket():
    v = verdict(Action.SKIP, checks=OK + (Check("gap", Status.BLOCK, "too wide"),), sizing=ZERO)
    for confirmed in (False, True):
        c = build_card(v, NSE, LIMITS, override_confirmed=confirmed)
        assert c.qty == 0 and c.ticket == () and c.ticket_header == ""
        for f in ("cost", "max_loss", "profit_t1", "profit_t2", "expected_value", "notional",
                  "pct_capital_at_risk", "pct_capital_deployed"):
            assert getattr(c, f) is None, f
        assert c.risk_per_share == 5 and c.rr_t1 == 2.0
        assert c.unavailable_reason == ("The desk sized 0 shares (0 shares: stop too wide) — "
                                        "enter a quantity to see the money")
    loss, p1, p2 = c.per_share
    assert loss == pytest.approx(5 + round_trip_cost(NSE, Mode.SWING, 100, 110, 1))
    assert p1 == pytest.approx(10 - round_trip_cost(NSE, Mode.SWING, 100, 110, 1))
    assert p2 == pytest.approx(15 - round_trip_cost(NSE, Mode.SWING, 100, 115, 1))


def test_zero_sized_skip_with_qty_gets_full_money_and_override_ticket():
    v = verdict(Action.SKIP, sizing=ZERO)
    c = build_card(v, NSE, LIMITS, qty=5, override_confirmed=True)
    assert c.max_loss == pytest.approx(25 + round_trip_cost(NSE, Mode.SWING, 100, 110, 5))
    assert c.unavailable_reason == "" and c.qty_is_override
    assert c.ticket[0] == "BUY 5 ABC CNC LIMIT 100.00"
    assert c.ticket_header == "YOUR OVERRIDE — the desk said SKIP"


def test_override_sizing_is_rebuilt_honestly():
    c = build_card(verdict(), NSE, LIMITS, qty=40)
    z = c.sizing
    assert z.qty == 40 and z.risk_amount == 40 * 5 and z.notional == 40 * 100
    assert z.costs == pytest.approx(round_trip_cost(NSE, Mode.SWING, 100, 110, 40))
    assert z.note == "40 shares (your quantity)"
    assert build_card(verdict(), NSE, LIMITS).sizing == SIZING  # not overridden: the desk's own


def test_wait_reasons_and_same_qty_not_override_and_take_override_ticket():
    w = build_card(verdict(Action.WAIT, checks=(Check("gap", Status.WARN, "wide"),)), NSE, LIMITS)
    assert w.reasons == ("WARN gap — wide",) and w.ticket == ()
    assert build_card(verdict(), NSE, LIMITS, qty=10).qty_is_override is False
    t = build_card(verdict(), NSE, LIMITS, qty=20)
    assert t.ticket[0] == "BUY 20 ABC CNC LIMIT 100.00" and t.ticket_header == ""


def test_entry_must_be_above_stop():
    with pytest.raises(ValueError, match="entry must be above stop"):
        build_card(verdict(plan=replace(PLAN, stop=100)), NSE, LIMITS)
    with pytest.raises(ValueError, match="entry must be above stop"):
        build_card(verdict(plan=replace(PLAN, stop=105)), NSE, LIMITS)
