"""Scoreboard I/O: audit reading, swing fill session, cache, CLI and page tab. Fictional prices only; no network."""
import json
import os
import stat
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from nse_backtest.desk import scoreboard_io, session
from nse_backtest.desk.models import Mode
from nse_backtest.desk.scoreboard import SCOREBOARD_VERSION, Status

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=IST)


def row(created, symbol="ABC", market="NSE", mode="swing", entry=100.0, stop=98.0, target_1=103.0, h="h1",
        action="TAKE", **kw):
    r = {"created_at": created.isoformat(), "action": action, "symbol": symbol, "market": market, "mode": mode,
         "entry": entry, "stop": stop, "target_1": target_1, "qty": 10, "mc_expectancy_r": 0.3,
         "history_expectancy_r": 0.2, "input_hash": h, "max_hold_bars": 15, "cost_per_share": 0.0}
    r.update(kw)
    return r


def write_audit(path, rows):
    path.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in rows), encoding="utf-8")


def frame(bars: dict) -> pd.DataFrame:
    """{date-string: (open, high, low, close)} -> daily OHLC frame."""
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in bars])
    return pd.DataFrame(list(bars.values()), index=idx, columns=["Open", "High", "Low", "Close"])


WIN_BARS = {"2026-10-16": (101, 101.5, 99.5, 100.5), "2026-10-19": (100.5, 103.2, 100.1, 103)}


class Loader:
    """Fake loader: returns a frame per symbol (or raises), counting calls."""

    def __init__(self, frames=None, exc=None):
        self.frames, self.exc, self.calls = frames or {}, exc, []

    def __call__(self, profile, symbol, *a, **k):
        self.calls.append((profile.code, symbol))
        if self.exc is not None:
            raise self.exc
        return self.frames[symbol]


def boom_loader():
    return Loader(exc=AssertionError("loader must not be called"))


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "desk_audit.jsonl", tmp_path / "desk_scoreboard.json"


def run(paths, loader=None, intraday=None, **kw):
    audit, cache = paths
    kw.setdefault("now", NOW)
    return session.run_scoreboard(audit_path=audit, cache_path=cache, load_daily=loader or boom_loader(),
                                  load_intraday=intraday or boom_loader(), **kw)


# ---------------------------------------------------------------- swing + cache

def test_swing_won_then_served_from_cache(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    ld = Loader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert [o.status for o in res.outcomes] == [Status.WON]
    assert res.outcomes[0].fill_price == 100 and res.outcomes[0].exit_price == 103
    assert ld.calls == [("NSE", "ABC")]
    res2 = run(paths, Loader(exc=RuntimeError("must not load")))
    assert [o.status for o in res2.outcomes] == [Status.WON]
    assert res2.outcomes[0].realised_r == pytest.approx(1.5)
    assert res2.stats[0].n == 1


def test_fill_session_after_close_skips_holiday():
    assert scoreboard_io.fill_session_of("NSE", datetime(2026, 10, 19, 16, 0, tzinfo=IST)) == date(2026, 10, 21)
    assert scoreboard_io.fill_session_of("NSE", datetime(2026, 10, 19, 15, 29, tzinfo=IST)) == date(2026, 10, 19)
    assert scoreboard_io.fill_session_of("NSE", datetime(2026, 10, 20, 11, 0, tzinfo=IST)) == date(2026, 10, 21)
    # uncovered year: weekday rule, no crash
    assert scoreboard_io.fill_session_of("NSE", datetime(2027, 3, 5, 16, 0, tzinfo=IST)) == date(2027, 3, 8)


def test_swing_after_close_fills_on_next_session(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 19, 16, 0, tzinfo=IST))])
    bars = {"2026-10-19": (100, 100, 90, 95),  # would fill (and stop out) if the 19th were used
            "2026-10-21": (101, 101.5, 99.5, 100.5), "2026-10-22": (100.5, 103.2, 100.1, 103)}
    res = run(paths, Loader({"ABC": frame(bars)}))
    o = res.outcomes[0]
    assert o.status is Status.WON and o.fill_at.startswith("2026-10-21")


