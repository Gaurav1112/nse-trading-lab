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
    # F1 A1: max loss uses the sizer's cost basis (exit at the stop), so headline == card == sizing
    assert c.max_loss == pytest.approx(50 + round_trip_cost(NSE, Mode.SWING, 100, 95, 10))
    assert c.profit_t1 == pytest.approx(100 - cost1)
    assert c.profit_t2 == pytest.approx(150 - cost2)
    assert c.rr_t1 == 2.0
    assert c.expected_value == pytest.approx(0.3 * 50)
    assert c.notional == 1_000 and c.pct_capital_deployed == pytest.approx(10.0)
    assert c.pct_capital_at_risk == pytest.approx(c.max_loss / 10_000 * 100)  # F1 A2: incl. costs
    assert c.breaches == () and c.reasons == ()
    assert c.ticket == ticket_lines(PLAN, SIZING, NSE) and c.ticket_header == ""
    assert "gap" in c.gap_note and c.entry_limit is None


def test_no_odds_means_no_expected_value():
    assert build_card(verdict(odds=None), NSE, LIMITS).expected_value is None


def test_override_qty_breaches():
    c = build_card(verdict(), NSE, LIMITS, qty=40)
    assert c.qty == 40 and c.qty_is_override
    assert "risk per trade 2.3% of capital (limit 1.0%)" in c.breaches  # F1 A2: max loss incl. costs
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
    assert loss == pytest.approx(5 + round_trip_cost(NSE, Mode.SWING, 100, 95, 1))  # F1 A1: the max loss basis
    assert p1 == pytest.approx(10 - round_trip_cost(NSE, Mode.SWING, 100, 110, 1))
    assert p2 == pytest.approx(15 - round_trip_cost(NSE, Mode.SWING, 100, 115, 1))


def test_zero_sized_skip_with_qty_gets_full_money_and_override_ticket():
    v = verdict(Action.SKIP, sizing=ZERO)
    c = build_card(v, NSE, LIMITS, qty=5, override_confirmed=True)
    assert c.max_loss == pytest.approx(25 + round_trip_cost(NSE, Mode.SWING, 100, 95, 5))
    assert c.unavailable_reason == "" and c.qty_is_override
    assert c.ticket[0] == "BUY 5 ABC CNC LIMIT 100.00"
    assert c.ticket_header == "YOUR OVERRIDE — the desk said SKIP"


def test_override_sizing_is_rebuilt_honestly():
    c = build_card(verdict(), NSE, LIMITS, qty=40)
    z = c.sizing
    assert z.qty == 40 and z.notional == 40 * 100
    assert z.risk_amount == pytest.approx(c.max_loss)  # F1 A1: the max loss incl. costs, as the sizer reports it
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


# ---------------------------------------------------------------- F1 A: worst-fill money honesty

from datetime import datetime as _dt  # noqa: E402

from nse_backtest.desk import fills as _fills  # noqa: E402
from nse_backtest.desk.markets import US  # noqa: E402
from nse_backtest.desk.sizing import size_position  # noqa: E402
from nse_backtest.desk.verdict import decide  # noqa: E402

_NOW = _dt(2026, 10, 29, 10, 0)


def _probe_plan(mode, entry=1000.0, stop=990.0, t1=1020.0, t2=1030.0, market="NSE", tick=0.05):
    lim = _fills.entry_limit(entry, stop, tick) if mode is Mode.INTRADAY else None
    return TradePlan("ABC", market, mode, entry, stop, t1, t2, 5, 15, 1000, "2026-10-28T00:00:00", "user", lim)


