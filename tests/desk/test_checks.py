from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo
import pytest
from nse_backtest.desk.checks import CheckContext, run_checks, stoploss_streak
from nse_backtest.desk.models import Limits, Mode, Odds, Sizing, Status, TradePlan
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.nse_lists import BanList

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 29, 10, 0, tzinfo=IST)


def plan(mode=Mode.SWING, last_bar="2026-10-28T00:00:00", t1=103.0, market="NSE"):
    return TradePlan("ABC", market, mode, 100, 98, t1, 106, 2, 15, 100, last_bar, "user")


SIZE = Sizing(100, "position cap", 250.0, 10_000.0, 50.0, False, "100 shares")
ODDS = Odds(10_000, 19, .5, .3, .2, .4, 30, 55.0, .3, (0.3,) * 30, 1, 3, 6, .1, .2, .5, 0.0)


def ctx(**kw):
    base = dict(profile=NSE, limits=Limits(capital=100_000), now=NOW, regime="TRENDING",
                governor_can_trade=True, ban=BanList(frozenset(), "ok", "2026-10-29", ""),
                next_earnings=date(2026, 12, 30), earnings_checked=True, turnover_20d=1e9, today_gap_pct=0.5)
    base.update(kw)
    return CheckContext(**base)


def by_name(checks):
    return {c.name: c.status for c in checks}


def test_baseline_all_pass_with_sixteen_checks():
    cs = run_checks(plan(), SIZE, ODDS, ctx())
    assert len(cs) == 16 and all(c.status is Status.PASS for c in cs), [c for c in cs if c.status is not Status.PASS]
    assert all(c.reason.strip() for c in cs)


@pytest.mark.parametrize("override,name,status", [
    (dict(regime="HOSTILE"), "regime", Status.BLOCK),
    (dict(regime="MIXED"), "regime", Status.WARN),
    (dict(regime=None), "regime", Status.WARN),
    (dict(governor_can_trade=False, governor_reasons=("max positions",)), "risk_governor", Status.BLOCK),
    (dict(governor_can_trade=None), "risk_governor", Status.WARN),
    (dict(ban=BanList(frozenset({"ABC"}), "ok", None, "")), "ban_list", Status.BLOCK),
    (dict(ban=None), "ban_list", Status.WARN),
    (dict(ban=BanList(frozenset(), "stale", "2026-10-20", "")), "ban_list", Status.WARN),
    (dict(turnover_20d=1e6), "liquidity", Status.BLOCK),
    (dict(turnover_20d=None), "liquidity", Status.WARN),
    (dict(earnings_checked=False), "earnings", Status.WARN),
    (dict(next_earnings=date(2026, 11, 5)), "earnings", Status.WARN),
    (dict(today_gap_pct=-3.1), "gap", Status.WARN),
    (dict(journal=tuple({"pnl": -1500, "closed_date": "2026-10-29"} for _ in range(2))), "daily_loss", Status.BLOCK),
])
def test_single_check_flips(override, name, status):
    assert by_name(run_checks(plan(), SIZE, ODDS, ctx(**override)))[name] is status


def test_reward_risk_and_size_and_odds_blocks():
    assert by_name(run_checks(plan(t1=101), SIZE, ODDS, ctx()))["reward_risk"] is Status.BLOCK
    assert by_name(run_checks(plan(), replace(SIZE, qty=0, note="too wide"), ODDS, ctx()))["size"] is Status.BLOCK
    assert by_name(run_checks(plan(), SIZE, replace(ODDS, mc_expectancy_r=-0.1), ctx()))["monte_carlo"] is Status.BLOCK
    assert by_name(run_checks(plan(), SIZE, replace(ODDS, noise_negative_frac=0.5), ctx()))["robustness"] is Status.WARN
    assert by_name(run_checks(plan(), SIZE, None, ctx()))["monte_carlo"] is Status.WARN


def test_stale_swing_data_blocks():
    assert by_name(run_checks(plan(last_bar="2026-10-20T00:00:00"), SIZE, ODDS, ctx()))["freshness"] is Status.BLOCK


def test_unparseable_last_bar_blocks_not_raises():
    r = run_checks(plan(last_bar="garbage"), SIZE, ODDS, ctx())
    f = [c for c in r if c.name == "freshness"][0]
    assert f.status is Status.BLOCK and "Could not read the last bar's time" in f.reason


def test_intraday_window_freshness_and_history_gate():
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T09:45:00")
    assert by_name(run_checks(p, SIZE, ODDS, ctx()))["entry_window"] is Status.PASS
    late = ctx(now=datetime(2026, 10, 29, 14, 45, tzinfo=IST))
    r = by_name(run_checks(p, SIZE, ODDS, late))
    assert r["entry_window"] is Status.BLOCK and r["freshness"] is Status.BLOCK
    assert by_name(run_checks(p, SIZE, replace(ODDS, history_expectancy_r=0.1), ctx()))["setup_history"] is Status.BLOCK


def test_us_no_ban_list_and_pdt():
    et = datetime(2026, 10, 29, 10, 30, tzinfo=ZoneInfo("America/New_York"))
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T10:15:00", market="US")
    c = ctx(profile=US, now=et, ban=None, limits=Limits(capital=10_000, pdt_guard=True), day_trades_5d=3, turnover_20d=1e9)
    r = by_name(run_checks(p, SIZE, ODDS, c))
    assert r["ban_list"] is Status.PASS and r["pdt"] is Status.BLOCK


def test_stoploss_streak_per_market():
    j = [{"pnl": -1, "closed_date": f"2026-10-2{i}"} for i in range(3)]
    assert stoploss_streak(j, "NSE") and not stoploss_streak(j, "US")
    assert not stoploss_streak(j + [{"pnl": 5, "closed_date": "2026-10-28"}], "NSE")