def test_friday_after_close_dedupes_with_monday_morning(paths):
    fri = datetime(2026, 10, 16, 16, 0, tzinfo=IST)
    assert scoreboard_io.fill_session_of("NSE", fri) == date(2026, 10, 19)
    write_audit(paths[0], [row(fri, h="fri"), row(datetime(2026, 10, 19, 10, 0, tzinfo=IST), h="mon")])
    bars = {"2026-10-19": (101, 101.5, 99.5, 100.5), "2026-10-21": (100.5, 103.2, 100.1, 103)}
    res = run(paths, Loader({"ABC": frame(bars)}))
    assert len(res.outcomes) == 1
    assert res.outcomes[0].candidate.key.startswith("fri|")


def test_open_not_cached_and_unavailable_not_cached(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 27, 10, 0, tzinfo=IST), symbol="OPN", h="o"),
                           row(datetime(2026, 10, 16, 10, 0, tzinfo=IST), symbol="BAD", h="b")])

    class Mixed(Loader):
        def __call__(self, profile, symbol, *a, **k):
            self.calls.append(symbol)
            if symbol == "BAD":
                raise RuntimeError("feed down")
            return frame({"2026-10-27": (101, 101.5, 99.5, 100.5), "2026-10-28": (100.5, 101, 99, 100.8)})

    res = run(paths, Mixed())
    by = {o.candidate.symbol: o for o in res.outcomes}
    assert by["OPN"].status is Status.OPEN
    assert by["BAD"].status is Status.UNAVAILABLE and by["BAD"].note == "daily bars unavailable: feed down"
    cached = json.loads(paths[1].read_text())["outcomes"] if paths[1].exists() else {}
    assert cached == {}
    ld = Mixed()
    run(paths, ld)
    assert sorted(ld.calls) == ["BAD", "OPN"]  # both recomputed


def test_intraday_older_than_60_days_unavailable_without_fetch(paths):
    write_audit(paths[0], [row(datetime(2026, 8, 20, 10, 0, tzinfo=IST), mode="intraday")])
    res = run(paths, intraday=boom_loader())
    o = res.outcomes[0]
    assert o.status is Status.UNAVAILABLE and "older than 60 days" in o.note


def test_intraday_resolves_from_fake_bars(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 27, 9, 50, tzinfo=IST), mode="intraday")])
    idx = pd.date_range("2026-10-27 09:15", "2026-10-27 15:15", freq="15min")
    bars = pd.DataFrame({"Open": 99.0, "High": 99.5, "Low": 98.5, "Close": 99.2}, index=idx)
    bars.loc[pd.Timestamp("2026-10-27 10:00"), ["Open", "High", "Low", "Close"]] = (99.5, 100.5, 99.2, 100.2)
    bars.loc[pd.Timestamp("2026-10-27 10:15"), ["Open", "High", "Low", "Close"]] = (100.2, 103.5, 100.1, 103.2)
    res = run(paths, intraday=Loader({"ABC": bars}))
    assert res.outcomes[0].status is Status.WON
    assert res.outcomes[0].candidate.mode is Mode.INTRADAY


