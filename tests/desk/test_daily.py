"""H2: the daily plan keeps every verdict it computes (DailyPlan.others), checks "far" symbols at the plan's own
breakout entry, and isolates a failing symbol. No network."""
import json

import pytest

from nse_backtest.desk import daily
from nse_backtest.desk.daily import daily_plan
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action, Limits
from nse_backtest.desk.tradecard import build_card
from nse_backtest.desk.verdict import is_quick
from tests.desk.test_daily_positions_intraday import ctx, series

TAKEABLE = {"A": 1.004, "B": 1.003, "C": 1.0035, "F": 1.0025}  # each passes every check on these bars


def _far():
    df = series()
    df.iloc[-1, df.columns.get_loc("Close")] = df.High.tail(20).max() / 1.2
    return df


def _frames():
    fr = {s: series(step=k) for s, k in TAKEABLE.items()}
    fr["D"] = series(step=1.0045)  # blocked by reward_risk
    fr["FAR"] = _far()
    return fr


def _plan(frames=None, **kw):
    kw.setdefault("top_take", 3)
    return daily_plan(NSE, frames or _frames(), series(step=1.004), Limits(capital=100_000), ctx(), replays=300, **kw)


def _audited_symbols(path):
    if not path.exists():
        return set()
    return {json.loads(line)["symbol"] for line in path.read_text().splitlines() if line.strip()}


def test_fourth_take_beyond_top_take_is_kept_in_others_as_take(tmp_path):
    dp = _plan(audit_path=tmp_path / "audit.jsonl")
    assert dp.regime == "TRENDING" and len(dp.take) == 3
    extra = [v for v in dp.others if v.plan.symbol in TAKEABLE]
    assert len(extra) == 1 and extra[0].action is Action.TAKE
    assert extra[0].odds is not None and is_quick(extra[0])
    assert not any(s == extra[0].plan.symbol for s, _ in dp.skipped)  # it has a verdict: not "not checked"
    assert {v.plan.symbol for v in dp.take} | {extra[0].plan.symbol} == set(TAKEABLE)


def test_far_symbol_is_checked_at_the_plans_breakout_entry_with_full_money():
    fr = _frames()
    dp = _plan(fr)
    far = next(v for v in dp.others if v.plan.symbol == "FAR")
    assert far.plan.entry == pytest.approx(float(fr["FAR"]["High"].tail(20).max()) * 1.001)
    assert far.action is Action.WAIT and far.odds is not None
    assert build_card(far, NSE, Limits(capital=100_000)).max_loss is not None
    assert not any(s == "FAR" for s, _ in dp.skipped)
    assert all(v.plan.symbol != "FAR" for v in dp.take + dp.watch)  # far is never on the near-setup lists


def test_blocked_pass_one_verdict_keeps_its_numbers_in_others():
    d = next(v for v in _plan().others if v.plan.symbol == "D")
    assert d.action is Action.SKIP and [c.name for c in d.blocks()] == ["reward_risk"]
    assert d.odds is not None and d.sizing is not None


def test_a_symbol_whose_check_raises_is_skipped_with_its_reason_and_the_rest_still_plan(monkeypatch):
    real = daily.check_trade

    def flaky(df, sym, *a, **k):
        if sym == "BAD":
            raise RuntimeError("odd bars")
        return real(df, sym, *a, **k)
    monkeypatch.setattr(daily, "check_trade", flaky)
    fr = _frames()
    fr["BAD"] = series()
    dp = _plan(fr)
    assert ("BAD", "RuntimeError: odd bars") in dp.skipped
    assert len(dp.take) == 3 and {v.plan.symbol for v in dp.others} >= {"D", "FAR"}


def test_others_are_never_written_to_the_audit_log(tmp_path):
    path = tmp_path / "audit.jsonl"
    dp = _plan(audit_path=path)
    logged = _audited_symbols(path)
    assert logged == {v.plan.symbol for v in dp.take}
    quick = {v.plan.symbol for v in dp.others if is_quick(v)}
    assert quick and not (quick & logged)


def test_every_symbol_lands_in_exactly_one_list():
    fr = _frames()
    fr["SHORT"] = series(n=50)
    dp = _plan(fr)
    syms = ([v.plan.symbol for v in dp.take + dp.watch + dp.others] + [s for s, _ in dp.skipped]
            + list(dp.unavailable))
    assert sorted(syms) == sorted(fr)


def test_pass_two_verdicts_are_not_quick():
    dp = _plan()
    assert dp.take and not any(is_quick(v) for v in dp.take)


def test_daily_plan_others_defaults_to_empty_for_positional_callers():
    from nse_backtest.desk.daily import DailyPlan
    assert DailyPlan("NSE", None, "", (), (), (), (), ()).others == ()


def test_overflow_helper_splits_by_rank():
    kept, rest = daily._overflow([3, 1, 2], 2, key=lambda x: -x)
    assert kept == [3, 2] and rest == [1]
