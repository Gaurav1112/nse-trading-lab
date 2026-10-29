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


def test_withheld_reason_never_shows_the_home_directory(tmp_path, monkeypatch):
    """F1 D2: the withheld TAKE's reason is scrubbed (file name only)."""
    from pathlib import Path
    monkeypatch.setattr(intraday, "check_trade", _fake_check({"AAA": Action.TAKE}, []))
    home = str(Path.home())

    def append(v, path):
        raise PermissionError(13, "Permission denied", f"{home}/.nse-trading-lab/desk_audit.jsonl")
    monkeypatch.setattr(intraday, "append_verdict", append)
    _, failures = intraday.orb_scan(NSE, {"AAA": pd.DataFrame()}, Limits(capital=100_000), ctx(),
                                    audit_path=tmp_path / "a.jsonl")
    (sym, why), = failures
    assert sym == "AAA" and home not in why and "desk_audit.jsonl" in why and "withheld" in why


# ---- H5a: the scan adds ORB shorts on HOSTILE NSE days ----
from nse_backtest.desk.models import Side  # noqa: E402


def _side_check(seen: list, short_action=Action.WAIT):
    def check(df, sym, profile, mode, limits, c, **kw):
        side = kw.get("side", Side.LONG)
        seen.append((sym, side, c.fno))
        p = replace(PI, symbol=sym, side=side)
        v = decide(p, S, None, OK, NSE, 100.0, VNOW)
        return replace(v, action=short_action if side is Side.SHORT else Action.WAIT)
    return check


def test_orb_scan_checks_each_requested_side(monkeypatch):
    seen: list = []
    monkeypatch.setattr(intraday, "check_trade", _side_check(seen))
    verdicts, failures = intraday.orb_scan(NSE, {"AAA": pd.DataFrame(), "BBB": pd.DataFrame()},
                                           Limits(capital=100_000), ctx(), sides=(Side.LONG, Side.SHORT))
    assert [(s, d) for s, d, _ in seen] == [("AAA", Side.LONG), ("AAA", Side.SHORT), ("BBB", Side.LONG),
                                            ("BBB", Side.SHORT)]
    assert sorted((v.plan.symbol, v.plan.side) for v in verdicts) == [
        ("AAA", Side.LONG), ("AAA", Side.SHORT), ("BBB", Side.LONG), ("BBB", Side.SHORT)]
    assert failures == []


def test_orb_scan_is_long_only_by_default(monkeypatch):
    seen: list = []
    monkeypatch.setattr(intraday, "check_trade", _side_check(seen))
    intraday.orb_scan(NSE, {"AAA": pd.DataFrame()}, Limits(capital=100_000), ctx())
    assert [d for _, d, _ in seen] == [Side.LONG]


def test_orb_scan_short_failure_is_labelled_and_the_long_survives(monkeypatch):
    ok = _side_check([])

    def check(df, sym, *a, **kw):
        if kw.get("side") is Side.SHORT:
            raise ValueError("stop must be above entry")
        return ok(df, sym, *a, **kw)
    monkeypatch.setattr(intraday, "check_trade", check)
    verdicts, failures = intraday.orb_scan(NSE, {"AAA": pd.DataFrame()}, Limits(capital=100_000), ctx(),
                                           sides=(Side.LONG, Side.SHORT))
    assert [v.plan.side for v in verdicts] == [Side.LONG]
    assert failures == [("AAA", "short: stop must be above entry")]


def _run(monkeypatch, tmp_path, regime, profile=NSE, fno=frozenset({"AAA"})):
    from nse_backtest.desk import loaders, session
    from tests.desk.test_cli_loaders import series
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loaders, "load_intraday", lambda p, s: series())
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    asked = []
    monkeypatch.setattr(loaders, "fno_underlyings", lambda p, now=None: asked.append(p.code) or (fno, None))
    seen: list = []
    monkeypatch.setattr(intraday, "check_trade", _side_check(seen))
    warns: list = []
    session.run_intraday(profile, Limits(capital=100_000), ["AAA"], regime=regime, on_warning=warns.append)
    return seen, asked, warns


def test_run_intraday_adds_shorts_only_on_hostile_nse_days(tmp_path, monkeypatch):
    from nse_backtest.desk.markets import US
    seen, asked, _ = _run(monkeypatch, tmp_path, "HOSTILE")
    assert [(s, d, f) for s, d, f in seen] == [("AAA", Side.LONG, frozenset({"AAA"})),
                                                ("AAA", Side.SHORT, frozenset({"AAA"}))]
    assert asked == ["NSE"]
    for regime in ("TRENDING", "MIXED", None):
        seen, asked, _ = _run(monkeypatch, tmp_path, regime)
        assert [d for _, d, _ in seen] == [Side.LONG] and asked == []
    seen, asked, _ = _run(monkeypatch, tmp_path, "HOSTILE", profile=US)
    assert [d for _, d, _ in seen] == [Side.LONG] and asked == []


def test_run_intraday_says_when_the_fno_list_is_unavailable(tmp_path, monkeypatch):
    seen, _, warns = _run(monkeypatch, tmp_path, "HOSTILE", fno=None)
    assert [d for _, d, _ in seen] == [Side.LONG, Side.SHORT]  # still checked: short_eligibility BLOCKs them
    assert any("could not verify F&O eligibility" in w for w in warns)
