import importlib.util
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nse_backtest.desk import cli, loaders, session
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.models import Limits
from tests.desk.test_cli_loaders import series


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset({"XYZ"}), "ok", None, ""))


class FakeTicker:
    def __init__(self, cal):
        self.cal = cal
        self.sym = None

    @property
    def calendar(self):
        if isinstance(self.cal, Exception):
            raise self.cal
        return self.cal


def patch_yf(monkeypatch, cal):
    import yfinance
    t = FakeTicker(cal)
    def mk(sym):
        t.sym = sym
        return t
    monkeypatch.setattr(yfinance, "Ticker", mk)
    return t


@pytest.mark.parametrize("cal,expected", [
    ({"Earnings Date": [pd.Timestamp("2026-11-05")]}, (date(2026, 11, 5), True)),
    ({"Earnings Date": [datetime(2026, 11, 5, 10, 0)]}, (date(2026, 11, 5), True)),
    ({"Earnings Date": pd.Timestamp("2026-11-05")}, (date(2026, 11, 5), True)),
    ({"Earnings Date": [pd.Timestamp("2026-11-05"), pd.Timestamp("2026-11-09")]}, (date(2026, 11, 5), True)),
    ({"Earnings Date": [pd.NaT]}, (None, False)),
    ({"Earnings Date": [float("nan")]}, (None, False)),
    ({"Earnings Date": []}, (None, False)),
    ({}, (None, False)),
    (None, (None, False)),
    (RuntimeError("boom"), (None, False)),
])
def test_next_earnings(monkeypatch, cal, expected):
    patch_yf(monkeypatch, cal)
    d, ok = loaders.next_earnings(US, "AAPL")
    assert (d, ok) == expected
    if d is not None:
        assert type(d) is date


def test_next_earnings_uses_yf_symbol(monkeypatch):
    t = patch_yf(monkeypatch, {"Earnings Date": [pd.Timestamp("2026-11-05")]})
    loaders.next_earnings(NSE, "INFY")
    assert t.sym == "INFY.NS"


def test_positions_journal_missing_and_corrupt(tmp_path, caplog):
    assert loaders.load_positions() == [] and loaders.load_journal() == []
    (tmp_path / "positions.json").write_text("{not json")
    (tmp_path / "trade_journal.json").write_text('{"a": 1}')
    with pytest.raises(loaders.UserDataError):
        loaders.load_positions()
    with pytest.raises(loaders.UserDataError):
        loaders.load_journal()
    (tmp_path / "positions.json").write_text(json.dumps([{"symbol": "A"}, 3]))
    assert loaders.load_positions() == [{"symbol": "A"}]


def test_limits_corrupt_and_invalid(tmp_path):
    (tmp_path / "desk_settings.json").write_text("garbage")
    assert loaders.load_limits(NSE) == Limits(capital=100_000)
    (tmp_path / "desk_settings.json").write_text(json.dumps({"NSE": {"capital": -5, "bogus": 1}, "US": {"capital": 777, "bogus": 2}}))
    assert loaders.load_limits(NSE) == Limits(capital=100_000)
    assert loaders.load_limits(US).capital == 777


@pytest.mark.parametrize("bad", [Limits(capital=0), Limits(capital=float("nan")), Limits(capital=1, risk_per_trade_pct=0),
                                  Limits(capital=1, max_position_pct=101), Limits(capital=1, daily_loss_limit_pct=float("inf")),
                                  Limits(capital=1, pdt_guard="yes")])
def test_save_limits_rejects_invalid(bad):
    with pytest.raises(ValueError):
        loaders.save_limits(NSE, bad)


def test_save_limits_preserves_other_market(tmp_path):
    loaders.save_limits(US, Limits(capital=4321))
    loaders.save_limits(NSE, Limits(capital=999))
    assert loaders.load_limits(US).capital == 4321 and loaders.load_limits(NSE).capital == 999
    assert not list(tmp_path.glob("*.tmp"))


