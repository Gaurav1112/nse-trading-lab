from nse_backtest.desk import loaders, session
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import DeskVerdict, Limits, Mode
from tests.desk.test_cli_loaders import series
from tests.desk.test_loaders_many import via_load_daily


def test_resolve_regime_none_when_load_fails(monkeypatch):
    def boom(p):
        raise RuntimeError("offline")
    monkeypatch.setattr(loaders, "load_regime", boom)
    warns = []
    assert session.resolve_regime(NSE, warns.append) is None
    assert warns and "offline" in warns[0]


def test_context_for_passes_regime_through(offline_desk):
    ctx = session.context_for(NSE, Limits(capital=100_000), regime="MIXED")
    assert ctx.regime == "MIXED"


def test_load_frames_marks_failures_as_none(monkeypatch):
    def load(p, s, years=4):
        if s == "BAD":
            raise ValueError("nope")
        return series()
    monkeypatch.setattr(loaders, "load_daily", load)
    monkeypatch.setattr(loaders, "load_daily_many", via_load_daily)  # S4: load_frames batches
    warns = []
    out = session.load_frames(NSE, ["OK", "BAD"], warns.append)
    assert out["OK"] is not None and out["BAD"] is None and "BAD" in warns[0]


def test_run_check_returns_verdict(offline_desk):
    v = session.run_check(NSE, " abc ", Mode.SWING, Limits(capital=100_000))
    assert isinstance(v, DeskVerdict) and v.plan.symbol == "ABC"


def test_run_check_blank_symbol_is_value_error(offline_desk):
    import pytest
    with pytest.raises(ValueError):
        session.run_check(NSE, "  ", Mode.SWING, Limits(capital=100_000))


def test_load_regime_uses_no_exchange_suffix_for_index(monkeypatch):
    import nse_backtest.data as data
    seen = {}
    def fake(sym, start, exchange="NS", **k):
        seen["args"] = (sym, exchange)
        return series()
    monkeypatch.setattr(data, "fetch_nse", fake)
    assert loaders.load_regime(NSE) is not None
    assert seen["args"] == ("^NSEI", "")


# ---- Final fix wave A5-A7 ----
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
from nse_backtest.desk.models import LiveQuote


def test_a6_stale_regime_is_none_with_warning():
    stale = series()
    stale.index = stale.index - pd.Timedelta(days=10)
    warns = []
    assert session.regime_of(stale, warns.append, NSE) is None
    assert warns == [f"Regime index data is stale (last bar {stale.index[-1].date()})"]
    assert session.regime_of(series(), [].append, NSE) is not None


def test_a6_resolve_tape_reports_stale(monkeypatch):
    stale = series()
    stale.index = stale.index - pd.Timedelta(days=10)
    monkeypatch.setattr(loaders, "load_regime", lambda p: stale)
    warns = []
    df, tape = session.resolve_tape(NSE, warns.append)
    assert tape is None and "stale" in warns[0]


def test_a5_run_check_fetches_live_quote_and_gap(offline_desk, monkeypatch):
    seen = []
    daily = series()
    now = datetime.now(ZoneInfo(NSE.tz))
    # The gap is measured against the last close before market-today (after the close, today's bar exists).
    prev_close = float(daily["Close"][daily.index.date < now.date()].iloc[-1])
    q = LiveQuote(price=prev_close, day_open=prev_close * 1.03, as_of=now)
    monkeypatch.setattr(loaders, "live_quote", lambda p, s: seen.append(s) or q)
    v = session.run_check(NSE, "abc", Mode.SWING, Limits(capital=100_000))
    assert seen == ["ABC"] and v.current_price == prev_close
    gap = [c for c in v.checks if c.name == "gap"][0]
    assert gap.status.value == "WARN" and "+3.0%" in gap.reason
    assert [c for c in v.checks if c.name == "live_price"][0].status.value == "PASS"


