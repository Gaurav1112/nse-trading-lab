from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo
import numpy as np
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


# ---- Fix round 1: NaN / invalid data never PASS ----
NAN = float("nan")


def _s(p, odds, c):
    return by_name(run_checks(p, SIZE, odds, c))


def test_nan_monte_carlo_warns():
    cs = run_checks(plan(), SIZE, replace(ODDS, mc_expectancy_r=NAN), ctx())
    c = [c for c in cs if c.name == "monte_carlo"][0]
    assert c.status is Status.WARN and "not a number" in c.reason


def test_nan_turnover_warns():
    c = [c for c in run_checks(plan(), SIZE, ODDS, ctx(turnover_20d=NAN)) if c.name == "liquidity"][0]
    assert c.status is Status.WARN and "unavailable" in c.reason.lower()


def test_nan_gap_warns():
    assert _s(plan(), ODDS, ctx(today_gap_pct=NAN))["gap"] is Status.WARN


def test_none_gap_warns():
    c = [c for c in run_checks(plan(), SIZE, ODDS, ctx(today_gap_pct=None)) if c.name == "gap"][0]
    assert c.status is Status.WARN and "could not be measured" in c.reason


def test_nan_history_expectancy_treated_missing():
    o = replace(ODDS, history_expectancy_r=NAN)
    assert _s(plan(mode=Mode.INTRADAY, last_bar="2026-10-29T09:45:00"), o, ctx())["setup_history"] is Status.BLOCK
    assert _s(plan(), o, ctx())["setup_history"] is Status.WARN


def test_nan_noise_frac_warns():
    assert _s(plan(), replace(ODDS, noise_negative_frac=NAN), ctx())["robustness"] is Status.WARN


def test_robustness_not_run_stays_pass():
    assert _s(plan(), replace(ODDS, noise_negative_frac=None), ctx())["robustness"] is Status.PASS


def test_nan_pnl_row_unreadable_warns_in_daily_loss():
    j = ({"pnl": NAN, "closed_date": "2026-10-29"}, {"pnl": -1500, "closed_date": "2026-10-29"},
         {"pnl": -1500, "closed_date": "2026-10-29"})
    assert _s(plan(), ODDS, ctx(journal=j))["daily_loss"] is Status.BLOCK  # BLOCK still wins
    c = _c("daily_loss", journal=({"pnl": NAN, "closed_date": "2026-10-29"},))
    assert c.status is Status.WARN
    assert c.reason == "Could not read today's P&L for 1 journal row(s) — daily loss limit not verified"


def test_nan_pnl_falls_back_to_net_pnl():
    j = ({"pnl": NAN, "net_pnl": -5000, "closed_date": "2026-10-29"},)
    assert _c("daily_loss", journal=j).status is Status.BLOCK


def test_none_pnl_today_warns_and_other_day_ignored():
    assert _c("daily_loss", journal=({"pnl": None, "closed_date": "2026-10-29"},)).status is Status.WARN
    assert _c("daily_loss", journal=({"pnl": "oops", "closed_date": "2026-10-29"},)).status is Status.WARN
    assert _c("daily_loss", journal=({"pnl": None, "closed_date": "2026-10-20"},)).status is Status.PASS


def test_unreadable_rows_ignored_by_streak():
    L = lambda d: {"pnl": -1, "closed_date": f"2026-10-{d}"}
    assert stoploss_streak([L(20), {"pnl": None, "closed_date": "2026-10-21"}, L(22), L(23)], "NSE") is True


def test_regime_none_intraday_warns():
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T09:45:00")
    c = [c for c in run_checks(p, SIZE, ODDS, ctx(regime=None)) if c.name == "regime"][0]
    assert c.status is Status.WARN and "Regime unknown" in c.reason


@pytest.mark.parametrize("val", [False, np.False_, 0, "false", "no"])
def test_governor_non_true_blocks(val):
    assert _s(plan(), ODDS, ctx(governor_can_trade=val, governor_reasons=("x",)))["risk_governor"] is Status.BLOCK


def test_governor_np_true_is_not_true_identity():
    # only the literal True passes
    assert _s(plan(), ODDS, ctx(governor_can_trade=True))["risk_governor"] is Status.PASS
    assert _s(plan(), ODDS, ctx(governor_can_trade=None))["risk_governor"] is Status.WARN


