import numpy as np, pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from tests.desk.test_loaders_many import via_load_daily

from pathlib import Path as _Path

_REPO = _Path(__file__).resolve().parents[1]  # AppTest paths: absolute, independent of cwd


def _session_keys(at) -> list[str]:
    """Session-state keys across Streamlit versions (1.66 AppTest has keys(); older ones only filtered_state)."""
    ss = at.session_state
    return list(ss.keys()) if hasattr(ss, "keys") else list(ss.filtered_state)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Page tests never touch the network: any socket connect fails (as in tests/desk/conftest.py)."""
    import socket

    def _blocked(*args, **kwargs):
        raise OSError("network access is blocked in page tests")
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture(autouse=True)
def _no_live_quote(monkeypatch):
    """No network from the page: the live quote is unavailable unless a test stubs one."""
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "live_quote", lambda p, s: None)


def _last_completed_session(prof=None):
    """The newest NSE session that has closed as of the wall clock, so the swing freshness check passes at any
    time of day (a fixed "yesterday" goes stale once today's session closes at 15:30 IST)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from nse_backtest.desk import calendars as cal
    from nse_backtest.desk.markets import NSE
    prof = prof or NSE
    now = datetime.now(ZoneInfo(prof.tz))
    d = now.date()
    if cal.is_trading_day(prof, d) and now.time() >= cal.session_close_for(prof, d):
        return d
    return cal.previous_trading_day(prof, d)


def series(n=400):
    idx = pd.bdate_range(end=pd.Timestamp(_last_completed_session()), periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def test_desk_page_renders_and_checks_a_trade(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_daily_many", via_load_daily)  # S4: load_frames batches
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Choose a Trade", "Today's Plan", "Check a Trade", "My Positions",
                                          "Intraday", "Scoreboard"]
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
    monkeypatch.setattr(loaders, "load_daily_many", via_load_daily)  # S4: load_frames batches
    monkeypatch.setattr(loaders, "load_regime", boom)
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, False))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    monkeypatch.setattr(loaders, "load_daily_many", via_load_daily)  # S4: load_frames batches
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    assert any("positions.json is corrupt" in _plain(e.value) for e in at.error)
    at.run()
    assert any("positions.json is corrupt" in _plain(e.value) for e in at.error)
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    assert not at.exception
    assert any(df.value["Distance %"].isna().all() for df in at.dataframe
               if "Distance %" in df.value.columns)


def test_check_result_not_shown_for_edited_inputs(monkeypatch, tmp_path):
    _patch_ok(monkeypatch, tmp_path)
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.button(key="desk_scan").click().run()
    keys = [k for k in _session_keys(at) if k.startswith("('intraday', 'NSE', '" + _market_today())]
    assert len(keys) == 1 and len(eval(keys[0])) == 4  # noqa  # market-tz date and the limits hash


# ---------------------------------------------------------------- fix round 2

def _plain(text: str) -> str:
    """Markdown-escaped page text (F1 D2: exception reasons go through textsafe.shown) as the reader sees it."""
    import re
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!|<>~$:])", r"\1", text)


def _errors(at):
    return _plain(" | ".join(e.value for e in at.error))


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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    assert seen.get("regime") == "TRENDING" and seen.get("tape") is not None
    assert seen["tape"].regime == "TRENDING"


# ---------------------------------------------------------------- final fix wave A11

def test_a11_check_cache_key_has_market_date_and_limits(monkeypatch, tmp_path):
    loaders = _patch_ok(monkeypatch, tmp_path)
    from nse_backtest.desk.models import Limits
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert any("Plan" == m.value.strip("*") for m in at.markdown)
    keys = [k for k in _session_keys(at) if k.startswith("('check'")]
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
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
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    first = " ".join(m.value for m in at.markdown) + " ".join(i.value for i in at.info)
    assert "Open My Positions to review" in first  # positions are loaded after the plan tab on the first run
    at.run()
    text = " ".join(m.value for m in at.markdown)
    assert "1 position needs action" in text  # NODATA (DATA UNAVAILABLE); ABC is HOLD


# ---------------------------------------------------------------- C7: Choose a Trade

