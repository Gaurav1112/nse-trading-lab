"""C7: candidate gathering, corporate actions loader, CLI Money block / --qty / --choose. No network."""
import json
from dataclasses import replace

import pandas as pd
import pytest

from nse_backtest.desk import cli, loaders, session
from nse_backtest.desk.daily import DailyPlan
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action, Check, DeskVerdict, Limits, Mode, Odds, Sizing, Status, TradePlan
from nse_backtest.desk.verdict import ticket_lines

PLAN = TradePlan("ABC", "NSE", Mode.SWING, 100, 95, 110, 115, 2, 15, 100, "2026-10-28T00:00:00", "user")
SIZING = Sizing(10, "risk", 50.0, 1_000.0, 5.0, False, "10 shares")


def odds(e):
    return Odds(500, 19, 0.4, 0.4, 0.2, e, 0, None, None, (), None, None, None, None, None, None, None,
                mc_rs=(1.0, -1.0, 0.5))


def v(sym, action=Action.TAKE, mode=Mode.SWING, e=0.3, checks=(Check("regime", Status.PASS, ""),), h=None):
    p = replace(PLAN, symbol=sym, mode=mode)
    t = ticket_lines(p, SIZING, NSE) if action is Action.TAKE else ()
    return DeskVerdict(action, f"{action.value} {sym}", p, SIZING, None if e is None else odds(e), checks, t,
                       None, 100.0, h or (sym + action.value + mode.value).ljust(64, "x"), "2026-10-29T10:00:00")


# ---------------------------------------------------------------- session.merge / choose_candidates

def test_merge_dedupes_by_symbol_and_mode_and_sorts_take_first_then_expectancy():
    plan = DailyPlan("NSE", None, "", (), (v("T1", e=0.1), v("T2", e=0.5)),
                     (v("W1", Action.WAIT, e=0.9), v("S1", Action.SKIP, e=None)), (("X", "no setup"),), ())
    scan = [v("T1", Action.SKIP, Mode.INTRADAY, e=0.2), v("T2", e=0.6), v("I1", Action.TAKE, Mode.INTRADAY, e=0.4)]
    out = session.merge_candidates(plan, scan)
    keys = [(c.plan.symbol, c.plan.mode.value, c.action.value) for c in out]
    assert keys == [("T2", "swing", "TAKE"), ("I1", "intraday", "TAKE"), ("T1", "swing", "TAKE"),
                    ("W1", "swing", "WAIT"), ("T1", "intraday", "SKIP"), ("S1", "swing", "SKIP")]
    assert sum(1 for k in keys if k[:2] == ("T2", "swing")) == 1  # the scan's duplicate dropped
    assert session.merge_candidates(None, ()) == []


def test_choose_candidates_runs_plan_and_scan_with_one_regime(monkeypatch):
    seen = {}
    plan = DailyPlan("NSE", None, "", (), (v("T1"),), (), (), ())
    monkeypatch.setattr(session, "resolve_tape", lambda p, w=None: (None, None))

    def fake_plan(p, limits, syms, on_warning=None, **k):
        seen["plan"] = (list(syms), k)
        return plan

    def fake_scan(p, limits, syms, regime=None, on_warning=None):
        seen["scan"] = (list(syms), regime)
        return [v("I1", mode=Mode.INTRADAY)], [("BAD", "no bars")]
    monkeypatch.setattr(session, "run_daily_plan", fake_plan)
    monkeypatch.setattr(session, "run_intraday", fake_scan)
    out = session.choose_candidates(NSE, Limits(capital=100_000), symbols=["T1"], intraday_symbols=["I1"])
    assert [c.plan.symbol for c in out] == ["T1", "I1"]
    assert seen["plan"] == (["T1"], {"regime": None, "tape": None}) and seen["scan"] == (["I1"], None)


def test_choose_candidates_sends_every_scan_failure_to_on_warning(monkeypatch):
    monkeypatch.setattr(session, "resolve_tape", lambda p, w=None: (None, None))
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: DailyPlan("NSE", None, "", (), (), (), (), ()))
    reason = "desk said TAKE but it could not be recorded (OSError: disk full), so it is withheld"
    monkeypatch.setattr(session, "run_intraday", lambda *a, **k: ([], [("AAA", reason), ("BBB", "no bars")]))
    warnings = []
    session.choose_candidates(NSE, Limits(capital=100_000), warnings.append, symbols=[], intraday_symbols=["AAA"])
    assert warnings == [f"intraday AAA: {reason}", "intraday BBB: no bars"]