# ---- now / timezone handling ----
def test_utc_now_converted_to_profile_tz_blocks_entry_window():
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T14:55:00")
    utc = datetime(2026, 10, 29, 9, 30, tzinfo=ZoneInfo("UTC"))  # 15:00 IST
    assert _s(p, ODDS, ctx(now=utc))["entry_window"] is Status.BLOCK


def test_naive_now_intraday_blocks_and_does_not_raise():
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T09:45:00")
    cs = run_checks(p, SIZE, ODDS, ctx(now=datetime(2026, 10, 29, 10, 0)))
    r = {c.name: c for c in cs}
    assert r["freshness"].status is Status.BLOCK and "no timezone" in r["freshness"].reason
    assert r["entry_window"].status is Status.BLOCK and "no timezone" in r["entry_window"].reason


def test_naive_now_swing_entry_window_passes():
    cs = by_name(run_checks(plan(), SIZE, ODDS, ctx(now=datetime(2026, 10, 29, 10, 0))))
    assert cs["entry_window"] is Status.PASS and cs["freshness"] is Status.BLOCK


# ---- minor ----
def test_journal_market_case_insensitive():
    j = tuple({"pnl": -1500, "closed_date": "2026-10-29", "market": "nse"} for _ in range(2))
    assert _s(plan(), ODDS, ctx(journal=j))["daily_loss"] is Status.BLOCK


def test_future_bar_warns_swing_and_intraday():
    c = [c for c in run_checks(plan(last_bar="2026-11-02T00:00:00"), SIZE, ODDS, ctx()) if c.name == "freshness"][0]
    assert c.status is Status.WARN and "in the future" in c.reason
    p = plan(mode=Mode.INTRADAY, last_bar="2026-10-29T11:00:00")
    c = [c for c in run_checks(p, SIZE, ODDS, ctx()) if c.name == "freshness"][0]
    assert c.status is Status.WARN and "in the future" in c.reason


def test_stale_next_earnings_warns_swing():
    c = [c for c in run_checks(plan(), SIZE, ODDS, ctx(next_earnings=date(2026, 10, 1))) if c.name == "earnings"][0]
    assert c.status is Status.WARN and "stale" in c.reason


def test_unknown_ban_status_warns():
    assert _s(plan(), ODDS, ctx(ban=BanList(frozenset(), "weird", None, "")))["ban_list"] is Status.WARN
    assert _s(plan(), ODDS, ctx(ban=BanList(frozenset(), "cached", "2026-10-29", "")))["ban_list"] is Status.PASS


def test_streak_date_ties_use_journal_order():
    # same date: last-in-journal is "latest"; a win listed last breaks the streak
    j = [{"pnl": -1, "closed_date": "2026-10-20"}, {"pnl": -1, "closed_date": "2026-10-21"},
         {"pnl": -1, "closed_date": "2026-10-22"}, {"pnl": 5, "closed_date": "2026-10-22"}]
    assert not stoploss_streak(j, "NSE")
    # tie on 22nd resolved by journal order: win listed first, loss last -> last 3 = L, W, L
    k = j[:2] + [j[3], j[2]]
    assert not stoploss_streak(k, "NSE")
    # loss listed last on the tie date with two earlier losses -> streak
    m = [j[0], j[3], j[1], j[2]]
    assert stoploss_streak(m + [{"pnl": -1, "closed_date": "2026-10-22"}], "NSE") is False


# ---- Fix round 2 ----
def _c(name, p=None, s=SIZE, o=ODDS, **kw):
    return [c for c in run_checks(p or plan(), s, o, ctx(**kw)) if c.name == name][0]


def _intra():
    return plan(mode=Mode.INTRADAY, last_bar="2026-10-29T09:45:00")


@pytest.mark.parametrize("reg", ["garbage", "", "   "])
def test_intraday_unknown_regime_warns(reg):
    c = _c("regime", p=_intra(), regime=reg)
    assert c.status is Status.WARN and "Regime unknown" in c.reason


@pytest.mark.parametrize("reg", ["hostile", " Hostile "])
def test_swing_hostile_case_insensitive_blocks(reg):
    assert _c("regime", regime=reg).status is Status.BLOCK