@pytest.mark.parametrize("profile", [NSE, US])
@pytest.mark.parametrize("geom", [(1000.0, 990.0, 1020.0, 1030.0), (1000.0, 950.0, 1100.0, 1150.0)])
def test_intraday_headline_equals_card_max_loss_at_the_limit_fill(profile, geom):
    """probe1/probe2: the headline, the card and n*(limit-stop)+cost (exit at the stop) are one number."""
    p = _probe_plan(Mode.INTRADAY, *geom, market=profile.code, tick=profile.tick)
    lim = Limits(capital=100_000)
    s = size_position(p, lim, profile)
    v = decide(p, s, None, OK, profile, p.entry, _NOW)
    assert v.action is Action.TAKE
    c = build_card(v, profile, lim)
    expect = s.qty * (p.entry_limit - p.stop) + round_trip_cost(profile, Mode.INTRADAY, p.entry_limit, p.stop, s.qty)
    assert c.max_loss == pytest.approx(expect)
    assert profile.money(expect) in v.headline
    assert s.risk_amount == pytest.approx(expect)
    assert c.max_loss <= 100_000 * 0.01 + 1e-9  # the desk's own TAKE stays inside the risk budget at the limit
    trig = s.qty * (p.entry - p.stop) + round_trip_cost(profile, Mode.INTRADAY, p.entry, p.stop, s.qty)
    assert c.max_loss_at_trigger == pytest.approx(trig) and c.max_loss_at_trigger < c.max_loss
    assert "(the order's limit), which is already in the max loss" in c.gap_note
    assert profile.money(p.entry_limit) in c.gap_note


def test_swing_card_max_loss_uses_the_stop_exit_cost_like_sizing():
    p = _probe_plan(Mode.SWING)
    lim = Limits(capital=100_000)
    s = size_position(p, lim, NSE)
    v = decide(p, s, None, OK, NSE, p.entry, _NOW)
    c = build_card(v, NSE, lim)
    assert c.max_loss == pytest.approx(s.risk_amount)
    assert NSE.money(s.risk_amount) in v.headline
    assert c.max_loss_at_trigger == pytest.approx(c.max_loss)
    assert "order's limit" not in c.gap_note


def test_max_loss_at_trigger_none_without_shares():
    assert build_card(verdict(Action.SKIP, sizing=ZERO), NSE, LIMITS).max_loss_at_trigger is None


@pytest.mark.parametrize("mode", [Mode.SWING, Mode.INTRADAY])
def test_risk_breach_includes_cost_one_share_over_the_sizer(mode):
    """A2: at the sized qty + 1 the risk-per-trade breach is listed (probe2: max loss over budget, no breach)."""
    p = _probe_plan(mode, 1000.0, 950.0, 1100.0, 1150.0)
    lim = Limits(capital=100_000)
    s = size_position(p, lim, NSE)
    assert s.binding_limit == "max loss per trade"
    v = decide(p, s, None, OK, NSE, p.entry, _NOW)
    over = build_card(v, NSE, lim, qty=s.qty + 1)
    assert over.max_loss > 1000
    assert any(b.startswith("risk per trade") for b in over.breaches), over.breaches
    assert not any(b.startswith("risk per trade") for b in build_card(v, NSE, lim, qty=s.qty).breaches)


def test_cli_money_block_shows_trigger_line_and_daily_loss_note():
    from nse_backtest.desk.cli import format_money
    p = _probe_plan(Mode.INTRADAY)
    lim = Limits(capital=100_000)
    s = size_position(p, lim, NSE)
    v = decide(p, s, None, OK, NSE, p.entry, _NOW)
    text = "\n".join(format_money(build_card(v, NSE, lim, qty=s.qty * 10), NSE))
    assert "max loss if filled at the trigger" in text and "already in the max loss" in text
    assert "compares this trade alone with the daily loss limit" in text
    swing = "\n".join(format_money(build_card(verdict(), NSE, LIMITS), NSE))
    assert "filled at the trigger" not in swing and "daily loss limit" not in swing


def test_a2_breach_text_never_reads_as_equal_to_the_limit():
    """probe2 at qty+1: 1.035% of capital must not print as "1.0% ... (limit 1.0%)"."""
    p = _probe_plan(Mode.INTRADAY, 1000.0, 950.0, 1100.0, 1150.0)
    lim = Limits(capital=100_000)
    s = size_position(p, lim, NSE)
    over = build_card(decide(p, s, None, OK, NSE, p.entry, _NOW), NSE, lim, qty=s.qty + 1)
    (risk,) = [b for b in over.breaches if b.startswith("risk per trade")]
    assert risk == f"risk per trade {over.max_loss / 1000:.2f}% of capital (limit 1.0%)"
    assert "1.0% of capital" not in risk