def test_choose_tab_lists_scan_failures_under_the_table(monkeypatch, tmp_path):
    from tests.desk.test_scoreboard_io import _page
    reason = "desk said TAKE but it could not be recorded (OSError: disk full), so it is withheld"
    plan = DailyPlan("NSE", None, "", (), (v("T1"),), (), (), ())
    monkeypatch.setattr(session, "run_daily_plan", lambda *a, **k: plan)
    monkeypatch.setattr(session, "run_intraday", lambda *a, **k: ([], [("AAA", reason)]))
    at = _page(monkeypatch, tmp_path).run()
    at.button(key="desk_choose_load").click().run()
    assert not at.exception
    assert any("AAA" in w.value and reason in w.value for w in at.warning)


# ---------------------------------------------------------------- loaders.corporate_actions

class _T:
    def __init__(self, actions):
        self._a = actions

    @property
    def actions(self):
        if isinstance(self._a, Exception):
            raise self._a
        return self._a


def test_corporate_actions_last_two_years(monkeypatch):
    import yfinance
    now = pd.Timestamp.now(tz="Asia/Kolkata").normalize()
    df = pd.DataFrame({"Dividends": [1.0, 2.0], "Stock Splits": [0.0, 0.0]},
                      index=pd.DatetimeIndex([now - pd.Timedelta(days=1200), now - pd.Timedelta(days=30)]))
    monkeypatch.setattr(yfinance, "Ticker", lambda s: _T(df))
    out = loaders.corporate_actions(NSE, "ABC")
    assert list(out["Dividends"]) == [2.0]


@pytest.mark.parametrize("bad", [RuntimeError("yahoo down"), None])
def test_corporate_actions_errors_raise_value_error(monkeypatch, bad):
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", lambda s: _T(bad))
    with pytest.raises(ValueError):
        loaders.corporate_actions(NSE, "ABC")


def test_corporate_actions_validates_symbol_before_network(monkeypatch):
    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", lambda s: pytest.fail("network touched"))
    with pytest.raises(ValueError):
        loaders.corporate_actions(NSE, "../etc")


# ---------------------------------------------------------------- OutcomeMoney never serialised

def test_outcome_money_pnl_is_not_serialised():
    from nse_backtest.desk.projections import OutcomeMoney
    om = OutcomeMoney(tuple(float(i) for i in range(10_000)), -1, 0, 1, 2, 3, .4, .4, .2)
    d = cli.to_jsonable(om)
    assert "pnl" not in d and d["median"] == 1


# ---------------------------------------------------------------- CLI

@pytest.fixture
def desk(offline_desk):
    pass


def _fake(monkeypatch, verdict):
    calls = []
    monkeypatch.setattr(session, "run_check", lambda *a, **k: calls.append(a) or verdict)
    return calls


def _audit_rows(tmp_path):
    p = tmp_path / "desk_audit.jsonl"
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def test_cli_check_prints_money_block(desk, monkeypatch, capsys):
    _fake(monkeypatch, v("ABC"))
    assert cli.main(["check", "ABC"]) == 0
    out = capsys.readouterr().out
    assert "Money (10 shares):" in out
    assert "max loss ₹" in out and "profit at T1 ₹" in out and "profit at T2 ₹" in out
    assert "reward:risk 2.00" in out and "expected value ₹15.00" in out
    assert "gap through the stop" in out
    assert "Ticket (type it in yourself):" in out and "BUY 10 ABC CNC LIMIT 100.00" in out


def test_cli_check_real_path_has_money(desk, capsys):
    assert cli.main(["check", "ABC"]) == 0
    out = capsys.readouterr().out
    assert "Money (50 shares):" in out and "Money: unavailable" not in out  # the offline fixture sizes 50


def test_cli_check_qty_override_shows_breaches(desk, monkeypatch, capsys):
    _fake(monkeypatch, v("ABC"))
    loaders.save_limits(NSE, Limits(capital=10_000))
    assert cli.main(["check", "ABC", "--qty", "40"]) == 0
    out = capsys.readouterr().out
    assert "Money (40 shares, your quantity):" in out
    assert "Breaks your limits:" in out and "risk per trade 2.3% of capital (limit 1.0%)" in out  # F1 A2: incl. costs
    assert "BUY 40 ABC CNC LIMIT 100.00" in out


@pytest.mark.parametrize("bad", ["0", "-3"])
def test_cli_check_bad_qty_exits_2(desk, monkeypatch, capsys, bad):
    _fake(monkeypatch, v("ABC"))
    assert cli.main(["check", "ABC", "--qty", bad]) == 2
    assert "qty" in capsys.readouterr().err