def test_build_context_governor_and_filters(tmp_path, monkeypatch):
    import components.risk_governor as rg
    seen = {}
    class V:
        can_trade = False
        reasons = ["weekly drawdown"]
    def fake(pos, jr, cap, regime=None):
        seen.update(pos=pos, jr=jr, cap=cap)
        return V()
    monkeypatch.setattr(rg, "assess", fake)
    (tmp_path / "positions.json").write_text(json.dumps([{"symbol": "A"}, {"symbol": "B", "market": "us"}]))
    (tmp_path / "trade_journal.json").write_text(json.dumps([{"symbol": "A", "market": "NSE"}, {"symbol": "B", "market": "US"}]))
    ctx = loaders.build_context(NSE, Limits(capital=5000))
    assert ctx.governor_can_trade is False and ctx.governor_reasons == ("weekly drawdown",)
    assert seen["pos"] == [{"symbol": "A"}] and [j["symbol"] for j in seen["jr"]] == ["A"] and seen["cap"] == 5000
    assert ctx.ban is not None and "XYZ" in ctx.ban.symbols
    assert ctx.now.tzinfo is not None
    ctx_us = loaders.build_context(US, Limits(capital=5000))
    assert ctx_us.ban is None and seen["pos"] == [{"symbol": "B", "market": "us"}]


def test_build_context_governor_failure(monkeypatch):
    import components.risk_governor as rg
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(rg, "assess", boom)
    ctx = loaders.build_context(NSE, Limits(capital=5000))
    assert ctx.governor_can_trade is None


def test_load_intraday(monkeypatch):
    import yfinance
    idx = pd.date_range("2026-10-28 03:45", periods=5, freq="15min", tz="UTC")
    cols = pd.MultiIndex.from_product([["Open", "High", "Low", "Close", "Volume"], ["INFY.NS"]])
    df = pd.DataFrame(np.ones((5, 5)), index=idx, columns=cols)
    calls = {}
    def dl(sym, **kw):
        calls.update(sym=sym, **kw)
        return df
    monkeypatch.setattr(yfinance, "download", dl)
    out = loaders.load_intraday(NSE, "INFY")
    assert calls["sym"] == "INFY.NS" and calls["period"] == "60d" and calls["interval"] == "15m"
    assert list(out.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert out.index.tz is None and out.index[0].hour == 9 and out.index[0].minute == 15
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.DataFrame())
    with pytest.raises(ValueError, match="no intraday data for INFY"):
        loaders.load_intraday(NSE, "INFY")


def test_cli_plan_positions_intraday_settings_text(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: (_ for _ in ()).throw(ValueError(f"no intraday data for {s}")))
    (tmp_path / "positions.json").write_text(json.dumps(
        [{"symbol": "ABC", "buy_price": 100, "stop_loss": 95, "quantity": 10, "status": "open"}]))
    foot = "Not financial advice. Nothing was placed. Paper-trade until your record matches the odds."
    assert cli.main(["positions"]) == 0
    assert foot in capsys.readouterr().out
    assert cli.main(["intraday", "--symbols", "AAA,BBB"]) == 0
    out = capsys.readouterr().out
    assert foot in out and "AAA" in out
    assert cli.main(["intraday", "--symbols", "AAA", "--json"]) == 0
    json.loads(capsys.readouterr().out)
    assert cli.main(["settings"]) == 0
    assert foot in capsys.readouterr().out
    assert cli.main(["settings", "--capital", "-1"]) == 2
    assert "error:" in capsys.readouterr().err
    assert cli.main(["positions", "--json"]) == 0
    json.loads(capsys.readouterr().out)


def test_cli_plan_runs(capsys, monkeypatch):
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(cli, "PLAN_UNIVERSE_LIMIT", 3, raising=False)
    assert cli.main(["plan", "--market", "US"]) == 0
    assert "Nothing was placed" in capsys.readouterr().out
    assert cli.main(["plan", "--market", "US", "--json"]) == 0
    json.loads(capsys.readouterr().out)


def test_cli_unknown_market(capsys):
    assert cli.main(["check", "ABC", "--market", "XX"]) == 2
    assert "unknown market" in capsys.readouterr().err


NEWS = Path(__file__).resolve().parents[2] / ".claude/skills/trader-news/news.py"


def _news():
    spec = importlib.util.spec_from_file_location("vendored_news", NEWS)
    m = importlib.util.module_from_spec(spec)
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # never leave __pycache__ under .claude/skills
    try:
        spec.loader.exec_module(m)  # import-time is side-effect free (defs and constants only)
    finally:
        sys.dont_write_bytecode = old
    return m


@pytest.mark.parametrize("bad", ["", "aapl", "A/../x", "AAPL&x=1", "1ABC", "A" * 11, "A B", "AA\n", "AAPL%0d"])
def test_vendored_news_rejects_bad_ticker(bad):
    m = _news()
    with pytest.raises(m.NewsError):
        m.validate_ticker(bad)
    if bad != "aapl":  # yahoo_symbol upper-cases first, so lower-case input is legitimately accepted
        with pytest.raises(m.NewsError):
            m.yahoo_symbol(bad)