# ---------------------------------------------------------------- F1 round 2, I1: the sizer's own spread everywhere

@pytest.mark.parametrize("market", ["NSE", "US"])
@pytest.mark.parametrize("mode", [Mode.SWING, Mode.INTRADAY])
@pytest.mark.parametrize("spread", [0.0003, 0.00075, 0.0012])
def test_headline_card_and_sizer_share_the_sizers_spread(market, mode, spread):
    from nse_backtest.desk.markets import get_profile
    prof = get_profile(market)
    e = 1000.0 if market == "NSE" else 100.0
    p = _probe_plan(mode, e, e * 0.98, e * 1.04, e * 1.08, market=market, tick=prof.tick)
    lim = Limits(capital=1_000_000 if market == "NSE" else 10_000, max_position_pct=100, cash_buffer_pct=0)
    budget = lim.capital / 100
    s = size_position(p, lim, prof, spread_per_side=spread)
    assert s.spread_per_side == spread and s.binding_limit == "max loss per trade"
    v = decide(p, s, None, OK, prof, p.entry * 0.9995, _NOW)
    assert v.action is Action.TAKE
    c = build_card(v, prof, lim)
    assert c.max_loss == pytest.approx(s.risk_amount) and c.max_loss <= budget
    assert prof.money(s.risk_amount) in v.headline
    assert not any(b.startswith("risk per trade") for b in build_card(v, prof, lim, qty=s.qty).breaches)
    assert any(b.startswith("risk per trade") for b in build_card(v, prof, lim, qty=s.qty + 1).breaches)


# ---------------------------------------------------------------- round 2, I4: trigger-fill labels

def test_i4_fill_note_for_stop_limit_and_plain_limit_tickets():
    p = _probe_plan(Mode.INTRADAY)  # entry 1000, limit 1002.50
    lim = Limits(capital=100_000)
    s = size_position(p, lim, NSE)
    below = build_card(decide(p, s, None, OK, NSE, 999.5, _NOW), NSE, lim)  # stop-limit ticket (WAIT)
    assert below.fill_note == (f"assumes a fill at the trigger; up to {NSE.money(s.qty * 2.5)} less at the limit")
    at = build_card(decide(p, s, None, OK, NSE, 1001.0, _NOW), NSE, lim)  # plain LIMIT at the current price
    assert at.fill_note == (f"assumes a fill at the trigger; the ticket is a limit at the current price "
                            f"{NSE.money(1001.0)}, so about {NSE.money(s.qty * 1.0)} less")
    assert build_card(verdict(), NSE, LIMITS).fill_note == ""  # swing: a LIMIT never fills above entry


def test_i4_cli_money_block_labels_expected_value():
    from nse_backtest.desk.cli import format_money
    p = _probe_plan(Mode.INTRADAY)
    lim = Limits(capital=100_000)
    s = size_position(p, lim, NSE)
    v = replace(decide(p, s, None, OK, NSE, 999.5, _NOW), odds=ODDS)
    text = "\n".join(format_money(build_card(v, NSE, lim), NSE))
    assert "expected value" in text and "assumes a fill at the trigger; up to" in text


def test_me_cli_profit_line_carries_the_trigger_fill_note():
    from nse_backtest.desk.cli import format_money
    p = _probe_plan(Mode.INTRADAY)
    lim = Limits(capital=100_000)
    s = size_position(p, lim, NSE)
    lines = format_money(build_card(decide(p, s, None, OK, NSE, 999.5, _NOW), NSE, lim), NSE)
    (profit,) = [x for x in lines if "profit at T1" in x]
    assert "(assumes a fill at the trigger; up to" in profit


# ---- H5a: short money, mirrored and hand-computed (fictional numbers) ----
from nse_backtest.desk.models import Side  # noqa: E402
from nse_backtest.desk.sizing import size_position  # noqa: E402