def test_swing_garbage_regime_warns():
    assert _c("regime", regime="garbage").status is Status.WARN


def test_intraday_hostile_lowercase_warns():
    assert _c("regime", p=_intra(), regime="hostile").status is Status.WARN
    assert _c("regime", p=_intra(), regime="trending").status is Status.PASS


J2 = tuple({"pnl": -1500, "closed_date": "2026-10-29"} for _ in range(2))


@pytest.mark.parametrize("lim", [
    Limits(capital=NAN), Limits(capital=100_000, daily_loss_limit_pct=NAN),
    Limits(capital=100_000, daily_loss_limit_pct=0), Limits(capital=0)])
def test_unconfigured_loss_limit_blocks(lim):
    c = _c("daily_loss", limits=lim)
    assert c.status is Status.BLOCK and "Daily loss limit is not configured" in c.reason


def test_nan_qty_blocks():
    assert _c("size", s=replace(SIZE, qty=NAN)).status is Status.BLOCK


@pytest.mark.parametrize("mode_p,st", [(None, Status.WARN), ("i", Status.BLOCK)])
def test_nan_history_n_missing(mode_p, st):
    p = _intra() if mode_p else plan()
    assert _c("setup_history", p=p, o=replace(ODDS, history_n=NAN)).status is st


def test_ban_symbol_whitespace_and_ns_suffix():
    b = BanList(frozenset({"ABC"}), "ok", None, "")
    assert _c("ban_list", p=replace(plan(), symbol=" ABC"), ban=b).status is Status.BLOCK
    assert _c("ban_list", p=replace(plan(), symbol="abc.ns "), ban=b).status is Status.BLOCK


def test_exact_reason_wording():
    assert "edge is fragile" in _c("robustness", o=replace(ODDS, noise_negative_frac=0.5)).reason
    assert "Earnings date looks stale" in _c("earnings", next_earnings=date(2026, 10, 1)).reason
    assert "Robustness not run" in _c("robustness", o=replace(ODDS, noise_negative_frac=None)).reason
    naive = datetime(2026, 10, 29, 10, 0)
    assert _c("entry_window", p=_intra(), now=naive).reason == "Clock has no timezone — cannot verify freshness"
    assert _c("freshness", p=_intra(), now=naive).reason == "Clock has no timezone — cannot verify freshness"


def test_reward_risk_reason_shows_distinct_numbers_near_min():
    c = _c("reward_risk", p=plan(t1=100 + 2 * Limits(capital=1).min_rr - 1e-6))
    assert c.status is Status.BLOCK
    import re
    a, b = re.findall(r"1:(\d+\.\d+)", c.reason)
    assert a != b
    c2 = _c("reward_risk", p=plan(t1=101))
    assert re.findall(r"1:(\d+\.\d+)", c2.reason) == ["0.50", "%.2f" % Limits(capital=1).min_rr]


def test_streak_tie_order_decides_result():
    L = lambda d: {"pnl": -1, "closed_date": f"2026-10-{d}"}
    W = lambda d: {"pnl": 5, "closed_date": f"2026-10-{d}"}
    # stable sort: L20, L21, W22, L22 -> last three L21, W22, L22 -> no streak
    assert stoploss_streak([L(20), W(22), L(21), L(22)], "NSE") is False
    # stable sort: L21, W22, L22, L22 -> last three W22, L22, L22 -> no streak
    assert stoploss_streak([W(22), L(21), L(22), L(22)], "NSE") is False
    # stable sort: W22 kept first among ties, then L22, L22, L22 -> last three all losses
    assert stoploss_streak([W(22), L(22), L(22), L(22)], "NSE") is True
    # same entries, win listed last on the tie date -> streak broken
    assert stoploss_streak([L(22), L(22), L(22), W(22)], "NSE") is False


def test_string_numerics_never_raise():
    assert _c("liquidity", turnover_20d="1e9").status is Status.WARN
    assert _c("gap", today_gap_pct="x").status is Status.WARN
    assert _c("monte_carlo", o=replace(ODDS, mc_expectancy_r="1")).status is Status.WARN
    assert _c("setup_history", o=replace(ODDS, history_expectancy_r="x")).status is Status.WARN
    assert _c("robustness", o=replace(ODDS, noise_negative_frac="x")).status is Status.WARN