def _cands():
    """TAKE (ABC swing), WAIT (WTT), SKIP (SKP) from a real offline check, plus a scan SKIP (INT intraday)."""
    from dataclasses import replace
    from nse_backtest.desk.markets import NSE
    from nse_backtest.desk.models import Action, Check, Mode, Status
    from nse_backtest.desk.verdict import ticket_lines
    base = _verdict_with([Check("regime", Status.PASS, "")])
    assert base.sizing.qty > 0 and base.odds is not None and base.odds.mc_rs
    take = replace(base, action=Action.TAKE, input_hash="t" * 64, headline="TAKE ABC",
                   ticket=ticket_lines(base.plan, base.sizing, NSE))
    wait = replace(base, action=Action.WAIT, plan=replace(base.plan, symbol="WTT"), input_hash="w" * 64,
                   headline="WAIT WTT — buy only above", ticket=("IF TRIGGERED:",), checks=())
    skip = replace(base, action=Action.SKIP, plan=replace(base.plan, symbol="SKP"), input_hash="s" * 64,
                   headline="SKIP SKP — blocked by: liquidity", ticket=(),  # H3: a limit, so it can be overridden
                   checks=(Check("liquidity", Status.BLOCK, "too thin"), Check("gap", Status.WARN, "gap wide")))
    intra = replace(base, action=Action.SKIP, plan=replace(base.plan, symbol="INT", mode=Mode.INTRADAY),
                    input_hash="i" * 64, headline="SKIP INT", ticket=(),
                    checks=(Check("window", Status.BLOCK, "outside the entry window"),))
    return take, wait, skip, intra


def _choose_page(monkeypatch, tmp_path, *, overrides=None):
    """The page with a fake plan (TAKE, WAIT, SKIP + one skipped symbol) and a fake scan (one SKIP)."""
    from nse_backtest.desk import audit, session
    from nse_backtest.desk.daily import DailyPlan
    _patch_ok(monkeypatch, tmp_path)
    take, wait, skip, intra = _cands()
    calls = {"plan": 0, "scan": 0, "override": []}

    def plan(*a, **k):
        calls["plan"] += 1
        return DailyPlan("NSE", None, "", (), (take,), (wait, skip), (("ZZZ", "no setup today"),), ())

    def scan(*a, **k):
        calls["scan"] += 1
        return [intra], []
    monkeypatch.setattr(session, "run_daily_plan", plan)
    monkeypatch.setattr(session, "run_intraday", scan)
    monkeypatch.setattr(audit, "append_override", lambda v, path=None: calls["override"].append(v))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    assert not at.exception
    return at, calls


def _load(at):
    at.button(key="desk_choose_load").click().run()
    assert not at.exception
    return at


def _pick(at, prefix):
    sb = at.selectbox(key="desk_choose_pick")
    sb.set_value(next(o for o in sb.options if o.startswith(prefix))).run()
    assert not at.exception
    return at


def _texts(els):
    return _plain(" | ".join(e.value for e in els))


def _choose_children(at):
    return list(at.tabs[0].children.values())


def _codes(at):
    return " | ".join(c.value for c in at.tabs[0].code)  # Today's Plan also shows the TAKE's ticket


def test_c7_choose_tab_is_first_and_loads_only_on_the_button(monkeypatch, tmp_path):
    at, calls = _choose_page(monkeypatch, tmp_path)
    assert [t.label for t in at.tabs] == ["Choose a Trade", "Today's Plan", "Check a Trade", "My Positions",
                                          "Intraday", "Scoreboard"]
    assert calls["plan"] == 0 and calls["scan"] == 0
    assert any("Load candidates" in i.value for i in at.info)
    at.run()
    assert calls["plan"] == 0 and calls["scan"] == 0
    _load(at)
    assert calls == {"plan": 1, "scan": 1, "override": []}
    at.run()
    assert calls["plan"] == 1 and calls["scan"] == 1  # never on every rerun


def test_c7_candidates_table_columns_money_and_order(monkeypatch, tmp_path):
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    want = ["Symbol", "Mode", "Desk says", "Entry", "Stop", "T1", "T2", "Qty", "Max loss", "Profit T1", "Win odds"]
    df = next(d.value for d in at.dataframe if "Desk says" in d.value.columns)
    assert all(c in df.columns for c in want)
    assert list(df["Symbol"]) == ["ABC", "WTT", "SKP", "INT", "ZZZ"]
    assert list(df["Desk says"]) == ["TAKE", "WAIT", "SKIP", "SKIP", "SKIP"]
    assert df["Entry"].iloc[0].startswith("₹") and df["Max loss"].iloc[0].startswith("₹")
    assert df["Win odds"].iloc[0].endswith("%")
    zzz = df[df["Symbol"] == "ZZZ"].iloc[0]
    assert zzz["Entry"] == "—" and zzz["Max loss"] == "—" and "no setup today" in zzz["Why"]


