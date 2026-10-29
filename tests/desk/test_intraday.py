"""orb_scan logs its TAKE verdicts to the audit log (WAIT/SKIP from the scan are not logged)."""
import json
from dataclasses import replace

import pandas as pd

from nse_backtest.desk import intraday
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Action, Limits, Mode
from nse_backtest.desk.verdict import decide
from tests.desk.test_daily_positions_intraday import ctx
from tests.desk.test_scoreboard import OK, P, S, VNOW

PI = replace(P, mode=Mode.INTRADAY, entry_limit=100.5)


def _fake_check(actions: dict[str, Action | None], seen: list):
    def check(df, sym, profile, mode, limits, c, **kw):
        seen.append(kw.get("audit"))
        if actions[sym] is None:
            raise ValueError(f"{sym}: not enough bars")
        return replace(decide(replace(PI, symbol=sym), S, None, OK, NSE, 100.0, VNOW), action=actions[sym])
    return check


def _rows(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_orb_scan_logs_exactly_the_take_verdicts(tmp_path, monkeypatch):
    seen: list = []
    acts = {"AAA": Action.TAKE, "BBB": Action.WAIT, "CCC": Action.TAKE}
    monkeypatch.setattr(intraday, "check_trade", _fake_check(acts, seen))
    path = tmp_path / "audit.jsonl"
    frames = {s: pd.DataFrame() for s in acts}
    verdicts, failures = intraday.orb_scan(NSE, frames, Limits(capital=100_000), ctx(), audit_path=path)
    assert len(verdicts) == 3 and failures == []
    assert seen == [False, False, False]  # check_trade never logs: orb_scan logs TAKEs only
    rows = _rows(path)
    assert [(r["symbol"], r["action"], r["mode"]) for r in rows] == [("AAA", "TAKE", "intraday"),
                                                                     ("CCC", "TAKE", "intraday")]


def test_orb_scan_failure_for_one_symbol_still_logs_the_others(tmp_path, monkeypatch):
    acts = {"AAA": Action.TAKE, "BAD": None, "CCC": Action.TAKE}
    monkeypatch.setattr(intraday, "check_trade", _fake_check(acts, []))
    path = tmp_path / "audit.jsonl"
    verdicts, failures = intraday.orb_scan(NSE, {s: pd.DataFrame() for s in acts}, Limits(capital=100_000),
                                           ctx(), audit_path=path)
    assert [f[0] for f in failures] == ["BAD"] and len(verdicts) == 2
    assert [r["symbol"] for r in _rows(path)] == ["AAA", "CCC"]


def test_orb_scan_without_audit_path_logs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(intraday, "check_trade", _fake_check({"AAA": Action.TAKE}, []))
    intraday.orb_scan(NSE, {"AAA": pd.DataFrame()}, Limits(capital=100_000), ctx())
    assert list(tmp_path.iterdir()) == []


def test_run_intraday_logs_scan_takes_to_the_data_dir_audit(tmp_path, monkeypatch):
    from nse_backtest.desk import loaders, session
    from tests.desk.test_cli_loaders import series
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: series())
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(intraday, "check_trade", _fake_check({"AAA": Action.TAKE, "BBB": Action.SKIP}, []))
    session.run_intraday(NSE, Limits(capital=100_000), ["AAA", "BBB"], regime=None)
    assert [r["symbol"] for r in _rows(tmp_path / "desk_audit.jsonl")] == ["AAA"]


def test_orb_scan_oserror_for_one_symbol_is_a_failure_others_still_return(tmp_path, monkeypatch):
    ok = _fake_check({"AAA": Action.TAKE, "CCC": Action.TAKE}, [])

    def check(df, sym, *a, **k):
        if sym == "BAD":
            raise OSError("audit disk full")
        return ok(df, sym, *a, **k)
    monkeypatch.setattr(intraday, "check_trade", check)
    path = tmp_path / "audit.jsonl"
    verdicts, failures = intraday.orb_scan(NSE, {s: pd.DataFrame() for s in ("AAA", "BAD", "CCC")},
                                           Limits(capital=100_000), ctx(), audit_path=path)
    assert [v.plan.symbol for v in verdicts] == ["AAA", "CCC"]
    assert failures == [("BAD", "OSError: audit disk full")]  # not a TAKE write: text unchanged


def test_orb_scan_oserror_while_logging_a_take_is_that_symbols_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(intraday, "check_trade", _fake_check({"AAA": Action.TAKE, "CCC": Action.WAIT}, []))
    calls = []

    def append(v, path):
        calls.append(v.plan.symbol)
        raise OSError("read-only filesystem")
    monkeypatch.setattr(intraday, "append_verdict", append)
    verdicts, failures = intraday.orb_scan(NSE, {"AAA": pd.DataFrame(), "CCC": pd.DataFrame()},
                                           Limits(capital=100_000), ctx(), audit_path=tmp_path / "a.jsonl")
    assert calls == ["AAA"] and [v.plan.symbol for v in verdicts] == ["CCC"]
    assert failures == [("AAA", "desk said TAKE but it could not be recorded (OSError: read-only filesystem), "
                                "so it is withheld — fix the audit log and rescan")]