SHORT = TradePlan("X", "NSE", Mode.INTRADAY, 100.0, 102.0, 97.0, 94.0, 1.0, 18, 100.0, "2026-10-29T09:30:00",
                  "opening-range mid", 99.5, Side.SHORT)
SHORT_LIMITS = Limits(capital=100_000, max_position_pct=100.0)


def _short_verdict(action=Action.WAIT, cp=100.1, sizing=None):
    sz = sizing or size_position(SHORT, SHORT_LIMITS, NSE)
    return DeskVerdict(action, "h", SHORT, sz, None, (Check("short_edge", Status.WARN, "edge not proven"),), (),
                       None, cp, "s" * 64, "2026-10-29T09:45:00+05:30")


def test_short_card_money_hand_computed():
    c = build_card(_short_verdict(), NSE, SHORT_LIMITS)
    assert c.qty == 362 and c.risk_per_share == pytest.approx(2.0) and c.rr_t1 == pytest.approx(1.5)
    assert c.entry_limit == 99.5
    # worst fill: sell at 99.50, stopped at 102 -> 362 * 2.5 + 93.29 cost
    assert c.max_loss == pytest.approx(998.2926, abs=0.01)
    # fill at the trigger 100: 362 * 2 + 362 * 0.25840949
    assert c.max_loss_at_trigger == pytest.approx(817.5442, abs=0.01)
    # T1: 362 * (100 - 97) - 362 * 0.25255777 (sell 100, buy 97)
    assert c.profit_t1 == pytest.approx(994.5741, abs=0.01)
    # T2: 362 * (100 - 94) - 362 * 0.24904674 (sell 100, buy 94)
    assert c.profit_t2 == pytest.approx(2081.8451, abs=0.01)
    assert c.cost == pytest.approx(91.4259, abs=0.01)
    loss1, p1, p2 = c.per_share
    assert loss1 == pytest.approx(2.75771432, abs=1e-4)
    assert p1 == pytest.approx(3 - 0.25255777, abs=1e-4) and p2 == pytest.approx(6 - 0.24904674, abs=1e-4)
    assert c.notional == pytest.approx(36_200.0) and c.pct_capital_deployed == pytest.approx(36.2)
    assert c.pct_capital_at_risk == pytest.approx(0.9983, abs=1e-3)
    assert c.expected_value is None and c.breaches == ()
    assert c.max_loss > 0 and c.profit_t1 > 0 and c.profit_t2 > c.profit_t1


def test_short_card_override_breaches_hand_computed():
    # 1,000 shares: brokerage caps at 20 a side; cost 40 + 24.875 + 3.06 + 6.2062 + 8.317116 + 151.125 = 233.5833
    c = build_card(_short_verdict(), NSE, Limits(capital=100_000), qty=1000)
    assert c.max_loss == pytest.approx(2733.5833, abs=0.01)
    assert c.breaches == ("risk per trade 2.7% of capital (limit 1.0%)",
                          "position 100.0% of capital (limit 25.0%)",
                          "max loss 2.7% of capital exceeds the daily loss limit (2.0%)")


def test_short_card_fill_note_and_gap_note():
    above = build_card(_short_verdict(cp=100.1), NSE, SHORT_LIMITS)  # sell stop-limit: down to the limit
    assert above.fill_note == f"assumes a fill at the trigger; up to {NSE.money(362 * 0.5)} less at the limit"
    inside = build_card(_short_verdict(cp=99.8), NSE, SHORT_LIMITS)  # plain sell LIMIT at ~ the current price
    assert inside.fill_note == ("assumes a fill at the trigger; the ticket is a limit at the current price "
                                f"{NSE.money(99.8)}, so about {NSE.money(362 * 0.2)} less")
    assert "as low as ₹99.50" in above.gap_note


def test_short_card_ticket_only_on_confirmed_override():
    v = _short_verdict()
    assert build_card(v, NSE, SHORT_LIMITS).ticket == ()
    c = build_card(v, NSE, SHORT_LIMITS, override_confirmed=True)
    assert c.ticket[0] == "SELL 362 X MIS SL trigger 100.00 limit 99.50"
    assert c.ticket_header == "YOUR OVERRIDE — the desk said WAIT"