def test_vendored_news_accepts_good_ticker():
    m = _news()
    assert m.validate_ticker("BRK.B") == "BRK.B" and m.yahoo_symbol("brk.b") == "BRK-B"
    assert m.main(["--tickers", "AAPL/../../x", "--dry-run"]) == 2


# ------------------------------------------------------------------ fix round 1

def _record_assess(monkeypatch):
    import components.risk_governor as rg
    seen = []
    class V:
        can_trade = True
        reasons = []
    monkeypatch.setattr(rg, "assess", lambda pos, jr, cap, regime=None: seen.append(regime) or V())
    return seen


def test_build_context_passes_regime_to_governor(monkeypatch):
    seen = _record_assess(monkeypatch)
    ctx = loaders.build_context(NSE, Limits(capital=5000), regime=None)
    assert seen[-1] == "HOSTILE" and ctx.regime is None
    ctx = loaders.build_context(NSE, Limits(capital=5000), regime="MIXED")
    assert seen[-1] == "MIXED" and ctx.regime == "MIXED"
    loaders.build_context(NSE, Limits(capital=5000), regime="weird")
    assert seen[-1] == "HOSTILE"


@pytest.mark.parametrize("fname,content", [("positions.json", "{not json"), ("trade_journal.json", '{"a": 1}')])
def test_build_context_unreadable_user_data_warns(tmp_path, monkeypatch, fname, content):
    seen = _record_assess(monkeypatch)
    (tmp_path / fname).write_text(content)
    ctx = loaders.build_context(NSE, Limits(capital=5000))
    assert ctx.governor_can_trade is None and ctx.journal == () and seen == []
    assert "Could not read" in ctx.governor_reasons[0]