def test_c7_reuses_plan_and_scan_already_built_on_their_tabs(monkeypatch, tmp_path):
    at, calls = _choose_page(monkeypatch, tmp_path)
    at.button(key="desk_build_plan").click().run()
    at.button(key="desk_scan").click().run()
    assert calls["plan"] == 1 and calls["scan"] == 1
    assert any("Desk says" in d.value.columns for d in at.dataframe)  # no Load press needed
    assert not any("Load candidates" in i.value for i in at.info)
    assert calls["plan"] == 1 and calls["scan"] == 1


def test_c7_selected_row_renders_card_metrics(monkeypatch, tmp_path):
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    labels = {m.label: m.value for m in at.metric}
    for name in ("Max loss", "Profit at T1", "Profit at T2", "Risk:Reward", "Expected value"):
        assert name in labels, name
    assert labels["Max loss"].startswith("₹") and labels["Profit at T1"].startswith("₹")
    assert "BUY" in _codes(at)  # TAKE: ticket straight away
    assert "YOUR OVERRIDE" not in _texts(at.markdown) + _texts(at.warning)


def test_c7_skip_override_reveals_ticket_and_logs_once_per_hash_and_qty(monkeypatch, tmp_path):
    at, calls = _choose_page(monkeypatch, tmp_path)
    _pick(_load(at), "SKIP SKP")
    assert "BLOCK liquidity — too thin" in _texts(at.error) and "WARN gap — gap wide" in _texts(at.warning)
    assert "BUY" not in _codes(at)
    assert calls["override"] == []
    box = next(c for c in at.checkbox if c.label == "I choose this anyway")
    box.check().run()
    assert not at.exception
    assert "BUY" in _codes(at) and "SKP" in _codes(at)
    assert "YOUR OVERRIDE — the desk said SKIP" in _texts(at.markdown) + _texts(at.warning)
    assert len(calls["override"]) == 1 and calls["override"][0].plan.symbol == "SKP"
    at.run()
    at.run()
    assert len(calls["override"]) == 1  # reruns never log again
    first = int(next(n for n in at.number_input if n.label == "Quantity").value)
    assert calls["override"][0].sizing.qty == first
    assert f"Logged once as your override at qty {first}" in _texts(at.caption)
    qty = next(n for n in at.number_input if n.label == "Quantity")
    qty.set_value(first + 1).run()
    assert len(calls["override"]) == 1  # later qty changes add no rows
    assert f"Logged once as your override at qty {first}" in _texts(at.caption)
    assert f"BUY {first + 1} SKP" in _codes(at)


def test_c7_wait_shows_desk_conditional_ticket_and_override_adds_take_now(monkeypatch, tmp_path):
    at, calls = _choose_page(monkeypatch, tmp_path)
    _pick(_load(at), "WAIT WTT")
    assert "WAIT WTT — buy only above" in _texts(at.error)
    assert "IF TRIGGERED" in _codes(at)  # the desk's own conditional ticket, no override needed
    assert "conditional ticket" in _texts(at.tabs[0].markdown)
    assert "YOUR OVERRIDE" not in _texts(at.warning) and calls["override"] == []
    next(c for c in at.checkbox if c.label == "I choose this anyway").check().run()
    assert "YOUR OVERRIDE — the desk said WAIT" in _texts(at.warning)
    codes = [c.value for c in at.tabs[0].code]
    assert any(c.startswith("IF TRIGGERED") for c in codes) and any(c.startswith("BUY") for c in codes)
    assert len(calls["override"]) == 1


def test_c7_qty_above_sized_shows_breaches_in_red(monkeypatch, tmp_path):
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    qty = next(n for n in at.number_input if n.label == "Quantity")
    qty.set_value(int(qty.value) * 50).run()
    assert not at.exception
    errs = _texts(at.error)
    assert "Breaks your limits" in errs and "of capital" in errs


def test_c7_failing_sections_show_their_own_error_and_the_rest_renders(monkeypatch, tmp_path):
    from nse_backtest.desk import charts, projections

    def boom(*a, **k):
        raise RuntimeError("outcome exploded")

    def boom2(*a, **k):
        raise RuntimeError("account exploded")
    monkeypatch.setattr(charts, "outcome_chart", boom)
    monkeypatch.setattr(projections, "account_projection", boom2)
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    errs = _texts(at.error)
    assert "outcome exploded" in errs and "account exploded" in errs
    assert "Max loss" in [m.label for m in at.metric] and "BUY" in _codes(at)
    assert len(at.get("plotly_chart")) >= 1  # the price chart still renders