def test_corrupt_cache_backed_up_and_rebuilt(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    paths[1].write_text("{not json", encoding="utf-8")
    warns = []
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}), on_warning=warns.append)
    backups = list(paths[1].parent.glob("desk_scoreboard.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{not json"
    assert res.cache_note and "unreadable" in res.cache_note and backups[0].name in res.cache_note
    assert warns and "unreadable" in warns[0]
    assert res.outcomes[0].status is Status.WON
    assert json.loads(paths[1].read_text())["version"] == SCOREBOARD_VERSION


@pytest.mark.parametrize("bad", ['[1, 2]', '{"version": 1, "outcomes": []}', '{"outcomes": {}}'])
def test_wrong_shape_cache_backed_up(paths, bad):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    paths[1].write_text(bad, encoding="utf-8")
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert list(paths[1].parent.glob("desk_scoreboard.json.corrupt-*"))
    assert res.cache_note and res.outcomes[0].status is Status.WON


def test_wrong_version_rebuilt(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    paths[1].write_text(json.dumps({"version": SCOREBOARD_VERSION + 1, "outcomes": {}}), encoding="utf-8")
    ld = Loader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.calls and res.cache_note and "version" in res.cache_note
    data = json.loads(paths[1].read_text())
    assert data["version"] == SCOREBOARD_VERSION and len(data["outcomes"]) == 1
    kept = list(paths[1].parent.glob("desk_scoreboard.json.other-version-*"))  # never silently destroyed
    assert len(kept) == 1 and json.loads(kept[0].read_text())["version"] == SCOREBOARD_VERSION + 1


def test_bool_version_is_not_a_match(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    data = json.loads(paths[1].read_text())
    data["version"] = True
    paths[1].write_text(json.dumps(data), encoding="utf-8")
    ld = Loader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.calls and res.cache_note


def test_changed_candidate_fields_recomputed(paths):
    created = datetime(2026, 10, 16, 10, 0, tzinfo=IST)
    write_audit(paths[0], [row(created)])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    write_audit(paths[0], [row(created, target_1=104.0)])  # same input_hash + created_at = same key
    ld = Loader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.calls == [("NSE", "ABC")]
    assert res.outcomes[0].status is Status.OPEN  # 103.2 no longer reaches the target; still holding


def test_tampered_cache_entry_dropped_and_recomputed(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    lose = {"2026-10-16": (101, 101.5, 99.5, 100.5), "2026-10-19": (100, 100.2, 97, 97.5)}
    run(paths, Loader({"ABC": frame(lose)}))
    data = json.loads(paths[1].read_text())
    (key, entry), = data["outcomes"].items()
    assert entry["outcome"]["status"] == "lost"
    entry["outcome"]["status"] = "won"
    entry["outcome"]["realised_r"] = 1.5
    paths[1].write_text(json.dumps(data), encoding="utf-8")
    ld = Loader({"ABC": frame(lose)})
    res = run(paths, ld)
    assert ld.calls and res.outcomes[0].status is Status.LOST
    assert json.loads(paths[1].read_text())["outcomes"][key]["outcome"]["status"] == "lost"


def test_single_malformed_entry_dropped_only(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST), symbol="AAA", h="a"),
                           row(datetime(2026, 10, 16, 10, 5, tzinfo=IST), symbol="BBB", h="b")])
    run(paths, Loader({"AAA": frame(WIN_BARS), "BBB": frame(WIN_BARS)}))
    data = json.loads(paths[1].read_text())
    bkey = next(k for k in data["outcomes"] if k.startswith("b|"))
    data["outcomes"][bkey] = "garbage"
    paths[1].write_text(json.dumps(data), encoding="utf-8")
    ld = Loader({"AAA": frame(WIN_BARS), "BBB": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.calls == [("NSE", "BBB")]
    assert [o.status for o in res.outcomes] == [Status.WON, Status.WON]
    assert not list(paths[1].parent.glob("desk_scoreboard.json.corrupt-*"))


def test_rejects_carry_line_numbers(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST)), "garbage{", "",
                           row(datetime(2026, 10, 16, 11, 0, tzinfo=IST), symbol="XYZ", entry=None), "[1, 2]",
                           row(datetime(2026, 10, 16, 11, 0, tzinfo=IST), action="SKIP", h="s")])
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert [n for n, _ in res.rejects] == [2, 4, 5]
    assert "unreadable audit line" in res.rejects[0][1] and "entry" in res.rejects[1][1]
    assert [o.status for o in res.outcomes] == [Status.WON]


def test_missing_audit_is_empty(paths):
    res = run(paths)
    assert res.outcomes == [] and res.stats == [] and res.rejects == [] and res.cache_note is None


def test_cache_file_mode_and_atomic_write(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert stat.S_IMODE(os.stat(paths[1]).st_mode) == 0o600
    assert sorted(p.name for p in paths[1].parent.iterdir()) == ["desk_audit.jsonl", "desk_scoreboard.json"]
    before = os.stat(paths[1]).st_mtime_ns
    os.utime(paths[1], ns=(before - 10**9, before - 10**9))
    run(paths, Loader(exc=RuntimeError("cached")))
    assert os.stat(paths[1]).st_mtime_ns == before - 10**9  # nothing changed: not rewritten


def test_market_filter_and_stats_never_combine(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST), h="n"),
                           row(datetime(2026, 10, 16, 10, 0, tzinfo=ET), market="US", symbol="XYZ", h="u")])
    frames = {"ABC": frame(WIN_BARS), "XYZ": frame(WIN_BARS)}
    res = run(paths, Loader(frames), market="US")
    assert [o.candidate.market for o in res.outcomes] == ["US"]
    assert [(s.market, s.mode) for s in res.stats] == [("US", Mode.SWING)]
    both = run(paths, Loader(frames))
    assert [(s.market, s.n) for s in both.stats] == [("NSE", 1), ("US", 1)]
    with pytest.raises(ValueError):
        run(paths, market="LSE")


