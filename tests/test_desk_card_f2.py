"""F2 final-review fixes on the Desk card and page (components/desk_card.py, pages/0_Desk.py). No network."""
from contextlib import contextmanager
from dataclasses import replace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from components import desk_card
from nse_backtest.desk import session
from nse_backtest.desk.checks import CheckContext
from nse_backtest.desk.daily import plan_entry
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Limits, Mode, Side
from nse_backtest.desk.nse_lists import BanList
from nse_backtest.desk.service import check_trade
from nse_backtest.desk.verdict import data_broken


class _St:
    """The smallest Streamlit stand-in the Re-check / Full check prompts need: every button is pressed."""

    def __init__(self):
        self.session_state, self.shown = {}, []

    def button(self, *a, **k):
        return True

    def warning(self, *a, **k):
        self.shown.append(a[0])

    error = warning

    def rerun(self):
        pass

    @contextmanager
    def spinner(self, *a, **k):
        yield


def _swing_verdict(days_after=14):
    """A swing verdict at the daily plan's own breakout entry (not the last close), then left to go stale."""
    rng = np.random.default_rng(3)
    n = 400
    idx = pd.bdate_range("2024-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0, 0.015, n)))
    df = pd.DataFrame({"Open": c * 0.998, "High": c * 1.01, "Low": c * 0.99, "Close": c, "Volume": 1e6}, index=idx)
    now = (idx[-1] + pd.Timedelta(days=days_after, hours=10, minutes=30)).to_pydatetime().replace(
        tzinfo=ZoneInfo(NSE.tz))
    lim = Limits(capital=100_000)
    ctx = CheckContext(NSE, lim, now, regime="TRENDING", ban=BanList(frozenset(), "ok", None, ""),
                       turnover_20d=1e9, earnings_checked=True)
    e = plan_entry(df)
    v = check_trade(df, "X", NSE, Mode.SWING, lim, ctx, entry=e, replays=200, robustness=False, audit=False)
    return v, lim, now, df


def _capture(monkeypatch):
    calls = []

    def fake_run_check(profile, sym, mode, limits, **kw):
        calls.append(kw)
        raise ValueError("stub")

    monkeypatch.setattr(desk_card, "st", _St())
    monkeypatch.setattr(session, "run_check", fake_run_check)
    return calls


# ---------------------------------------------------------------- I1: Re-check keeps a swing entry

def test_i1_check_args_swing_keeps_the_plans_entry():
    v, *_ = _swing_verdict()
    assert v.plan.stop_basis != "user"
    assert desk_card._check_args(v) == {"entry": v.plan.entry}


def test_i1_check_args_swing_user_levels_kept():
    v, *_ = _swing_verdict()
    p = replace(v.plan, stop_basis="user")
    assert desk_card._check_args(replace(v, plan=p)) == {"entry": p.entry, "stop": p.stop, "target": p.target_1}


def test_i1_check_args_intraday_rebuilds_from_todays_opening_range():
    v, *_ = _swing_verdict()
    long_p = replace(v.plan, mode=Mode.INTRADAY)
    assert desk_card._check_args(replace(v, plan=long_p)) == {}  # no entry: today's OR high is the trigger
    user_p = replace(long_p, stop_basis="user")
    assert desk_card._check_args(replace(v, plan=user_p)) == {}  # yesterday's levels are not today's plan
    short_p = replace(long_p, side=Side.SHORT)
    assert desk_card._check_args(replace(v, plan=short_p)) == {"side": Side.SHORT}


def test_i1_recheck_of_broken_swing_passes_the_plans_entry_not_the_last_close(monkeypatch):
    v, lim, now, df = _swing_verdict()
    assert data_broken(v)  # 14 days stale: the card offers only a Re-check
    assert abs(v.plan.entry - float(df.Close.iloc[-1])) > 0.01  # the breakout entry is not the last close
    calls = _capture(monkeypatch)
    card = desk_card.build_card(v, NSE, lim)
    desk_card._ticket(v, card, NSE, lim, None, "w", key="choose", now=now)
    assert calls == [{"entry": v.plan.entry}]


def test_i1_full_check_uses_the_same_args(monkeypatch):
    v, lim, now, _ = _swing_verdict(days_after=0)
    calls = _capture(monkeypatch)
    desk_card._full_check(v, NSE, lim, "choose", "w")
    assert calls == [desk_card._check_args(v)] == [{"entry": v.plan.entry}]


# ---------------------------------------------------------------- I3: plan errors are red on the page

def _patch_page(monkeypatch, tmp_path):
    from tests.test_desk_page_smoke import _patch_ok
    return _patch_ok(monkeypatch, tmp_path)


def _error_plan():
    from nse_backtest.desk.daily import DailyPlan
    bad = (("ABC", "TypeError: boom"), ("XYZ", "TypeError: boom"))
    return DailyPlan("NSE", None, "", (), (), (), bad, (), errors=bad)


def test_i3_page_plan_and_choose_tabs_show_plan_errors(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    from tests.test_desk_page_smoke import _REPO, _plain
    _patch_page(monkeypatch, tmp_path)
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: _error_plan())
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    assert not at.exception
    errs = [_plain(e.value) for e in at.error]
    want = "The plan hit errors for 2 symbol(s): ABC: TypeError: boom"
    assert sum(want in e for e in errs) == 2  # Today's Plan and Choose a Trade


# ---------------------------------------------------------------- M10: an unknown regime is a red re-check

def test_m10_card_unknown_regime_is_a_red_recheck_not_an_override(monkeypatch):
    from nse_backtest.desk.models import Action, Check, Status
    v, lim, now, _ = _swing_verdict(days_after=0)
    checks = tuple(Check("regime", Status.BLOCK, "Regime unknown — treated as HOSTILE (swing longs blocked)")
                   if c.name == "regime" else replace(c, status=Status.PASS) for c in v.checks)
    v = replace(v, action=Action.SKIP, checks=checks, regime=None)
    calls = _capture(monkeypatch)
    desk_card._ticket(v, desk_card.build_card(v, NSE, lim), NSE, lim, None, "w", key="choose", now=now)
    assert desk_card.st.shown[0] == "Market regime unknown (index data unavailable) — re-check"
    assert calls == [{"entry": v.plan.entry}]  # the Re-check ran; no override box


def test_m7_page_plan_tab_shows_the_sat_out_note(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    from nse_backtest.desk.daily import DailyPlan
    from tests.test_desk_page_smoke import _REPO, _plain
    _patch_page(monkeypatch, tmp_path)
    plan = DailyPlan("NSE", None, "", (), (), (), (), (), sat_out_note="Sat-out record not written: boom")
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: plan)
    at = AppTest.from_file(str(_REPO / "pages/0_Desk.py"), default_timeout=120).run()
    at.button(key="desk_build_plan").click().run()
    assert not at.exception
    assert any("Sat-out record not written: boom" in _plain(w.value) for w in at.warning)
