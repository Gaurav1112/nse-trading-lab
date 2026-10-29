import numpy as np, pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def _no_live_quote(monkeypatch):
    """No network from the page: the live quote is unavailable unless a test stubs one."""
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "live_quote", lambda p, s: None)


def series(n=400):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def test_desk_page_renders_and_checks_a_trade(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Today's Plan", "Check a Trade", "My Positions", "Intraday"]
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    assert any(w in " ".join(m.value for m in at.markdown) for w in ("TAKE", "WAIT", "SKIP"))


def test_desk_page_survives_loader_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from nse_backtest.desk import loaders

    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(loaders, "load_daily", boom)
    monkeypatch.setattr(loaders, "load_regime", boom)
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, False))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert not at.exception
    assert any("HOSTILE" in w.value for w in at.warning)
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    assert any("network down" in e.value for e in at.error)


# ---------------------------------------------------------------- fix round 1

def _patch_ok(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    return loaders


def test_positions_error_persists_across_reruns(monkeypatch, tmp_path):
    loaders = _patch_ok(monkeypatch, tmp_path)

    def bad():
        raise loaders.UserDataError("positions.json is corrupt")
    monkeypatch.setattr(loaders, "load_positions", bad)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert any("positions.json is corrupt" in e.value for e in at.error)
    at.run()
    assert any("positions.json is corrupt" in e.value for e in at.error)
    assert not any("No open" in i.value for i in at.info)


def test_regime_failure_is_retried_not_cached(monkeypatch, tmp_path):
    loaders = _patch_ok(monkeypatch, tmp_path)
    calls = {"n": 0}

    def flaky(p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("blip")
        return series()
    monkeypatch.setattr(loaders, "load_regime", flaky)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert any("HOSTILE" in w.value for w in at.warning)
    at.run()
    assert not any("HOSTILE" in w.value for w in at.warning)


def _verdict_with(checks):
    from dataclasses import replace
    from nse_backtest.desk import session
    from nse_backtest.desk.models import Mode
    v = session.run_check(session.get_profile("NSE") if hasattr(session, "get_profile") else
                          __import__("nse_backtest.desk.markets", fromlist=["x"]).get_profile("NSE"),
                          "ABC", Mode("swing"), session.loaders.default_limits(
                              __import__("nse_backtest.desk.markets", fromlist=["x"]).get_profile("NSE")))
    return replace(v, checks=tuple(checks))


def _summary_md(monkeypatch, tmp_path, checks):
    loaders = _patch_ok(monkeypatch, tmp_path)
    from nse_backtest.desk import session
    v = _verdict_with(checks)
    monkeypatch.setattr(session, "run_check", lambda *a, **k: v)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    return [m.value for m in at.markdown]


def test_verdict_summary_line(monkeypatch, tmp_path):
    from nse_backtest.desk.models import Check, Status
    both = [Check("regime", Status.BLOCK, "x"), Check("earnings", Status.WARN, "y"),
            Check("gap", Status.WARN, "z"), Check("ok", Status.PASS, "")]
    md = _summary_md(monkeypatch, tmp_path, both)
    line = next(m for m in md if "BLOCK: regime" in m)
    assert "1 BLOCK" in line and "2 WARN" in line and "WARN: earnings, gap" in line and "#FF3355" in line
    warn_only = _summary_md(monkeypatch, tmp_path, [Check("gap", Status.WARN, "z")])
    line = next(m for m in warn_only if "WARN: gap" in m)
    assert "#FFB800" in line and "BLOCK" not in line.replace("0 BLOCK", "")
    clean = _summary_md(monkeypatch, tmp_path, [Check("a", Status.PASS, "")])
    line = next(m for m in clean if "checks passed" in m)
    assert "All 1 checks passed" in line and "#00C46A" in line


def test_watch_distance_guards_bad_price(monkeypatch, tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import session
    from nse_backtest.desk.daily import DailyPlan
    _patch_ok(monkeypatch, tmp_path)
    v = replace(_verdict_with([]), current_price=0.0, trigger=100.0)
    plan = DailyPlan("NSE", None, "", (), (), (v,), (), ())
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: plan)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    assert not at.exception
    assert any(df.value["Distance %"].isna().all() for df in at.dataframe
               if "Distance %" in df.value.columns)


def test_check_result_not_shown_for_edited_inputs(monkeypatch, tmp_path):
    _patch_ok(monkeypatch, tmp_path)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert any("Plan" == m.value.strip("*") for m in at.markdown)
    at.number_input(key="desk_stop").set_value(1.0).run()
    assert not any("Plan" == m.value.strip("*") for m in at.markdown)


def _market_today(tz="Asia/Kolkata"):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def test_intraday_scan_cache_key_has_date(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    monkeypatch.setattr(session, "run_intraday", lambda *a, **k: ([], []))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_scan").click().run()
    assert str(("intraday", "NSE", _market_today())) in at.session_state.filtered_state  # A11: market-tz date


# ---------------------------------------------------------------- fix round 2

def _errors(at):
    return " | ".join(e.value for e in at.error)


def _plan_with(verdicts_watch=(), take=()):
    from nse_backtest.desk.daily import DailyPlan
    return DailyPlan("NSE", None, "", tuple(take), (), tuple(verdicts_watch), (), ())


def test_empty_checks_never_green(monkeypatch, tmp_path):
    md = _summary_md(monkeypatch, tmp_path, [])
    joined = " ".join(md)
    assert "No checks were run" in joined and "do not trade" in joined
    assert not any("checks passed" in m for m in md)
    line = next(m for m in md if "No checks were run" in m)
    assert "#FFB800" in line


def test_intraday_error_persists_across_rerun(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)

    def bad(*a, **k):
        raise RuntimeError("feed down")
    monkeypatch.setattr(session, "run_intraday", bad)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_scan").click().run()
    assert "feed down" in _errors(at)
    at.run()
    assert "feed down" in _errors(at)
    assert not any("No setups" in i.value for i in at.info)


def test_intraday_failure_replaces_old_success(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    v = _verdict_with([])
    calls = {"n": 0}

    def scan(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return ([v], [])
        raise RuntimeError("second scan broke")
    monkeypatch.setattr(session, "run_intraday", scan)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_scan").click().run()
    assert any("Symbol" in df.value.columns and "MC R" in df.value.columns for df in at.dataframe)
    assert any("Built at" in c.value for c in at.caption)
    at.button(key="desk_scan").click().run()
    assert "second scan broke" in _errors(at)
    assert not any("MC R" in df.value.columns for df in at.dataframe)
    at.run()
    assert "second scan broke" in _errors(at)
    assert not any("MC R" in df.value.columns for df in at.dataframe)


def test_plan_error_persists_and_replaces_success(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    calls = {"n": 0}
    good = _plan_with()

    def build(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return good
        raise RuntimeError("universe scan broke")
    monkeypatch.setattr(session, "run_daily_plan", build)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    assert any("Nothing qualifies" in i.value for i in at.info)
    assert any("Built at" in c.value for c in at.caption)
    at.button(key="desk_build_plan").click().run()
    assert "universe scan broke" in _errors(at)
    at.run()
    assert "universe scan broke" in _errors(at)
    assert not any("Nothing qualifies" in i.value for i in at.info)


def test_check_error_persists_and_replaces_success(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    real = session.run_check
    calls = {"n": 0}

    def chk(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return real(*a, **k)
        raise ValueError("symbol XYZ not found")
    monkeypatch.setattr(session, "run_check", chk)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert any("Plan" == m.value.strip("*") for m in at.markdown)
    assert any("Checked at" in c.value for c in at.caption)
    at.button(key="desk_check").click().run()
    assert "symbol XYZ not found" in _errors(at)
    assert not any("Plan" == m.value.strip("*") for m in at.markdown)
    at.run()
    assert "symbol XYZ not found" in _errors(at)
    assert not any("Plan" == m.value.strip("*") for m in at.markdown)


def test_check_names_are_html_escaped(monkeypatch, tmp_path):
    from nse_backtest.desk.models import Check, Status
    md = _summary_md(monkeypatch, tmp_path, [Check("<b>x</b>&y", Status.WARN, "r")])
    joined = " ".join(md)
    assert "<b>x</b>" not in joined and "&lt;b&gt;x&lt;/b&gt;&amp;y" in joined


def test_watch_distance_is_numeric(monkeypatch, tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    v = replace(_verdict_with([]), current_price=100.0, trigger=105.0)
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: _plan_with([v]))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    frames = [df.value for df in at.dataframe if "Distance %" in df.value.columns]
    assert frames and pd.api.types.is_numeric_dtype(frames[0]["Distance %"])
    assert abs(frames[0]["Distance %"].iloc[0] - 5.0) < 1e-9


def test_positions_refresh_recovers_from_error(monkeypatch, tmp_path):
    loaders = _patch_ok(monkeypatch, tmp_path)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise loaders.UserDataError("positions.json is corrupt")
        return []
    monkeypatch.setattr(loaders, "load_positions", flaky)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert "positions.json is corrupt" in _errors(at)
    at.button(key="desk_refresh_pos").click().run()
    assert "positions.json is corrupt" not in _errors(at)
    assert any("No open" in i.value for i in at.info)


# ---------------------------------------------------------------- final fix wave A6/A7

def test_a6_stale_regime_warns_and_is_not_cached(monkeypatch, tmp_path):
    loaders = _patch_ok(monkeypatch, tmp_path)
    stale = series()
    stale.index = stale.index - pd.Timedelta(days=10)
    calls = {"n": 0}

    def regime(p):
        calls["n"] += 1
        return stale if calls["n"] == 1 else series()
    monkeypatch.setattr(loaders, "load_regime", regime)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert any("HOSTILE" in w.value and "stale" in w.value for w in at.warning)
    at.run()
    assert calls["n"] == 2 and not any("HOSTILE" in w.value for w in at.warning)


def test_a7_plan_uses_header_regime_and_tape(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    seen = {}

    def build(*a, **k):
        seen.update(k)
        return _plan_with()
    monkeypatch.setattr(session, "run_daily_plan", build)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    assert seen.get("regime") == "TRENDING" and seen.get("tape") is not None
    assert seen["tape"].regime == "TRENDING"


# ---------------------------------------------------------------- final fix wave A11

def test_a11_check_cache_key_has_market_date_and_limits(monkeypatch, tmp_path):
    loaders = _patch_ok(monkeypatch, tmp_path)
    from nse_backtest.desk.models import Limits
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert any("Plan" == m.value.strip("*") for m in at.markdown)
    keys = [k for k in at.session_state.filtered_state if k.startswith("('check'")]
    assert keys and all(_market_today() in k for k in keys)
    cap = next(c.value for c in at.caption if "Checked at" in c.value)
    assert _market_today() in cap
    # the limits changed (e.g. saved elsewhere): the old verdict must not be shown for the new limits
    monkeypatch.setattr(loaders, "load_limits_with_status", lambda p: (Limits(capital=55_555), False))
    at.run()
    assert not any("Plan" == m.value.strip("*") for m in at.markdown)


# ---------------------------------------------------------------- final fix wave C1/C4

def test_c1_page_noise_label(monkeypatch, tmp_path):
    _patch_ok(monkeypatch, tmp_path)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    labels = [m.label for m in at.metric]
    assert "Candle-noise expectancy range" in labels and not any("shuffled" in l for l in labels)


def _pos_rows():
    return [{"symbol": "ABC", "buy_price": 100, "stop_loss": 95, "quantity": 10, "status": "open"},
            {"symbol": "NODATA", "buy_price": 50, "stop_loss": 45, "quantity": 1, "status": "open"}]


def test_c4_plan_tab_counts_positions_needing_action(monkeypatch, tmp_path):
    loaders = _patch_ok(monkeypatch, tmp_path)
    from nse_backtest.desk import session
    monkeypatch.setattr(loaders, "load_positions", _pos_rows)
    real = loaders.load_daily
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: (_ for _ in ()).throw(ValueError("none"))
                        if s == "NODATA" else real(p, s))
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: _plan_with())
    from types import SimpleNamespace
    from nse_backtest import position_monitor  # its scorer would call yfinance for earnings
    monkeypatch.setattr(position_monitor, "daily_check", lambda p, df: SimpleNamespace(
        action="HOLD", current_price=float(df["Close"].iloc[-1]), suggested_sl=None, pnl_pct=1.0, reason="ok"))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    first = " ".join(m.value for m in at.markdown) + " ".join(i.value for i in at.info)
    assert "Open My Positions to review" in first  # positions are loaded after the plan tab on the first run
    at.run()
    text = " ".join(m.value for m in at.markdown)
    assert "1 position needs action" in text  # NODATA (DATA UNAVAILABLE); ABC is HOLD