# ---------------------------------------------------------------- CLI

DISCLAIMER = "Paper record of the desk's TAKEs as if each ticket were followed exactly — not your real P&L."


def _cli_env(tmp_path, monkeypatch, rows):
    from nse_backtest.desk import loaders
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    write_audit(tmp_path / "desk_audit.jsonl", rows)
    monkeypatch.setattr(loaders, "load_daily", Loader({"ABC": frame(WIN_BARS), "XYZ": frame(WIN_BARS)}))
    monkeypatch.setattr(loaders, "load_intraday", boom_loader())


def test_cli_text(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST)),
                                     row(datetime(2026, 10, 16, 10, 0, tzinfo=ET), market="US", symbol="XYZ",
                                         h="u"), "garbage{"])
    assert main(["scoreboard", "--market", "ALL"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == DISCLAIMER
    v = next(i for i, x in enumerate(out) if "Not enough trades yet" in x)
    s = next(i for i, x in enumerate(out) if "win" in x and "mean realised" in x)
    assert v < s
    text = "\n".join(out)
    assert "₹100.00" in text and "$100.00" in text and "won" in text
    assert "Rejected 1 audit line" in text and "line 3" in text


def test_cli_default_market_all_and_json(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST)),
                                     row(datetime(2026, 10, 16, 10, 0, tzinfo=ET), market="US", symbol="XYZ",
                                         h="u")])
    assert main(["scoreboard", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {o["candidate"]["market"] for o in data["outcomes"]} == {"NSE", "US"}
    assert set(data) == {"outcomes", "stats", "rejects", "cache_note"}
    assert main(["scoreboard", "--market", "US", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {o["candidate"]["market"] for o in data["outcomes"]} == {"US"}


def test_cli_empty_audit_and_bad_market(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, [])
    assert main(["scoreboard", "--market", "NSE"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == DISCLAIMER
    assert any(x.startswith("No TAKE verdicts logged yet for NSE") for x in out)
    assert main(["scoreboard", "--market", "LSE"]) == 2


# ---------------------------------------------------------------- page tab

def _fake_result():
    from nse_backtest.desk.scoreboard import Candidate, Outcome, summarize
    c = Candidate(datetime(2026, 10, 16, 10, 0, tzinfo=IST), "NSE", "ABC", Mode.SWING, 100.0, 98.0, 103.0, 15,
                  0.0, 10, 0.3, 0.2, "h|x")
    outs = [Outcome(c, Status.WON, 100.0, "2026-10-16", 103.0, "2026-10-19", 1.5, None, 1, "")]
    return session.ScoreboardResult(outs, summarize(outs), [(3, "unreadable audit line")], None)


def _page(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    from tests.test_desk_page_smoke import _patch_ok
    _patch_ok(monkeypatch, tmp_path)
    return AppTest.from_file("pages/0_Desk.py", default_timeout=120)


def test_page_scoreboard_tab_renders_verdict(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(session, "run_scoreboard", lambda market=None, **k: calls.append(market) or _fake_result())
    at = _page(monkeypatch, tmp_path).run()
    assert not at.exception
    assert [t.label for t in at.tabs][-1] == "Scoreboard"
    assert calls == []  # lazy: nothing loads until the button
    at.button(key="desk_load_scoreboard").click().run()
    assert not at.exception and calls == ["NSE"]
    assert any("Not enough trades yet" in i.value for i in at.info)
    assert any(DISCLAIMER in c.value for c in at.caption)


def test_page_scoreboard_error_is_shown(monkeypatch, tmp_path):
    def bad(market=None, **k):
        raise RuntimeError("audit unreadable")
    monkeypatch.setattr(session, "run_scoreboard", bad)
    at = _page(monkeypatch, tmp_path).run()
    at.button(key="desk_load_scoreboard").click().run()
    assert not at.exception
    assert any("audit unreadable" in e.value for e in at.error)
