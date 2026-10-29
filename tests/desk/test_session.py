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
    prev_close = float(daily["Close"].iloc[-1])
    now = datetime.now(ZoneInfo(NSE.tz))
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
