from nse_backtest.desk import loaders, session
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import DeskVerdict, Limits, Mode
from tests.desk.test_cli_loaders import series


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