def test_cli_wait_keeps_desk_conditional_ticket_and_choose_adds_override(desk, monkeypatch, capsys, tmp_path):
    w = replace(v("ABC", Action.WAIT), ticket=("IF TRIGGERED:",) + ticket_lines(PLAN, SIZING, NSE))
    _fake(monkeypatch, w)
    assert cli.main(["check", "ABC"]) == 0
    out = capsys.readouterr().out
    assert "Desk's conditional ticket" in out and "IF TRIGGERED:" in out
    assert "YOUR OVERRIDE" not in out and _audit_rows(tmp_path) == []
    assert cli.main(["check", "ABC", "--choose"]) == 0
    out = capsys.readouterr().out
    assert "IF TRIGGERED:" in out and "YOUR OVERRIDE — the desk said WAIT" in out
    assert out.index("IF TRIGGERED:") < out.index("YOUR OVERRIDE") and len(_audit_rows(tmp_path)) == 1
    assert cli.main(["check", "ABC", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ticket"][0] == "IF TRIGGERED:"


def test_cli_skip_without_choose_has_no_ticket(desk, monkeypatch, capsys, tmp_path):
    _fake(monkeypatch, v("ABC", Action.SKIP, checks=(Check("gap", Status.BLOCK, "too wide"),)))
    assert cli.main(["check", "ABC"]) == 0
    out = capsys.readouterr().out
    assert "BUY 10 ABC" not in out and "--choose" in out
    assert "BLOCK gap — too wide" in out
    assert _audit_rows(tmp_path) == []


def test_cli_choose_on_skip_prints_override_ticket_and_logs_once(desk, monkeypatch, capsys, tmp_path):
    _fake(monkeypatch, v("ABC", Action.SKIP, checks=(Check("gap", Status.BLOCK, "too wide"),)))
    assert cli.main(["check", "ABC", "--choose", "--qty", "12"]) == 0
    out = capsys.readouterr().out
    assert "YOUR OVERRIDE — the desk said SKIP" in out
    assert out.index("YOUR OVERRIDE") < out.index("BUY 12 ABC CNC LIMIT 100.00")
    assert "Override logged" in out
    rows = _audit_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["action"] == "OVERRIDE" and rows[0]["desk_action"] == "SKIP"
    assert rows[0]["chosen_by"] == "owner" and rows[0]["qty"] == 12  # the quantity the owner chose


def test_cli_choose_on_take_is_a_noop_note(desk, monkeypatch, capsys, tmp_path):
    _fake(monkeypatch, v("ABC"))
    assert cli.main(["check", "ABC", "--choose"]) == 0
    out = capsys.readouterr().out
    assert "--choose ignored" in out and "YOUR OVERRIDE" not in out
    assert _audit_rows(tmp_path) == []


def test_cli_choose_with_zero_sized_skip_needs_qty(desk, monkeypatch, capsys, tmp_path):
    zero = replace(v("ABC", Action.SKIP), sizing=Sizing(0, "risk", 0.0, 0.0, 0.0, False, "stop too wide"))
    _fake(monkeypatch, zero)
    assert cli.main(["check", "ABC", "--choose"]) == 0
    out = capsys.readouterr().out
    assert "Money: unavailable" in out and "Per share:" in out and "--qty" in out
    assert "YOUR OVERRIDE" not in out and _audit_rows(tmp_path) == []


def test_cli_check_json_carries_card_without_bulky_fields(desk, monkeypatch, capsys, tmp_path):
    _fake(monkeypatch, v("ABC", Action.WAIT))
    assert cli.main(["check", "ABC", "--json", "--choose"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["plan"]["symbol"] == "ABC" and d["action"] == "WAIT"
    assert d["card"]["max_loss"] > 0 and "verdict" not in d["card"]
    assert d["card"]["ticket_header"] == "YOUR OVERRIDE — the desk said WAIT"
    assert d["override_logged"] is True and len(_audit_rows(tmp_path)) == 1
    assert "mc_rs" not in d["odds"]


def test_cli_choose_always_runs_a_fresh_check_and_stamps_the_override_now(desk, monkeypatch, capsys, tmp_path):
    """F1 B1: --choose never reuses an old verdict: each call checks again, and the row is stamped when chosen."""
    from datetime import datetime, timezone
    calls = _fake(monkeypatch, v("ABC", Action.SKIP, checks=(Check("gap", Status.BLOCK, "too wide"),)))
    assert cli.main(["check", "ABC", "--choose"]) == 0
    assert cli.main(["check", "ABC", "--choose"]) == 0
    assert len(calls) == 2
    rows = _audit_rows(tmp_path)
    assert [r["verdict_created_at"] for r in rows] == ["2026-10-29T10:00:00"] * 2
    for r in rows:
        assert abs((datetime.now(timezone.utc) - datetime.fromisoformat(r["created_at"])).total_seconds()) < 60