def test_cli_positions_corrupt_exits_2(tmp_path, capsys):
    (tmp_path / "positions.json").write_text("[oops")
    assert cli.main(["positions"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error:") and "positions.json" in err


def test_cli_intraday_prints_full_take_block(capsys, monkeypatch):
    from nse_backtest.desk.service import check_trade
    from nse_backtest.desk.checks import CheckContext
    seen = {}
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: series())
    def fake_scan(profile, frames, limits, ctx, *, replays=3000, ctx_by_symbol=None, quotes=None):
        seen["ctx"] = (ctx_by_symbol or {}).get("AAA")
        v = check_trade(series(), "AAA", profile, __import__("nse_backtest.desk.models", fromlist=["Mode"]).Mode.SWING,
                        limits, ctx, robustness=False, replays=200, audit=False)
        return [v], []
    monkeypatch.setattr(session, "orb_scan", fake_scan)
    assert cli.main(["intraday", "--symbols", "AAA"]) == 0
    out = capsys.readouterr().out
    assert "Checks:" in out and ("PASS" in out or "WARN" in out)
    assert seen["ctx"] is not None and seen["ctx"].turnover_20d and seen["ctx"].turnover_20d > 0


def test_cli_settings_fallback_warning(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    (tmp_path / "desk_settings.json").write_text("garbage")
    assert cli.main(["check", "ABC"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("WARNING: settings file unreadable — using default capital ")
    assert loaders.load_limits_with_status(NSE)[1] is True
    (tmp_path / "desk_settings.json").unlink()
    assert loaders.load_limits_with_status(NSE)[1] is False


def test_cli_plan_passes_regime_and_earnings_lookup(offline_desk, capsys, monkeypatch):
    seen = _record_assess(monkeypatch)
    monkeypatch.setattr(cli, "PLAN_UNIVERSE_LIMIT", 2, raising=False)
    calls = []
    real = session.daily_plan
    monkeypatch.setattr(session, "daily_plan", lambda *a, **k: calls.append(k) or real(*a, **k))
    assert cli.main(["plan", "--market", "US"]) == 0
    assert seen and seen[0] in ("TRENDING", "MIXED", "HOSTILE")
    assert calls and callable(calls[0].get("earnings_lookup"))


def test_cli_settings_corrupt_warns_and_backs_up(tmp_path, capsys):
    p = tmp_path / "desk_settings.json"
    p.write_text("garbage")
    assert cli.main(["settings", "--market", "US"]) == 0
    assert capsys.readouterr().out.startswith("WARNING: settings file unreadable — using default capital ")
    assert not list(tmp_path.glob("desk_settings.json.corrupt-*"))  # read-only: nothing to back up
    assert cli.main(["settings", "--market", "US", "--capital", "2500"]) == 0
    backups = list(tmp_path.glob("desk_settings.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "garbage"
    assert loaders.load_limits(US).capital == 2500


def test_plan_watch_rows_list_check_names():
    from nse_backtest.desk.models import Action
    from nse_backtest.desk.daily import daily_plan
    from tests.desk.test_daily_positions_intraday import ctx as dctx, series as dseries
    dp = daily_plan(NSE, {"AAA": dseries()}, dseries(step=0.997), Limits(capital=100_000), dctx(), replays=300)
    row = cli._format_row(dp.watch[0], why=True)  # D2: no unused profile parameter
    assert "blocked by: regime" in row


def test_vendored_facts_rejects_non_digit_cik(capsys):
    path = Path(__file__).resolve().parents[2] / ".claude/skills/trader-analyst/facts.py"
    spec = importlib.util.spec_from_file_location("vendored_facts", path)
    m = importlib.util.module_from_spec(spec)
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(m)
    finally:
        sys.dont_write_bytecode = old
    for bad in ("../x", "12a", "12345678901", ""):
        assert m.main(["--cik", bad]) == 2
    assert "digits" in capsys.readouterr().err


def test_check_trade_does_not_halve_on_unreadable_journal(tmp_path):
    from nse_backtest.desk.checks import stoploss_streak
    (tmp_path / "trade_journal.json").write_text('{"a": 1}')
    ctx = loaders.build_context(NSE, Limits(capital=5000))
    assert ctx.journal == () and ctx.journal_readable is False
    assert stoploss_streak(ctx.journal, "NSE") is False  # service halves only on this


# ------------------------------------------------------------------ final fix wave A4-A6

def _intraday_raw(n=5, nan_last=False):
    idx = pd.date_range("2026-10-28 03:45", periods=n, freq="15min", tz="UTC")
    df = pd.DataFrame({c: np.linspace(100, 101, n) for c in ("Open", "High", "Low", "Close")} | {"Volume": 1e5}, index=idx)
    if nan_last:
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    return df


def test_a4_load_intraday_drops_trailing_nan_row(monkeypatch):
    import yfinance
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: _intraday_raw(nan_last=True))
    out = loaders.load_intraday(NSE, "INFY")
    assert len(out) == 4 and np.isfinite(out[["Open", "High", "Low", "Close"]].to_numpy()).all()
    assert out.index.is_monotonic_increasing and (out.index[-1].hour, out.index[-1].minute) == (10, 0)  # 10:15 NaN bar dropped


def test_a4_load_intraday_dedupes_and_sorts(monkeypatch):
    import yfinance
    raw = _intraday_raw()
    dup = raw.iloc[[-1]].copy()
    dup["Close"] = 999.0
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: pd.concat([raw.iloc[::-1], dup]))
    out = loaders.load_intraday(NSE, "INFY")
    assert out.index.is_monotonic_increasing and not out.index.has_duplicates and out["Close"].iloc[-1] == 999.0


def _patch_fetch(monkeypatch, frame):
    import nse_backtest.data as data
    seen = {}
    def fake(sym, start, end=None, exchange="NS", **k):
        seen.update(sym=sym, start=start, end=end, exchange=exchange)
        return frame.copy()
    monkeypatch.setattr(data, "fetch_nse", fake)
    return seen


def _daily_to(last: str, n=30):
    idx = pd.bdate_range(end=last, periods=n)
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": np.arange(n, dtype=float) + 100,
                         "Volume": 1e6}, index=idx)


def test_a4_load_daily_dedupes_duplicated_last_row(monkeypatch):
    f = _daily_to("2026-10-28")
    f = pd.concat([f, f.iloc[[-1]].assign(Close=555.0)])
    _patch_fetch(monkeypatch, f)
    now = datetime(2026, 10, 29, 10, 0, tzinfo=loaders.ZoneInfo(NSE.tz))
    out = loaders.load_daily(NSE, "INFY", now=now)
    assert not out.index.has_duplicates and len(out) == 30 and out["Close"].iloc[-1] == 555.0


def test_a5_load_daily_end_includes_today_and_drops_partial_bar(monkeypatch):
    seen = _patch_fetch(monkeypatch, _daily_to("2026-10-29"))
    tz = loaders.ZoneInfo(NSE.tz)
    during = loaders.load_daily(NSE, "INFY", now=datetime(2026, 10, 29, 11, 0, tzinfo=tz))
    assert seen["end"] == "2026-10-30"
    assert during.index[-1].date() == date(2026, 10, 28)  # today's bar is partial: dropped
    after = loaders.load_daily(NSE, "INFY", now=datetime(2026, 10, 29, 16, 0, tzinfo=tz))
    assert after.index[-1].date() == date(2026, 10, 29)  # completed bar kept


def _minute_frame(tz="UTC", nan_first_open=False, nan_last_close=False):
    idx = pd.date_range("2026-10-29 03:45", periods=4, freq="1min", tz=tz)
    df = pd.DataFrame({"Open": [101.0, 102.0, 103.0, 104.0], "High": 105.0, "Low": 100.0,
                       "Close": [101.5, 102.5, 103.5, 104.5], "Volume": 1e4}, index=idx)
    if nan_first_open:
        df.iloc[0, df.columns.get_loc("Open")] = np.nan
    if nan_last_close:
        df.iloc[-1, df.columns.get_loc("Close")] = np.nan
    return df


def test_a5_live_quote_parses_minute_bars(monkeypatch, real_live_quote):
    import yfinance
    calls = {}
    def dl(sym, **kw):
        calls.update(sym=sym, **kw)
        return _minute_frame(nan_first_open=True, nan_last_close=True)
    monkeypatch.setattr(yfinance, "download", dl)
    q = real_live_quote(NSE, "INFY")
    assert calls["sym"] == "INFY.NS" and calls["period"] == "1d" and calls["interval"] == "1m"
    assert calls["auto_adjust"] is False and calls["progress"] is False
    assert q.price == 103.5 and q.day_open == 102.0  # last finite close, first finite open
    assert q.as_of.tzinfo is not None and q.as_of.utcoffset().total_seconds() == 5.5 * 3600
    assert (q.as_of.hour, q.as_of.minute) == (9, 17)  # time of the bar the price came from


@pytest.mark.parametrize("ret", [pd.DataFrame(), None, RuntimeError("down")])
def test_a5_live_quote_failure_is_none(monkeypatch, real_live_quote, ret):
    import yfinance
    def dl(*a, **k):
        if isinstance(ret, Exception):
            raise ret
        return ret
    monkeypatch.setattr(yfinance, "download", dl)
    assert real_live_quote(US, "AAPL") is None


def test_a6_load_regime_failure_reaches_on_warning(monkeypatch):
    import nse_backtest.data as data
    def boom(*a, **k):
        raise RuntimeError("yfinance failed after 3 attempts")
    monkeypatch.setattr(data, "fetch_nse", boom)
    warns = []
    assert session.resolve_regime(NSE, warns.append) is None
    assert warns and "yfinance failed after 3 attempts" in warns[0]



# ------------------------------------------------------------------ final fix wave A10
def _journal(tmp_path, rows):
    (tmp_path / "trade_journal.json").write_text(json.dumps(rows))


US_NOW = datetime(2026, 10, 29, 11, 0, tzinfo=loaders.ZoneInfo(US.tz))  # Thursday


def test_a10_pdt_counts_same_day_round_trips_in_last_5_weekdays(tmp_path):
    rows = [
        {"market": "US", "symbol": "A", "entry_date": "2026-10-29T09:40", "closed_date": "2026-10-29T10:10", "pnl": 1},
        {"market": "US", "symbol": "B", "open_date": "2026-10-23", "exit_date": "2026-10-23", "pnl": 1},  # Fri, in window
        {"market": "US", "symbol": "C", "entry_date": "2026-10-22", "closed_date": "2026-10-22", "pnl": 1},  # Thu last week: out
        {"market": "US", "symbol": "D", "entry_date": "2026-10-27", "closed_date": "2026-10-28", "pnl": 1},  # swing: not a day trade
        {"market": "NSE", "symbol": "E", "entry_date": "2026-10-29", "closed_date": "2026-10-29", "pnl": 1},
    ]
    _journal(tmp_path, rows)
    ctx = loaders.build_context(US, Limits(capital=5000, pdt_guard=True), now=US_NOW)
    assert ctx.day_trades_5d == 2


def test_a10_pdt_unknowable_without_entry_dates(tmp_path):
    _journal(tmp_path, [{"market": "US", "symbol": "A", "closed_date": "2026-10-28", "pnl": 1}])
    ctx = loaders.build_context(US, Limits(capital=5000, pdt_guard=True), now=US_NOW)
    assert ctx.day_trades_5d is None
    _journal(tmp_path, [])
    assert loaders.build_context(US, Limits(capital=5000, pdt_guard=True), now=US_NOW).day_trades_5d == 0


# ------------------------------------------------------------------ final fix wave B1, B3, B4
BAD_SYM = "../../v7/x?a=1"


def test_b3_symbol_validated_before_any_yfinance_call(monkeypatch, real_live_quote):
    import yfinance
    def never(*a, **k):
        raise AssertionError("yfinance must not be called for an invalid symbol")
    monkeypatch.setattr(yfinance, "download", never)
    monkeypatch.setattr(yfinance, "Ticker", never)
    with pytest.raises(ValueError):
        loaders.load_intraday(NSE, BAD_SYM)
    assert loaders.next_earnings(NSE, BAD_SYM) == (None, False)
    assert real_live_quote(NSE, BAD_SYM) is None


def test_b3_index_symbol_with_caret_is_allowed(monkeypatch):
    import yfinance
    seen = []
    monkeypatch.setattr(yfinance, "download", lambda sym, **k: seen.append(sym) or _intraday_raw())
    loaders.load_intraday(US, "^GSPC")
    assert seen == ["^GSPC"]


def test_b4_audit_file_and_data_dir_are_owner_only(tmp_path, monkeypatch):
    import os, stat
    from nse_backtest.desk import audit
    from nse_backtest.desk.verdict import decide
    from tests.desk.test_verdict_service import P, S, OK, NOW
    d = tmp_path / "fresh"
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(d))
    assert stat.S_IMODE(os.stat(audit.data_dir()).st_mode) == 0o700
    path = d / "a.jsonl"
    path.write_text("")
    os.chmod(path, 0o644)  # pre-existing loose file
    audit.append_verdict(decide(P, S, None, OK, NSE, 100.0, NOW), path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    os.chmod(d, 0o755)
    audit.data_dir()
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700


def test_b1_vendored_skills_do_not_point_at_a_broker():
    root = Path(__file__).resolve().parents[2] / ".claude/skills"
    for name in ("trader-news", "trader-analyst"):
        text = (root / name / "SKILL.md").read_text()
        assert "/trader-broker" not in text
        assert "This repo never places orders; there is no broker skill." in text
    assert "N8" in (root / "VENDORED.md").read_text()


# ---- Last round: partial bar via cache ----
def _recording_fetch(monkeypatch, frame):
    import nse_backtest.data as data
    calls = []
    def fake(sym, start, end=None, exchange="NS", use_cache=True, **k):
        calls.append(use_cache)
        return frame.copy()
    monkeypatch.setattr(data, "fetch_nse", fake)
    return calls


def test_lr_load_daily_after_close_refetches_without_cache(monkeypatch):
    calls = _recording_fetch(monkeypatch, _daily_to("2026-10-29"))
    tz = loaders.ZoneInfo(NSE.tz)
    loaders.load_daily(NSE, "INFY", now=datetime(2026, 10, 29, 15, 45, tzinfo=tz))
    assert calls == [True, False]


def test_lr_load_daily_no_refetch_in_session_late_or_weekend(monkeypatch):
    tz = loaders.ZoneInfo(NSE.tz)
    calls = _recording_fetch(monkeypatch, _daily_to("2026-10-29"))
    loaders.load_daily(NSE, "INFY", now=datetime(2026, 10, 29, 11, 0, tzinfo=tz))
    loaders.load_daily(NSE, "INFY", now=datetime(2026, 10, 29, 20, 0, tzinfo=tz))  # >3h after close
    assert calls == [True, True]
    calls = _recording_fetch(monkeypatch, _daily_to("2026-10-28"))  # last bar not today
    loaders.load_daily(NSE, "INFY", now=datetime(2026, 10, 29, 15, 45, tzinfo=tz))
    assert calls == [True]


def test_lr_load_daily_us_after_close_uses_market_tz(monkeypatch):
    et = loaders.ZoneInfo(US.tz)
    calls = _recording_fetch(monkeypatch, _daily_to("2026-10-29"))
    loaders.load_daily(US, "AAPL", now=datetime(2026, 10, 29, 16, 30, tzinfo=et))
    assert calls == [True, False]