def test_string_mode_normalised():
    p = replace(plan(), mode="swing")
    assert _c("regime", p=p, regime="HOSTILE").status is Status.BLOCK
    assert _c("entry_window", p=p).status is Status.PASS


@pytest.mark.parametrize("sym", ["NSE:ABC", "ABC-EQ", "nse:abc-eq", "ABC.NS"])
def test_ban_symbol_normalisation(sym):
    b = BanList(frozenset({"ABC"}), "ok", None, "")
    assert _c("ban_list", p=replace(plan(), symbol=sym), ban=b).status is Status.BLOCK


@pytest.mark.parametrize("val,st", [
    (datetime(2026, 12, 30, 9, 0), Status.PASS),
    (datetime(2026, 11, 2, 9, 0), Status.WARN),
    ("2026-12-30", Status.PASS),
    ("2026-12-30T00:00:00", Status.PASS),
    ("garbage", Status.WARN),
])
def test_next_earnings_types(val, st):
    c = _c("earnings", next_earnings=val)
    assert c.status is st
    if val == "garbage":
        assert "Could not check earnings date" in c.reason


def test_next_earnings_pandas_timestamp():
    import pandas as pd
    assert _c("earnings", next_earnings=pd.Timestamp("2026-12-30")).status is Status.PASS


def test_size_below_one_share_reason_and_nan_text():
    c = _c("size", s=replace(SIZE, qty=0.5, note=""))
    assert c.status is Status.BLOCK and "below one share" in c.reason
    assert "not a valid number" in _c("size", s=replace(SIZE, qty=NAN)).reason


def test_slightly_future_bar_reads_current_and_stale_unknown_asof():
    p = _intra()
    p = replace(p, last_bar="2026-10-29T10:03:00")
    c = _c("freshness", p=p)
    assert c.status is Status.PASS and "Last bar is current" in c.reason
    c = _c("ban_list", ban=BanList(frozenset(), "stale", None, ""))
    assert "last fetch date unknown" in c.reason and "None" not in c.reason


def _by(cs):
    return {c.name: c for c in cs}


def test_unreadable_journal_warns_loss_streak_pdt():
    lim = Limits(capital=100_000, pdt_guard=True)
    cs = _by(run_checks(plan(Mode.INTRADAY, market="US", last_bar="2026-10-29T09:55:00"), SIZE, ODDS,
                        ctx(profile=US, limits=lim, now=datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("America/New_York")),
                            journal_readable=False, day_trades_5d=0)))
    assert cs["daily_loss"].status is Status.WARN and "daily loss limit not verified" in cs["daily_loss"].reason
    assert cs["stoploss_streak"].status is Status.WARN and "stop-out streak not verified" in cs["stoploss_streak"].reason
    assert cs["pdt"].status is Status.WARN and "day-trade count not verified" in cs["pdt"].reason
    assert all("Could not read the trade journal" in cs[n].reason for n in ("daily_loss", "stoploss_streak", "pdt"))


def test_unreadable_journal_still_blocks_pdt_and_unconfigured_loss():
    lim = Limits(capital=100_000, pdt_guard=True)
    us_now = datetime(2026, 10, 29, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    cs = _by(run_checks(plan(Mode.INTRADAY, market="US", last_bar="2026-10-29T09:55:00"), SIZE, ODDS,
                        ctx(profile=US, limits=lim, now=us_now, journal_readable=False, day_trades_5d=3)))
    assert cs["pdt"].status is Status.BLOCK
    cs = _by(run_checks(plan(), SIZE, ODDS, ctx(limits=Limits(capital=0), journal_readable=False)))
    assert cs["daily_loss"].status is Status.BLOCK


def test_governor_not_evaluated_surfaces_reasons():
    cs = _by(run_checks(plan(), SIZE, ODDS, ctx(governor_can_trade=None, governor_reasons=("a broke", "b broke"))))
    assert cs["risk_governor"].status is Status.WARN
    assert cs["risk_governor"].reason == "Risk governor not evaluated: a broke; b broke"
    cs = _by(run_checks(plan(), SIZE, ODDS, ctx(governor_can_trade=None)))
    assert cs["risk_governor"].reason == "Risk governor not evaluated: portfolio limits could not be checked"
