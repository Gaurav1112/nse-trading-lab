import numpy as np, pandas as pd
from streamlit.testing.v1 import AppTest


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
    assert any(df.value["Distance %"].astype(str).eq("n/a").all() for df in at.dataframe
               if "Distance %" in df.value.columns)


def test_check_result_not_shown_for_edited_inputs(monkeypatch, tmp_path):
    _patch_ok(monkeypatch, tmp_path)
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert any("Plan" == m.value.strip("*") for m in at.markdown)
    at.number_input(key="desk_stop").set_value(1.0).run()
    assert not any("Plan" == m.value.strip("*") for m in at.markdown)


def test_intraday_scan_cache_key_has_date(monkeypatch, tmp_path):
    from datetime import date
    from nse_backtest.desk import session
    _patch_ok(monkeypatch, tmp_path)
    monkeypatch.setattr(session, "run_intraday", lambda *a, **k: ([], []))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    at.button(key="desk_scan").click().run()
    assert str(("intraday", "NSE", date.today().isoformat())) in at.session_state.filtered_state