def test_c7_price_chart_load_failure_is_unavailable_only_there(monkeypatch, tmp_path):
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    from nse_backtest.desk import loaders

    n = {"calls": 0}

    def counting(*a, **k):
        n["calls"] += 1
        raise RuntimeError("bars down")
    monkeypatch.setattr(loaders, "load_daily", counting)
    _pick(at, "WAIT WTT")  # a symbol whose bars are not cached yet
    assert "unavailable: bars down" in _texts(at.error) and "retry in" in _texts(at.error)
    assert "Max loss" in [m.label for m in at.metric]
    at.run()
    at.run()
    assert n["calls"] == 1  # a failure is not retried on every rerun
    next(b for b in at.button if b.label == "Retry").click().run()
    assert n["calls"] == 2


def test_c7_charts_are_plotly_with_c5_figures_and_captioned(monkeypatch, tmp_path):
    import json
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    titles = [json.loads(p.proto.spec)["layout"].get("title", {}).get("text", "") for p in at.get("plotly_chart")]
    assert any(t.startswith("Money outcome per trade") for t in titles)
    assert any(t.startswith("Price and levels") for t in titles)
    assert any(t.startswith("Account after 20 trades") for t in titles)
    assert sum("range of past behaviour, not a forecast" in c.value for c in at.caption) >= 3
    n = next(r for r in at.radio if r.label.startswith("Trades to project"))
    assert list(n.options) == ["10", "20", "50"] and n.value == 20
    n.set_value(50).run()
    titles = [json.loads(p.proto.spec)["layout"].get("title", {}).get("text", "") for p in at.get("plotly_chart")]
    assert any(t.startswith("Account after 50 trades") for t in titles)


def test_c7_research_loads_on_the_button_caches_and_shows_unavailable(monkeypatch, tmp_path):
    from nse_backtest.desk import loaders, research
    calls = []

    def actions(p, s):
        calls.append(s)
        raise ValueError("no corporate actions for ABC")
    monkeypatch.setattr(loaders, "corporate_actions", actions)  # F1 E2: earnings unknown too -> Unavailable
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    assert calls == []  # slow: never without the button
    btn = next(b for b in at.button if b.label == "Load news & earnings")
    btn.click().run()
    assert not at.exception and calls == ["ABC"]
    assert "no corporate actions for ABC" in _texts(at.warning)
    at.run()
    assert calls == ["ABC"] and "no corporate actions for ABC" in _texts(at.warning)  # shown, not refetched
    next(b for b in at.button if b.label == "Load news & earnings").click().run()
    assert calls == ["ABC", "ABC"]  # an unavailable result never blocks a retry


def test_c7_research_items_and_reasons_are_shown(monkeypatch, tmp_path):
    from nse_backtest.desk import loaders, research
    seen = {}
    monkeypatch.setattr(loaders, "corporate_actions", lambda p, s: pd.DataFrame({"Dividends": [], "Stock Splits": []}))

    def nse(sym, profile, *, earnings=None, actions=None, actions_error=None):
        seen.update(sym=sym, earnings=earnings, actions=actions)
        return research.Research("NSE", "2026-10-29T05:00:00+00:00",
                                 (research.ResearchItem("Dividend 5 per share", None, "2026-08-01", "dividend"),
                                  research.ResearchItem("Q2 *beats* [x](y)", "https://e.test/a(1)", None, "headline")),
                                 ("earnings date unknown",))
    monkeypatch.setattr(research, "nse_research", nse)
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    next(b for b in at.button if b.label == "Load news & earnings").click().run()
    assert seen["sym"] == "ABC" and seen["actions"] is not None
    seen.clear()
    next(b for b in at.button if b.label == "Load news & earnings").click().run()
    assert seen == {}  # a success stays cached for 30 minutes
    text = _texts(at.markdown) + _texts(at.caption)
    assert "Dividend 5 per share" in text and "earnings date unknown" in text and "display only" in text.lower()
    raw = " | ".join(m.value for m in at.markdown)  # the escaping itself, before _texts reads it back
    assert r"Q2 \*beats\* \[x\]\(y\)" in raw and "(https://e.test/a%281%29)" in raw


def test_c7_research_unavailable_result_reason_is_shown(monkeypatch, tmp_path):
    from nse_backtest.desk import loaders, research
    monkeypatch.setattr(loaders, "corporate_actions", lambda p, s: pd.DataFrame())
    monkeypatch.setattr(research, "nse_research", lambda s, p, **k: research.Unavailable("invalid NSE symbol"))
    at, _ = _choose_page(monkeypatch, tmp_path)
    _load(at)
    next(b for b in at.button if b.label == "Load news & earnings").click().run()
    assert "invalid NSE symbol" in _texts(at.warning)


