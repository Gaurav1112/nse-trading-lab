import importlib.util
import json
import math
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nse_backtest.desk import cli, loaders
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
    assert loaders.load_positions() == [] and loaders.load_journal() == []
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
    spec.loader.exec_module(m)  # import-time is side-effect free (defs and constants only)
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