def test_a5_run_check_without_quote_warns_live_price_and_gap(offline_desk):
    v = session.run_check(NSE, "ABC", Mode.SWING, Limits(capital=100_000))
    st = {c.name: c.status.value for c in v.checks}
    assert st["live_price"] == "WARN" and st["gap"] == "WARN"


def test_a7_run_daily_plan_resolves_regime_once_and_passes_it(offline_desk, monkeypatch):
    calls, kws = [], []
    real_assess = session.tape_monitor.assess_tape
    monkeypatch.setattr(session.tape_monitor, "assess_tape", lambda df: calls.append(1) or real_assess(df))
    real = session.daily_plan
    monkeypatch.setattr(session, "daily_plan", lambda *a, **k: kws.append(k) or real(*a, **k))
    quotes = []
    monkeypatch.setattr(loaders, "live_quote", lambda p, s: quotes.append(s) or None)
    dp = session.run_daily_plan(NSE, Limits(capital=100_000), ["AAA", "BBB"])
    assert len(calls) == 1 and kws[0]["regime"] == dp.regime and kws[0]["tape"] is not None
    assert callable(kws[0]["quote_lookup"])
    assert sorted(quotes) == ["AAA", "BBB"]  # only the TAKE/WATCH candidates, once each


def test_a7_run_daily_plan_accepts_header_regime(offline_desk, monkeypatch):
    def boom(df):
        raise AssertionError("must not re-assess the tape")
    monkeypatch.setattr(session.tape_monitor, "assess_tape", boom)
    monkeypatch.setattr(loaders, "load_regime", lambda p: (_ for _ in ()).throw(AssertionError("no reload")))
    dp = session.run_daily_plan(NSE, Limits(capital=100_000), ["AAA"], regime="HOSTILE", tape=None)
    assert dp.regime == "HOSTILE" and dp.take == ()


# ---- S2: regime staleness counts trading sessions ----
def _regime_ending(last: str):
    df = series()
    df.index = pd.bdate_range(end=last, periods=len(df))
    return df


def test_s2_regime_stale_allows_one_missed_session():
    now = datetime(2026, 10, 21, 10, 0, tzinfo=ZoneInfo(NSE.tz))
    assert session._regime_stale(_regime_ending("2026-10-16"), NSE, now=now) is None
    assert session._regime_stale(_regime_ending("2026-10-15"), NSE, now=now) == \
        "Regime index data is stale (last bar 2026-10-15)"


def test_s2_regime_stale_uncovered_year_keeps_3_day_rule():
    now = datetime(2027, 1, 7, 10, 0, tzinfo=ZoneInfo(NSE.tz))
    assert session._regime_stale(_regime_ending("2027-01-04"), NSE, now=now) is None
    assert session._regime_stale(_regime_ending("2027-01-01"), NSE, now=now) is not None


def test_s2_fix1_regime_stale_counts_todays_finished_session():
    now = datetime(2026, 10, 29, 16, 0, tzinfo=ZoneInfo(NSE.tz))
    assert session._regime_stale(_regime_ending("2026-10-27"), NSE, now=now) == \
        "Regime index data is stale (last bar 2026-10-27)"
    assert session._regime_stale(_regime_ending("2026-10-28"), NSE, now=now) is None
    # session still open: only the 28th has finished, one lag allowed
    assert session._regime_stale(_regime_ending("2026-10-27"), NSE,
                                 now=datetime(2026, 10, 29, 11, 0, tzinfo=ZoneInfo(NSE.tz))) is None


def test_r1b_regime_future_dated_last_bar_warns():
    now = datetime(2026, 10, 21, 10, 0, tzinfo=ZoneInfo(NSE.tz))
    assert session._regime_stale(_regime_ending("2026-10-22"), NSE, now=now) == \
        "Regime index data has a future-dated last bar (2026-10-22)"
    assert session._regime_stale(_regime_ending("2026-10-21"), NSE, now=now) is None  # today is fine