def test_c7_skipped_symbol_check_it_runs_check_and_renders_card(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    at, _ = _choose_page(monkeypatch, tmp_path)
    take = _cands()[0]
    from dataclasses import replace
    seen = []
    monkeypatch.setattr(session, "run_check", lambda p, sym, mode, limits, **k: seen.append((sym, mode.value)) or
                        replace(take, plan=replace(take.plan, symbol=sym), input_hash="z" * 64))
    _pick(_load(at), "SKIP ZZZ")
    assert "no setup today" in _texts(at.error) + _texts(at.info)
    assert "Max loss" not in [m.label for m in at.metric]
    at.button(key="desk_choose_checkit").click().run()
    assert not at.exception and seen == [("ZZZ", "swing")]
    assert "Max loss" in [m.label for m in at.metric] and "ZZZ" in _codes(at)
    at.run()
    assert seen == [("ZZZ", "swing")]


def test_c7_intraday_row_band_uses_current_session_only(monkeypatch, tmp_path):
    from nse_backtest.desk import loaders
    import json
    today = _market_today()
    days = pd.bdate_range(end=pd.Timestamp(today) - pd.Timedelta(days=1), periods=3)
    idx = pd.DatetimeIndex([t for d in days for t in pd.date_range(f"{d.date()} 09:15", periods=25, freq="15min")]
                           + list(pd.date_range(f"{today} 09:15", periods=10, freq="15min")))
    bars = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": np.linspace(100, 101, len(idx)),
                         "Volume": 1e5}, index=idx)
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: bars)
    at, _ = _choose_page(monkeypatch, tmp_path)
    _pick(_load(at), "SKIP INT")
    assert "range band unavailable" not in _texts(at.caption)
    price = next(json.loads(p.proto.spec) for p in at.get("plotly_chart")
                 if json.loads(p.proto.spec)["layout"].get("title", {}).get("text", "").startswith("Price"))
    assert len(price["data"][0]["x"]) == 10  # candles: the current session only
    assert any(t.get("name") == "p5-p95" for t in price["data"])  # the band, from session returns


def test_c7_intraday_session_over(monkeypatch, tmp_path):
    from nse_backtest.desk import loaders
    today = _market_today()
    full = pd.DatetimeIndex(list(pd.date_range(f"{today} 09:15", periods=25, freq="15min")))
    bars = pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1e5}, index=full)
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: bars)
    at, _ = _choose_page(monkeypatch, tmp_path)
    _pick(_load(at), "SKIP INT")
    assert "range band unavailable: session over" in _texts(at.caption)


def test_c7_intraday_empty_frame_is_no_bars(monkeypatch, tmp_path):
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: pd.DataFrame(
        columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([])))
    at, _ = _choose_page(monkeypatch, tmp_path)
    _pick(_load(at), "SKIP INT")
    assert "Price chart unavailable: no bars" in _texts(at.error)
    assert "IndexError" not in _texts(at.error)


def test_c7_check_a_trade_renders_the_same_card(monkeypatch, tmp_path):
    _patch_ok(monkeypatch, tmp_path)
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    labels = [m.label for m in at.metric]
    assert "Max loss" in labels and "Expected value" in labels
    assert any(n.label == "Quantity" for n in at.number_input)


