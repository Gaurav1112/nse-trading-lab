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
    assert c.ticket == ticket_lines(PLAN, replace(SIZING, qty=40, notional=4_000.0), NSE)


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