def test_r1b_regime_stale_uncovered_year_names_missing_calendar():
    now = datetime(2027, 1, 7, 10, 0, tzinfo=ZoneInfo(NSE.tz))
    assert session._regime_stale(_regime_ending("2027-01-01"), NSE, now=now) == \
        "Regime index data is stale (last bar 2027-01-01) (holiday calendar not available for 2027)"


# ---------------------------------------------------------------- H2: your-call ranking, the desk's own entry

def _rank_v(sym, action, e, blocks=()):
    from tests.desk.test_choose import v
    from nse_backtest.desk.models import Action, Check, Status
    checks = tuple(Check(b, Status.BLOCK, b) for b in blocks) or (Check("regime", Status.PASS, ""),)
    return v(sym, Action(action), e=e, checks=checks)


def test_h2_candidate_rank_take_wait_advice_skip_then_other_skip_by_expectancy():
    vs = [_rank_v("FRESH", "SKIP", 0.9, ["freshness"]),
          _rank_v("RMC", "SKIP", -0.3, ["regime", "monte_carlo"]),
          _rank_v("NOODDS", "SKIP", None, ["regime"]),
          _rank_v("REG", "SKIP", 0.2, ["regime"]),
          _rank_v("W1", "WAIT", 0.1), _rank_v("W2", "WAIT", 0.4),
          _rank_v("T1", "TAKE", 0.05), _rank_v("HIST", "SKIP", 0.1, ["setup_history", "robustness"]),
          _rank_v("LIM", "SKIP", None, ["size"])]
    order = [x.plan.symbol for x in sorted(vs, key=session.candidate_rank)]
    assert order == ["T1", "W2", "W1", "REG", "HIST", "RMC", "NOODDS", "FRESH", "LIM"]


def test_h2_merge_candidates_includes_others_ranked_and_deduped():
    from nse_backtest.desk.daily import DailyPlan
    take = _rank_v("T1", "TAKE", 0.3)
    others = (_rank_v("T4", "TAKE", 0.1), _rank_v("S1", "SKIP", 0.2, ["regime"]), _rank_v("T1", "SKIP", 0.0))
    plan = DailyPlan("NSE", None, "", (), (take,), (), (), (), others)
    out = [(x.plan.symbol, x.action.value) for x in session.merge_candidates(plan, ())]
    assert out == [("T1", "TAKE"), ("T4", "TAKE"), ("S1", "SKIP")]
    assert [x.plan.symbol for x in session.merge_candidates(plan, (), include_others=False)] == ["T1"]


def test_h2_run_check_desk_entry_uses_the_plans_breakout_entry(offline_desk, monkeypatch):
    df = series()
    df.iloc[-1, df.columns.get_loc("Close")] = float(df.High.tail(20).max()) / 1.2  # far below the 20-day high
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: df)
    want = float(df.High.tail(20).max()) * 1.001
    v = session.run_check(NSE, "ABC", Mode.SWING, Limits(capital=100_000), desk_entry=True)
    assert abs(v.plan.entry - want) < 1e-6
    v0 = session.run_check(NSE, "ABC", Mode.SWING, Limits(capital=100_000))  # the CLI default: the last close
    assert abs(v0.plan.entry - float(df.Close.iloc[-1])) < 1e-6
    v1 = session.run_check(NSE, "ABC", Mode.SWING, Limits(capital=100_000), entry=150.0, desk_entry=True)
    assert v1.plan.entry == 150.0  # an entry the owner typed always wins


def test_h2_plan_entry_matches_the_plans_own_geometry():
    from nse_backtest.desk.daily import plan_entry
    at = series()
    assert plan_entry(at) is None  # at the 20-day high: the plan buys at the close
    far = series()
    far.iloc[-1, far.columns.get_loc("Close")] = float(far.High.tail(20).max()) / 1.2
    assert abs(plan_entry(far) - float(far.High.tail(20).max()) * 1.001) < 1e-9