def test_c7_zero_sized_card_shows_reason_and_per_share(monkeypatch, tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import session
    from nse_backtest.desk.models import Sizing
    _patch_ok(monkeypatch, tmp_path)
    take = _cands()[0]
    from nse_backtest.desk import charts, projections
    zero = replace(take, sizing=Sizing(0, "risk", 0.0, 0.0, 0.0, False, "stop too wide"))
    monkeypatch.setattr(session, "run_check", lambda *a, **k: zero)
    real_oc, real_ap = charts.outcome_chart, projections.account_projection
    armed = {"on": True}

    def oc(*a, **k):
        if armed["on"]:
            raise AssertionError("outcome_chart called for a zero-quantity card")
        return real_oc(*a, **k)

    def ap(*a, **k):
        if armed["on"]:
            raise AssertionError("account_projection called for a zero-quantity card")
        return real_ap(*a, **k)
    monkeypatch.setattr(charts, "outcome_chart", oc)
    monkeypatch.setattr(projections, "account_projection", ap)
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    qty = next(n for n in at.number_input if n.label == "Quantity")
    assert qty.value is None
    text = _texts(at.info) + _texts(at.caption) + _texts(at.markdown)
    assert "The desk sized 0 shares (stop too wide)" in text and "Per share" in text
    assert "zero-quantity" not in _texts(at.error)
    armed["on"] = False
    qty.set_value(3).run()
    assert "Max loss" in [m.label for m in at.metric]


def test_c7_zero_sized_choose_row_shows_dash_for_money(monkeypatch, tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import session
    from nse_backtest.desk.daily import DailyPlan
    from nse_backtest.desk.models import Sizing
    _patch_ok(monkeypatch, tmp_path)
    take = _cands()[0]
    zero = replace(take, sizing=Sizing(0, "risk", 0.0, 0.0, 0.0, False, "stop too wide"))
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: DailyPlan("NSE", None, "", (), (zero,), (), (), ()))
    monkeypatch.setattr(session, "run_intraday", lambda *a, **k: ([], []))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    _load(at)
    df = next(d.value for d in at.dataframe if "Desk says" in d.value.columns)
    row = df.iloc[0]
    assert row["Qty"] == "0" and row["Max loss"] == "—" and row["Profit T1"] == "—"


# ---------------------------------------------------------------- H2: every verdict keeps its numbers

QUICK_MSG = "Quick check — run a full check before choosing"


def _others():
    """Two first-pass verdicts the plan kept in `others`: a 4th TAKE (OTK) and a regime-blocked breakout (OSK)."""
    from dataclasses import replace
    from nse_backtest.desk.models import Action, Check, Status
    take = _cands()[0]
    otk = replace(take, plan=replace(take.plan, symbol="OTK"), input_hash="o" * 64, headline="TAKE OTK",
                  first_pass=True, quote_as_of=None, quote_live=False)
    p = take.plan
    osk = replace(take, action=Action.SKIP, input_hash="q" * 64, headline="SKIP OSK — blocked by: regime", ticket=(),
                  plan=replace(p, symbol="OSK", entry=p.entry * 1.05, stop=p.stop * 1.05,
                               target_1=p.target_1 * 1.05, target_2=p.target_2 * 1.05),
                  checks=(Check("regime", Status.BLOCK, "HOSTILE"),), first_pass=True, quote_as_of=None)
    return otk, osk


def _others_page(monkeypatch, tmp_path):
    from nse_backtest.desk import audit, session
    from nse_backtest.desk.daily import DailyPlan
    _patch_ok(monkeypatch, tmp_path)
    take, wait, skip, intra = _cands()
    otk, osk = _others()
    plan = DailyPlan("NSE", "HOSTILE", "", (), (take,), (wait,), (("ZZZ", "RuntimeError: odd bars"),), (),
                     (otk, osk, skip))
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: plan)
    monkeypatch.setattr(session, "run_intraday", lambda *a, **k: ([intra], []))
    logged = []
    monkeypatch.setattr(audit, "append_override", lambda v, path=None: logged.append(v))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    assert not at.exception
    return _load(at), logged


def test_h2_choose_table_has_numbers_for_others_and_the_new_columns(monkeypatch, tmp_path):
    at, _ = _others_page(monkeypatch, tmp_path)
    df = next(d.value for d in at.dataframe if "Desk says" in d.value.columns)
    assert all(c in df.columns for c in ("Exp R", "Expected value", "To entry %"))
    assert list(df["Symbol"]) == ["ABC", "OTK", "WTT", "OSK", "INT", "SKP", "ZZZ"]  # your-call ranking (ties: plan lists, scan, others)
    for sym in ("OTK", "OSK"):
        row = df[df["Symbol"] == sym].iloc[0]
        assert row["Entry"].startswith("₹") and row["Max loss"].startswith("₹")
        assert row["Expected value"].startswith("₹") and row["Exp R"].endswith("R")
        assert row["To entry %"] == row["To entry %"]  # a number, not NaN
    osk = df[df["Symbol"] == "OSK"].iloc[0]
    assert osk["To entry %"] == pytest.approx(5.0, abs=0.01)
    assert df[df["Symbol"] == "OTK"].iloc[0]["Desk says"] == "TAKE"
    assert "quick" in df[df["Symbol"] == "OTK"].iloc[0]["Checked"]
    assert "is not replaced by the next quick one" in _texts(at.caption)


def test_h2_quick_card_has_no_ticket_and_no_override_until_full_check(monkeypatch, tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import session
    at, logged = _others_page(monkeypatch, tmp_path)
    otk = _others()[0]
    seen = []

    def run_check(p, sym, mode, limits, **k):
        seen.append((sym, mode.value, k))
        return replace(otk, input_hash="f" * 64, first_pass=False)
    monkeypatch.setattr(session, "run_check", run_check)
    _pick(at, "TAKE OTK")
    assert QUICK_MSG in _texts(at.warning)
    assert "OTK" not in _codes(at)
    assert not any(c.label.startswith("I choose this anyway") for c in at.checkbox)
    assert "Max loss" in [m.label for m in at.metric]  # levels and money still shown
    next(b for b in at.button if b.label == "Full check").click().run()
    assert not at.exception
    assert len(seen) == 1 and seen[0][:2] == ("OTK", "swing") and seen[0][2]["entry"] == otk.plan.entry
    assert QUICK_MSG not in _texts(at.warning)
    assert "OTK" in _codes(at)  # the full check's TAKE ticket replaces the quick card
    assert logged == []


def test_h2_quick_skip_card_offers_no_override_checkbox(monkeypatch, tmp_path):
    at, _ = _others_page(monkeypatch, tmp_path)
    _pick(at, "SKIP OSK")
    assert QUICK_MSG in _texts(at.warning)
    assert not any(c.label.startswith("I choose this anyway") for c in at.checkbox)
    assert any(b.label == "Full check" for b in at.button)


def test_h2_check_it_for_a_skipped_plan_symbol_uses_the_desks_entry(monkeypatch, tmp_path):
    from dataclasses import replace
    from nse_backtest.desk import session
    at, _ = _others_page(monkeypatch, tmp_path)
    take = _cands()[0]
    seen = []
    monkeypatch.setattr(session, "run_check", lambda p, sym, mode, limits, **k: seen.append(k) or
                        replace(take, plan=replace(take.plan, symbol=sym), input_hash="z" * 64))
    _pick(at, "SKIP ZZZ")
    at.button(key="desk_choose_checkit").click().run()
    assert not at.exception and len(seen) == 1 and seen[0].get("desk_entry") is True


def test_h2_check_a_trade_entry_zero_is_the_desks_own_entry(monkeypatch, tmp_path):
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    take = _cands()[0]
    seen = []
    monkeypatch.setattr(session, "run_check", lambda p, sym, mode, limits, **k: seen.append(k) or take)
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    assert "0 = the desk's own entry (breakout above the 20-day high for swing)" in at.number_input(key="desk_entry").help
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception and seen and seen[0]["entry"] is None and seen[0]["desk_entry"] is True


# ---------------------------------------------------------------- H3: advice looks like advice

H3_FLIP = "Nifty closes above its 50-day average"
H3_HOSTILE = "Market regime HOSTILE: swing longs historically lose here (held-out 2026: −1.61%/trade)"


def _h3_card(monkeypatch, tmp_path, checks, *, reruns=None, **changes):
    from dataclasses import replace
    from nse_backtest.desk import daily
    from nse_backtest.desk.models import Action
    from tests.test_desk_card_f1 import _base, _card_page
    monkeypatch.setattr(daily, "flip_conditions", lambda df, profile: (H3_FLIP,))
    base = _base(monkeypatch, tmp_path)
    v = replace(base, action=Action.SKIP, headline="SKIP ABC — blocked", ticket=(), checks=tuple(checks),
                input_hash=("h3" + "".join(c.name[:2] for c in checks)).ljust(64, "0"), **changes)
    at, calls = _card_page(monkeypatch, tmp_path, v, reruns=reruns)
    return at, calls, v


def test_h3_card_market_and_odds_are_amber_data_and_limits_red(monkeypatch, tmp_path):
    from nse_backtest.desk.markets import NSE
    from nse_backtest.desk.models import Check, Status
    checks = (Check("regime", Status.BLOCK, H3_HOSTILE),
              Check("monte_carlo", Status.BLOCK, "Simulated expectancy -0.20R is negative"),
              Check("liquidity", Status.BLOCK, "turnover too low"),
              Check("live_price", Status.WARN, "Price is the last close"))
    at, calls, v = _h3_card(monkeypatch, tmp_path, checks, regime="HOSTILE")
    warn, err = _texts(at.warning), _errors(at)
    assert "Market says sit out" in warn and H3_HOSTILE in warn and H3_FLIP in warn
    assert "Simulated expectancy -0.20R is negative" in warn
    assert "turnover too low" in err
    for advice in (H3_HOSTILE, "Simulated expectancy", "Market says sit out"):
        assert advice not in err, advice
    box = next(c for c in at.checkbox if c.label == "I choose this anyway")
    box.check().run()
    assert not at.exception
    qty = int(next(n for n in at.number_input if n.label == "Quantity").value)
    x = NSE.money(0.0161 * qty * v.plan.entry)
    assert ("YOUR OVERRIDE — against the market regime (HOSTILE): the desk's held-out longs here averaged −1.61% "
            f"per trade ≈ a loss of {x} on this position") in _texts(at.warning)
    assert len(calls["override"]) == 1


def test_h3_card_broken_data_has_no_override_box_only_a_recheck(monkeypatch, tmp_path):
    from dataclasses import replace
    from nse_backtest.desk.models import Action, Check, Status
    from tests.test_desk_card_f1 import _base
    fresh = replace(_base(monkeypatch, tmp_path), action=Action.TAKE, input_hash="f" * 64)
    checks = (Check("freshness", Status.BLOCK, "Data is stale: misses 2 sessions"),
              Check("regime", Status.BLOCK, H3_HOSTILE))
    at, calls, _ = _h3_card(monkeypatch, tmp_path, checks, reruns=[fresh])
    assert not any(c.label.startswith("I choose this anyway") for c in at.checkbox)
    assert "Data is broken (Data is stale: misses 2 sessions) — re-check before choosing" in _errors(at)
    at.button(key=next(b.key for b in at.button if b.label == "Re-check")).click().run()
    assert not at.exception
    assert len(calls["check"]) == 2 and calls["override"] == []
    assert "Data is broken" not in _errors(at)


def test_h3_quick_verdict_gate_comes_before_the_broken_data_gate(monkeypatch, tmp_path):
    from nse_backtest.desk.models import Check, Status
    checks = (Check("freshness", Status.BLOCK, "Data is stale: misses 2 sessions"),)
    at, _, _ = _h3_card(monkeypatch, tmp_path, checks, first_pass=True, quote_as_of=None)
    labels = [b.label for b in at.button]
    assert "Full check" in labels and "Re-check" not in labels
    assert not any(c.label.startswith("I choose this anyway") for c in at.checkbox)


# ---------------------------------------------------------------- H6: holding up

HOLDING_UP_TITLE = "Holding up — ready for when the regime turns (not a trade)"


def _holding_up_page(monkeypatch, tmp_path, **plan_kw):
    from nse_backtest.desk import session
    from nse_backtest.desk.daily import DailyPlan
    _patch_ok(monkeypatch, tmp_path)
    take, wait, skip, _ = _cands()
    monkeypatch.setattr(session, "run_daily_plan",
                        lambda *a, **k: DailyPlan("NSE", "HOSTILE", "", (), (take,), (wait, skip), (), (), **plan_kw))
    monkeypatch.setattr(session, "run_intraday", lambda *a, **k: ([], []))
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    assert not at.exception
    return _load(at)


def test_h6_choose_tab_shows_the_holding_up_list_as_not_a_trade(monkeypatch, tmp_path):
    at = _holding_up_page(monkeypatch, tmp_path, holding_up=(("HUP", 4.25), ("HLD", -0.5)))
    assert any(e.value == HOLDING_UP_TITLE for e in at.tabs[0].subheader)
    df = next(d.value for d in at.tabs[0].dataframe if "vs index (60d)" in d.value.columns)
    assert list(df["Symbol"]) == ["HUP", "HLD"] and list(df["vs index (60d)"]) == ["+4.2 pts", "-0.5 pts"]
    assert "Desk says" not in df.columns  # no action, no money: it is not a trade
    assert not any(o.startswith(("TAKE HUP", "WAIT HUP", "SKIP HUP")) for o in at.selectbox(key="desk_choose_pick").options)
    cap = _texts(at.tabs[0].caption)
    assert "Above their own 50-EMA" in cap and "not a trade" in cap


def test_h6_choose_tab_holding_up_shows_why_it_is_empty(monkeypatch, tmp_path):
    at = _holding_up_page(monkeypatch, tmp_path, holding_up_note="Regime index data unavailable")
    assert any(e.value == HOLDING_UP_TITLE for e in at.tabs[0].subheader)
    assert "Regime index data unavailable" in _texts(at.tabs[0].warning)


def test_h6_choose_tab_holding_up_empty_without_a_reason(monkeypatch, tmp_path):
    at = _holding_up_page(monkeypatch, tmp_path)
    assert any(e.value == HOLDING_UP_TITLE for e in at.tabs[0].subheader)
    assert "No stock is holding up" in _texts(at.tabs[0].caption)
