"""Scoreboard I/O: audit reading, swing fill session, cache, CLI and page tab. Fictional prices only; no network."""
import json
import os
import re
import stat
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from nse_backtest.desk import scoreboard_io, session
from nse_backtest.desk.models import Mode
from nse_backtest.desk.scoreboard import SCOREBOARD_VERSION, Status

IST = ZoneInfo("Asia/Kolkata")
ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=IST)


LAST_CLOSE = 100.0  # every frame() starts with a bar closing here on the trading day before its first date


def row(created, symbol="ABC", market="NSE", mode="swing", entry=100.0, stop=98.0, target_1=103.0, h="h1",
        action="TAKE", **kw):
    """An audit TAKE row. last_bar defaults to the trading day before created_at's market-local date, which is
    the bar frame() prepends when the frame starts on that date (the fill session in most tests)."""
    from nse_backtest.desk import calendars
    from nse_backtest.desk.markets import get_profile
    p = get_profile(market)
    prior = calendars.previous_trading_day(p, created.astimezone(ZoneInfo(p.tz)).date())
    r = {"created_at": created.isoformat(), "action": action, "symbol": symbol, "market": market, "mode": mode,
         "entry": entry, "stop": stop, "target_1": target_1, "qty": 10, "mc_expectancy_r": 0.3,
         "history_expectancy_r": 0.2, "input_hash": h, "max_hold_bars": 15, "cost_per_share": 0.0,
         "last_bar": f"{prior.isoformat()}T00:00:00", "last_close": LAST_CLOSE}
    if mode == "intraday":  # the plan's last bar is the 15-minute bar in progress at created_at
        local = created.astimezone(ZoneInfo(p.tz)).replace(tzinfo=None)
        r["last_bar"] = local.replace(minute=local.minute // 15 * 15, second=0, microsecond=0).isoformat()
        r["last_close"] = None  # unknown to the fake bars: the 20% entry band applies
    r.update(kw)
    return r


def write_audit(path, rows):
    path.write_text("".join((x if isinstance(x, str) else json.dumps(x)) + "\n" for x in rows), encoding="utf-8")


def frame(bars: dict, market="NSE", prior=True) -> pd.DataFrame:
    """{date-string: (open, high, low, close)} -> daily OHLC frame. prior=True prepends the ticket's last bar:
    the trading day before the first date, closing at LAST_CLOSE (what row() logs), so the price scale checks."""
    from nse_backtest.desk import calendars
    from nse_backtest.desk.markets import get_profile
    bars = dict(bars)
    if prior and bars:
        first = pd.Timestamp(next(iter(bars))).date()
        before = calendars.previous_trading_day(get_profile(market), first)
        bars = {before.isoformat(): (LAST_CLOSE,) * 4, **bars}
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in bars])
    return pd.DataFrame(list(bars.values()), index=idx, columns=["Open", "High", "Low", "Close"])


WIN_BARS = {"2026-10-16": (101, 101.5, 99.5, 100.5), "2026-10-19": (100.5, 103.2, 100.1, 103)}
PRE_OPEN_16 = datetime(2026, 10, 16, 9, 0, tzinfo=IST)  # before the open: the daily fill rule applies


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
    """A loader that must not be called. Production code turns loader errors into UNAVAILABLE, so tests check
    `.calls` is empty instead of relying on the exception."""
    return Loader(exc=RuntimeError("loader must not be called"))


@pytest.fixture
def paths(tmp_path):
    return tmp_path / "desk_audit.jsonl", tmp_path / "desk_scoreboard.json"


def run(paths, loader=None, intraday=None, **kw):
    audit, cache = paths
    kw.setdefault("now", NOW)
    unused = [b for b in (None if loader else boom_loader(), None if intraday else boom_loader()) if b]
    res = session.run_scoreboard(audit_path=audit, cache_path=cache, load_daily=loader or unused[0],
                                 load_intraday=intraday or unused[-1], **kw)
    for b in unused:
        assert b.calls == [], f"unexpected load: {b.calls}"
    return res


# ---------------------------------------------------------------- swing + cache

def test_swing_won_then_served_from_cache(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
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
    write_audit(paths[0], [row(datetime(2026, 10, 27, 9, 0, tzinfo=IST), symbol="OPN", h="o"),
                           row(datetime(2026, 10, 16, 9, 0, tzinfo=IST), symbol="BAD", h="b")])

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
    li = boom_loader()
    res = run(paths, intraday=li)
    o = res.outcomes[0]
    assert o.status is Status.UNAVAILABLE and "older than 60 days" in o.note
    assert li.calls == []


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
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
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
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
    paths[1].write_text(bad, encoding="utf-8")
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert list(paths[1].parent.glob("desk_scoreboard.json.corrupt-*"))
    assert res.cache_note and res.outcomes[0].status is Status.WON


def test_wrong_version_rebuilt(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
    paths[1].write_text(json.dumps({"version": SCOREBOARD_VERSION + 1, "outcomes": {}}), encoding="utf-8")
    ld = Loader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.calls and res.cache_note and "version" in res.cache_note
    data = json.loads(paths[1].read_text())
    assert data["version"] == SCOREBOARD_VERSION and len(data["outcomes"]) == 1
    kept = list(paths[1].parent.glob("desk_scoreboard.json.other-version-*"))  # never silently destroyed
    assert len(kept) == 1 and json.loads(kept[0].read_text())["version"] == SCOREBOARD_VERSION + 1


def test_bool_version_is_not_a_match(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    data = json.loads(paths[1].read_text())
    data["version"] = True
    paths[1].write_text(json.dumps(data), encoding="utf-8")
    ld = Loader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.calls and res.cache_note


def test_changed_candidate_fields_recomputed(paths):
    created = datetime(2026, 10, 16, 9, 0, tzinfo=IST)
    write_audit(paths[0], [row(created)])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    write_audit(paths[0], [row(created, target_1=104.0)])  # same input_hash + created_at = same key
    ld = Loader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.calls == [("NSE", "ABC")]
    # 103.2 no longer reaches the target (recompute is OPEN, not final): the old entry is kept, never served
    o = res.outcomes[0]
    assert o.status is Status.UNAVAILABLE
    assert "cached result no longer matches the logged ticket (kept in cache)" in o.note
    (entry,) = json.loads(paths[1].read_text())["outcomes"].values()
    assert entry["candidate"]["target_1"] == 103.0 and entry["outcome"]["status"] == "won"


def test_changed_candidate_fields_final_recompute_replaces_entry(paths):
    created = datetime(2026, 10, 16, 9, 0, tzinfo=IST)
    write_audit(paths[0], [row(created)])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    write_audit(paths[0], [row(created, target_1=103.1)])
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert res.outcomes[0].status is Status.WON and res.outcomes[0].note == ""
    (entry,) = json.loads(paths[1].read_text())["outcomes"].values()
    assert entry["candidate"]["target_1"] == 103.1


def test_tampered_cache_entry_dropped_and_recomputed(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
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
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST), symbol="AAA", h="a"),
                           row(datetime(2026, 10, 16, 9, 5, tzinfo=IST), symbol="BBB", h="b")])
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
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST)), "garbage{", "",
                           row(datetime(2026, 10, 16, 9, 10, tzinfo=IST), symbol="XYZ", entry=None), "[1, 2]",
                           row(datetime(2026, 10, 16, 9, 10, tzinfo=IST), action="SKIP", h="s")])
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert [n for n, _ in res.rejects] == [2, 4, 5]
    assert "unreadable audit line" in res.rejects[0][1] and "entry" in res.rejects[1][1]
    assert [o.status for o in res.outcomes] == [Status.WON]


def test_missing_audit_is_empty(paths):
    res = run(paths)
    assert res.outcomes == [] and res.stats == [] and res.rejects == [] and res.cache_note is None
    assert res.note == "No audit log at desk_audit.jsonl in the data directory"  # R1: said, not silent


def test_cache_file_mode_and_atomic_write(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert stat.S_IMODE(os.stat(paths[1]).st_mode) == 0o600
    assert sorted(p.name for p in paths[1].parent.iterdir()) == ["desk_audit.jsonl", "desk_scoreboard.json"]
    before = os.stat(paths[1]).st_mtime_ns
    os.utime(paths[1], ns=(before - 10**9, before - 10**9))
    run(paths, Loader(exc=RuntimeError("cached")))
    assert os.stat(paths[1]).st_mtime_ns == before - 10**9  # nothing changed: not rewritten


def test_market_filter_and_stats_never_combine(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST), h="n"),
                           row(datetime(2026, 10, 16, 9, 0, tzinfo=ET), market="US", symbol="XYZ", h="u")])
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
    li = boom_loader()
    monkeypatch.setattr(loaders, "load_intraday", li)
    return li


def test_cli_text(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST)),
                                     row(datetime(2026, 10, 16, 9, 0, tzinfo=ET), market="US", symbol="XYZ",
                                         h="u"), "garbage{"])
    assert main(["scoreboard", "--market", "ALL"]) == 0
    from nse_backtest.desk import loaders
    assert loaders.load_intraday.calls == []
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
    _cli_env(tmp_path, monkeypatch, [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST)),
                                     row(datetime(2026, 10, 16, 9, 0, tzinfo=ET), market="US", symbol="XYZ",
                                         h="u")])
    assert main(["scoreboard", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {o["candidate"]["market"] for o in data["outcomes"]} == {"NSE", "US"}
    assert set(data) == {"outcomes", "stats", "rejects", "cache_note", "note"}  # R1: note (missing audit log)
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
    order = [type(e).__name__ for e in at.tabs[4].children.values()]
    assert order.index("Info") < order.index("Dataframe")  # verdict banner before the stats table
    assert order[0] == "Caption" and DISCLAIMER in list(at.tabs[4].children.values())[0].value


def test_page_scoreboard_error_is_shown(monkeypatch, tmp_path):
    def bad(market=None, **k):
        raise RuntimeError("audit unreadable")
    monkeypatch.setattr(session, "run_scoreboard", bad)
    at = _page(monkeypatch, tmp_path).run()
    at.button(key="desk_load_scoreboard").click().run()
    assert not at.exception
    assert any("audit unreadable" in e.value for e in at.error)


# ---------------------------------------------------------------- fix round 1

def _cached(paths) -> dict:
    return json.loads(paths[1].read_text())["outcomes"] if paths[1].exists() else {}


def test_nan_cache_entry_dropped_and_cache_repaired(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST), symbol="AAA", h="a")])
    run(paths, Loader({"AAA": frame(WIN_BARS)}))
    data = json.loads(paths[1].read_text())
    (entry,) = data["outcomes"].values()
    entry["candidate"]["mc_expectancy_r"] = float("nan")  # a field the match does not compare
    paths[1].write_text(json.dumps(data), encoding="utf-8")  # json writes a bare NaN
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST), symbol="AAA", h="a"),
                           row(datetime(2026, 10, 16, 9, 5, tzinfo=IST), symbol="BBB", h="b")])
    ld = Loader({"AAA": frame(WIN_BARS), "BBB": frame(WIN_BARS)})
    res = run(paths, ld)
    assert [o.status for o in res.outcomes] == [Status.WON, Status.WON]
    assert sorted(ld.calls) == [("NSE", "AAA"), ("NSE", "BBB")]  # the NaN entry was dropped and recomputed
    text = paths[1].read_text()
    assert "NaN" not in text and "Infinity" not in text and len(json.loads(text)["outcomes"]) == 2


def test_save_reports_unserialisable_payload(tmp_path):
    warns = []
    cache = scoreboard_io.ScoreboardCache(tmp_path / "c.json", NOW, warns.append)
    cache.entries["x"] = {"bad": float("inf")}
    cache.dirty = True
    cache.save()  # must not raise
    assert cache.note and "could not be written" in cache.note and warns
    assert not (tmp_path / "c.json").exists()


def test_same_day_result_shown_but_not_cached(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 28, 9, 0, tzinfo=IST))])
    today = frame({"2026-10-28": (101, 101, 97, 99)})  # fills at 100 and touches the stop on the same bar
    res = run(paths, Loader({"ABC": today}), now=datetime(2026, 10, 28, 15, 31, tzinfo=IST))
    assert res.outcomes[0].status is Status.LOST
    assert _cached(paths) == {}
    res = run(paths, Loader({"ABC": today}), now=datetime(2026, 10, 29, 12, 0, tzinfo=IST))
    assert res.outcomes[0].status is Status.LOST
    assert [e["outcome"]["status"] for e in _cached(paths).values()] == ["lost"]


def test_same_day_not_filled_and_intraday_not_cached(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 28, 9, 0, tzinfo=IST)),
                           row(datetime(2026, 10, 28, 9, 50, tzinfo=IST), symbol="XYZ", mode="intraday", h="i")])
    idx = pd.date_range("2026-10-28 09:15", "2026-10-28 15:15", freq="15min")
    flat = pd.DataFrame({"Open": 101.0, "High": 101.5, "Low": 100.5, "Close": 101.2}, index=idx)  # R1: above the limit
    res = run(paths, Loader({"ABC": frame({"2026-10-28": (101, 102, 100.5, 101)})}), Loader({"XYZ": flat}),
              now=datetime(2026, 10, 28, 15, 40, tzinfo=IST))
    assert [o.status for o in res.outcomes] == [Status.NOT_FILLED, Status.NOT_FILLED]
    assert _cached(paths) == {}


@pytest.mark.parametrize("bad", [
    '"created_at": "9999-12-31T23:30:00-12:00"', '"created_at": "0001-01-01T00:00:00+14:00"',
    '"qty": 1e999', '"max_hold_bars": 1e999'])
@pytest.mark.parametrize("mode", ["swing", "intraday"])
def test_overflow_audit_rows_are_rejects(paths, bad, mode):
    good = row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))
    base = json.dumps(row(datetime(2026, 10, 16, 9, 10, tzinfo=IST), symbol="XYZ", mode=mode, h="x"))
    key = bad.split(":")[0]
    broken = re.sub(key + r': [^,]+', bad, base, count=1)
    assert broken != base
    write_audit(paths[0], [good, broken])
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert [n for n, _ in res.rejects] == [2]
    assert [o.candidate.symbol for o in res.outcomes] == ["ABC"]


def test_audit_path_unreadable_is_a_clear_error(tmp_path):
    with pytest.raises(scoreboard_io.AuditReadError, match="cannot read audit log"):
        session.run_scoreboard(audit_path=tmp_path, cache_path=tmp_path / "c.json", now=NOW,
                               load_daily=boom_loader(), load_intraday=boom_loader())


def test_cli_audit_directory_exit_2(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, [])
    (tmp_path / "desk_audit.jsonl").unlink()
    (tmp_path / "desk_audit.jsonl").mkdir()
    assert main(["scoreboard"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("scoreboard: cannot read audit log") and "Traceback" not in err


def test_cli_runtime_error_exit_2(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, [])

    def lost(*a, **k):
        raise RuntimeError("scoreboard lost a candidate")
    monkeypatch.setattr(session, "run_scoreboard", lost)
    assert main(["scoreboard"]) == 2
    assert "scoreboard: scoreboard lost a candidate" in capsys.readouterr().err


def test_intraday_mismatched_entry_beyond_60_days_kept(paths):
    created = datetime(2026, 10, 27, 9, 50, tzinfo=IST)
    write_audit(paths[0], [row(created, mode="intraday")])
    run(paths, intraday=Loader({"ABC": _intraday_win_bars()}))
    assert len(_cached(paths)) == 1
    write_audit(paths[0], [row(created, mode="intraday", target_1=104.0)])
    li = boom_loader()
    res = run(paths, intraday=li, now=datetime(2027, 1, 29, 12, 0, tzinfo=IST))
    o = res.outcomes[0]
    assert o.status is Status.UNAVAILABLE and li.calls == []
    assert "cached result no longer matches the logged ticket (kept in cache)" in o.note
    (entry,) = _cached(paths).values()
    assert entry["candidate"]["target_1"] == 103.0 and entry["outcome"]["status"] == "won"


def test_replay_error_note_and_warning_carry_type(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 9, 0, tzinfo=IST))])
    no_close = frame(WIN_BARS).drop(columns=["Close"])
    warns = []
    res = run(paths, Loader({"ABC": no_close}), on_warning=warns.append)
    o = res.outcomes[0]
    assert o.status is Status.UNAVAILABLE and o.note.startswith("could not replay (KeyError):")
    assert any("KeyError" in w and "ABC" in w for w in warns)


def _intraday_win_bars(day="2026-10-27"):
    idx = pd.date_range(f"{day} 09:15", f"{day} 15:15", freq="15min")
    bars = pd.DataFrame({"Open": 99.0, "High": 99.5, "Low": 98.5, "Close": 99.2}, index=idx)
    bars.loc[pd.Timestamp(f"{day} 10:00"), ["Open", "High", "Low", "Close"]] = (99.5, 100.5, 99.2, 100.2)
    bars.loc[pd.Timestamp(f"{day} 10:15"), ["Open", "High", "Low", "Close"]] = (100.2, 103.5, 100.1, 103.2)
    return bars


@pytest.mark.parametrize("age,fetched", [(58, True), (59, False)])
def test_intraday_60_day_boundary(paths, age, fetched):
    created = datetime(2026, 10, 29, 10, 0, tzinfo=IST) - timedelta(days=age)
    write_audit(paths[0], [row(created, mode="intraday")])
    li = Loader(exc=RuntimeError("feed down"))
    res = run(paths, intraday=li)
    assert bool(li.calls) is fetched
    assert ("older than 60 days" in res.outcomes[0].note) is (not fetched)


def test_cached_intraday_older_than_60_days_served_without_load(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 27, 9, 50, tzinfo=IST), mode="intraday")])
    run(paths, intraday=Loader({"ABC": _intraday_win_bars()}))
    li = boom_loader()
    res = run(paths, intraday=li, now=datetime(2027, 1, 29, 12, 0, tzinfo=IST))
    assert res.outcomes[0].status is Status.WON and li.calls == []


def test_us_early_close_flat_by_at_io_level(paths):
    write_audit(paths[0], [row(datetime(2026, 11, 27, 10, 0, tzinfo=ET), market="US", mode="intraday")])
    idx = pd.date_range("2026-11-27 09:30", "2026-11-27 12:45", freq="15min")
    bars = pd.DataFrame({"Open": 100.5, "High": 101.0, "Low": 99.5, "Close": 100.8}, index=idx)
    bars.loc[pd.Timestamp("2026-11-27 12:30"), "Close"] = 101.5
    bars.loc[pd.Timestamp("2026-11-27 12:45"), ["High", "Close"]] = (110.0, 109.0)  # at/after flat-by: ignored
    res = run(paths, intraday=Loader({"ABC": bars}), now=datetime(2026, 11, 30, 12, 0, tzinfo=ET))
    o = res.outcomes[0]
    assert o.status is Status.TIME and o.exit_price == 101.5 and o.exit_at.startswith("2026-11-27 12:30")


def test_partial_daily_bar_ignored_before_close(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 28, 9, 0, tzinfo=IST))])
    f = frame({"2026-10-28": (101, 101, 90, 95)})
    res = run(paths, Loader({"ABC": f}), now=datetime(2026, 10, 28, 12, 0, tzinfo=IST))
    assert res.outcomes[0].status is Status.OPEN and _cached(paths) == {}


def test_same_symbol_day_across_markets_and_modes_stay_separate(paths):
    write_audit(paths[0], [
        row(datetime(2026, 10, 27, 9, 0, tzinfo=IST), h="ns"),
        row(datetime(2026, 10, 27, 9, 50, tzinfo=IST), mode="intraday", h="ni"),
        row(datetime(2026, 10, 27, 9, 0, tzinfo=ET), market="US", h="us")])
    daily = frame({"2026-10-27": (101, 101.5, 99.5, 100.5), "2026-10-28": (100.5, 103.2, 100.1, 103)})
    res = run(paths, Loader({"ABC": daily}), Loader({"ABC": _intraday_win_bars()}))
    assert sorted((o.candidate.market, o.candidate.mode.value) for o in res.outcomes) == [
        ("NSE", "intraday"), ("NSE", "swing"), ("US", "swing")]
    assert [(s.market, s.mode.value, s.n) for s in res.stats] == [
        ("NSE", "intraday", 1), ("NSE", "swing", 1), ("US", "swing", 1)]


def test_cli_rejects_only_message(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, ["garbage{", "[1]"])
    assert main(["scoreboard", "--market", "NSE"]) == 0
    out = capsys.readouterr().out
    assert "No valid TAKE verdicts for NSE (2 unreadable — see below)" in out
    assert "No TAKE verdicts logged yet" not in out


# ---------------------------------------------------------------- review round R1

class KwLoader(Loader):
    """Loader that also records keyword arguments."""

    def __call__(self, profile, symbol, *a, **k):
        self.kwargs = getattr(self, "kwargs", []) + [k]
        return super().__call__(profile, symbol, *a, **k)


def test_r1_scoreboard_asks_for_unadjusted_daily_bars(paths):
    write_audit(paths[0], [row(PRE_OPEN_16)])
    ld = KwLoader({"ABC": frame(WIN_BARS)})
    res = run(paths, ld)
    assert ld.kwargs == [{"adjusted": False}] and res.outcomes[0].status is Status.WON


def test_r1_bonus_after_ticket_is_unavailable_not_lost(paths):
    # probe_adj: a 1:1 bonus after the ticket halves the history yfinance serves, even unadjusted
    write_audit(paths[0], [row(datetime(2026, 9, 1, 18, 0, tzinfo=IST), entry=100.0, stop=95.0, target_1=110.0,
                               last_bar="2026-09-01T00:00:00", last_close=101.0)])
    days = pd.bdate_range("2026-09-01", periods=7)
    raw = pd.DataFrame({"Open": [100, 101, 102, 105, 108, 56, 57], "High": [101.5, 102, 104, 108, 112, 57, 58],
                        "Low": [99.5, 99.5, 101, 104, 107, 55, 56], "Close": [101, 101, 103, 107, 111, 56.5, 57.5]},
                       index=days)
    adj = raw.astype(float)
    adj.iloc[:5] = adj.iloc[:5] / 2
    o = run(paths, Loader({"ABC": adj})).outcomes[0]
    assert o.status is Status.UNAVAILABLE and o.note.startswith("prices re-scaled since the ticket (split/bonus?)")
    assert _cached(paths) == {}
    paths[1].unlink(missing_ok=True)
    assert run(paths, Loader({"ABC": raw})).outcomes[0].status is Status.WON


def test_r1_row_without_last_bar_is_unavailable(paths):
    r = row(PRE_OPEN_16)
    del r["last_bar"], r["last_close"]
    write_audit(paths[0], [r])
    o = run(paths, Loader({"ABC": frame(WIN_BARS)})).outcomes[0]
    assert o.status is Status.UNAVAILABLE and "last_bar" in o.note


def test_r1_cache_matches_on_last_bar_and_last_close(paths):
    write_audit(paths[0], [row(PRE_OPEN_16)])
    run(paths, Loader({"ABC": frame(WIN_BARS)}))
    (entry,) = _cached(paths).values()
    assert entry["candidate"]["last_close"] == LAST_CLOSE and entry["candidate"]["last_bar"].startswith("2026-10-15")
    write_audit(paths[0], [row(PRE_OPEN_16, last_close=50.0)])  # same key, different ticket scale
    ld = Loader({"ABC": frame(WIN_BARS)})
    o = run(paths, ld).outcomes[0]
    assert ld.calls and o.status is Status.UNAVAILABLE


def _fill_day_bars(day="2026-10-16", first="10:15", over=None):
    idx = pd.date_range(f"{day} {first}", f"{day} 15:15", freq="15min")
    bars = pd.DataFrame({"Open": 100.5, "High": 100.8, "Low": 100.3, "Close": 100.6}, index=idx)
    for t, v in (over or {}).items():
        bars.loc[pd.Timestamp(f"{day} {t}"), ["Open", "High", "Low", "Close"]] = v
    return bars


def test_r1_mid_session_swing_resolved_from_15_minute_bars(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    li = Loader({"ABC": _fill_day_bars()})  # never back to 100 after 10:00: the daily Low 99.5 came earlier
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}), li)
    assert res.outcomes[0].status is Status.NOT_FILLED and li.calls == [("NSE", "ABC")]
    paths[1].unlink()
    li = Loader({"ABC": _fill_day_bars(over={"11:00": (99.8, 100.1, 99.6, 100.0)})})
    o = run(paths, Loader({"ABC": frame(WIN_BARS)}), li).outcomes[0]
    assert o.status is Status.WON and o.fill_price == pytest.approx(99.8) and o.fill_at.startswith("2026-10-16 11:00")


def test_r1_mid_session_swing_without_intraday_bars_is_unavailable(paths):
    write_audit(paths[0], [row(datetime(2026, 10, 16, 10, 0, tzinfo=IST))])
    warns = []
    o = run(paths, Loader({"ABC": frame(WIN_BARS)}), Loader(exc=RuntimeError("feed down")),
            on_warning=warns.append).outcomes[0]
    assert o.status is Status.UNAVAILABLE
    assert o.note.startswith("TAKE logged mid-session; intraday bars needed for the fill day") and "feed down" in o.note
    assert any("feed down" in w for w in warns)


def test_r1_old_mid_session_swing_unavailable_without_fetch(paths):
    write_audit(paths[0], [row(datetime(2026, 8, 20, 10, 0, tzinfo=IST))])
    days = {d.date().isoformat(): (101, 101.5, 99.5, 100.5) for d in pd.bdate_range("2026-08-20", periods=20)}
    li = boom_loader()
    o = run(paths, Loader({"ABC": frame(days)}), li).outcomes[0]
    assert o.status is Status.UNAVAILABLE and "older than 60 days" in o.note and li.calls == []


# ---- hardening: warnings, missing/oversized files, paths, backups
def test_r1_bar_load_failures_reach_on_warning(paths):
    write_audit(paths[0], [row(PRE_OPEN_16), row(datetime(2026, 10, 27, 9, 50, tzinfo=IST), mode="intraday",
                                                   symbol="XYZ", h="i")])
    warns = []
    res = run(paths, Loader(exc=RuntimeError("daily feed down")), Loader(exc=RuntimeError("intraday feed down")),
              on_warning=warns.append)
    assert [o.status for o in res.outcomes] == [Status.UNAVAILABLE, Status.UNAVAILABLE]
    assert any("daily feed down" in w and "ABC" in w for w in warns)
    assert any("intraday feed down" in w and "XYZ" in w for w in warns)


def test_r1_missing_audit_says_so(paths):
    res = run(paths)
    assert res.outcomes == [] and res.note == "No audit log at desk_audit.jsonl in the data directory"
    write_audit(paths[0], [])
    assert run(paths).note is None


def test_r1_audit_too_large_is_an_error(paths):
    with open(paths[0], "wb") as f:
        f.truncate(51 * 1024 * 1024)  # sparse: no real disk use
    with pytest.raises(scoreboard_io.AuditReadError, match=r"audit log too large \(51 MB\)"):
        run(paths)


def test_r1_cache_too_large_backed_up_and_rebuilt(paths):
    write_audit(paths[0], [row(PRE_OPEN_16)])
    with open(paths[1], "wb") as f:
        f.truncate(21 * 1024 * 1024)
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert res.outcomes[0].status is Status.WON and "too large" in res.cache_note
    backups = list(paths[1].parent.glob("desk_scoreboard.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].stat().st_size == 21 * 1024 * 1024
    assert json.loads(paths[1].read_text())["version"] == SCOREBOARD_VERSION


def test_r1_errors_never_show_full_paths(paths, tmp_path):
    with pytest.raises(scoreboard_io.AuditReadError) as e:
        session.run_scoreboard(audit_path=tmp_path, cache_path=tmp_path / "c.json", now=NOW,
                               load_daily=boom_loader(), load_intraday=boom_loader())
    assert str(tmp_path) not in str(e.value) and tmp_path.name in str(e.value)
    write_audit(paths[0], [row(PRE_OPEN_16)])
    secret = tmp_path / "private" / "cache" / "ABC_1234.parquet"
    res = run(paths, Loader(exc=PermissionError(13, "Permission denied", str(secret))))
    note = res.outcomes[0].note
    assert "Permission denied" in note and "ABC_1234.parquet" in note and str(tmp_path) not in note
    res = run(paths, Loader(exc=RuntimeError(f"Corrupt cache {secret}: bad magic")))
    note = res.outcomes[0].note
    assert "ABC_1234.parquet" in note and str(tmp_path) not in note and "bad magic" in note


def test_r1_backup_never_overwrites_an_existing_backup(paths, monkeypatch):
    write_audit(paths[0], [row(PRE_OPEN_16)])
    paths[1].write_text("{not json", encoding="utf-8")
    stamp = NOW.astimezone(scoreboard_io.timezone.utc).strftime("%Y%m%d%H%M%S")
    taken = paths[1].with_name(f"desk_scoreboard.json.corrupt-{stamp}")
    taken.write_text("older backup", encoding="utf-8")
    monkeypatch.setattr(scoreboard_io.Path, "exists", lambda self: False)  # the name appears after any check
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert taken.read_text() == "older backup"
    new = paths[1].with_name(taken.name + "-1")
    assert new.read_text() == "{not json" and new.name in res.cache_note


def test_r1_save_has_no_chmod_after_replace(tmp_path, monkeypatch):
    def no_chmod(*a, **k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(scoreboard_io.os, "chmod", no_chmod)
    cache = scoreboard_io.ScoreboardCache(tmp_path / "c.json", NOW)
    cache.dirty = True
    cache.save()
    assert cache.note is None and stat.S_IMODE(os.stat(tmp_path / "c.json").st_mode) == 0o600


# ---- surfaces: coverage sentence, warnings and rejects on the page
COVERAGE = "Covers TAKEs from Check a Trade and Today's Plan; Intraday-tab scan TAKEs are not logged yet."


def test_r1_cli_disclaimer_line_2_and_note(tmp_path, monkeypatch, capsys):
    from nse_backtest.desk.cli import main
    _cli_env(tmp_path, monkeypatch, [])
    (tmp_path / "desk_audit.jsonl").unlink()
    assert main(["scoreboard"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == DISCLAIMER and out[1] == COVERAGE
    assert "No audit log at desk_audit.jsonl in the data directory" in out
    assert str(tmp_path) not in "\n".join(out)


def test_r1_page_shows_warnings_rejects_and_coverage(monkeypatch, tmp_path):
    def fake(market=None, on_warning=None, **k):
        on_warning("scoreboard: daily bars for NSE ABC unavailable: feed down")
        return _fake_result()
    monkeypatch.setattr(session, "run_scoreboard", fake)
    at = _page(monkeypatch, tmp_path).run()
    at.button(key="desk_load_scoreboard").click().run()
    assert not at.exception
    tab = at.tabs[4]
    warnings = [w.value for w in tab.warning]
    assert any("feed down" in w for w in warnings)
    assert any(w == "1 audit line could not be read" for w in warnings)
    assert any(COVERAGE in c.value for c in tab.caption)


# ---- R1 follow-up: intraday price scale (yfinance split-adjusts 15-minute bars too)
def test_r1_intraday_halved_bars_unavailable(paths):
    created = datetime(2026, 10, 27, 9, 50, tzinfo=IST)
    write_audit(paths[0], [row(created, mode="intraday", last_bar="2026-10-27T09:45:00", last_close=99.2)])
    o = run(paths, intraday=Loader({"ABC": _intraday_win_bars()})).outcomes[0]
    assert o.status is Status.WON
    paths[1].unlink()
    o = run(paths, intraday=Loader({"ABC": _intraday_win_bars() / 2})).outcomes[0]  # 1:1 bonus after the TAKE
    assert o.status is Status.UNAVAILABLE and o.note.startswith("prices re-scaled since the ticket (split/bonus?)")
    assert _cached(paths) == {}


def test_r1_err_never_raises_on_odd_filename():
    assert scoreboard_io._err(OSError(9, "Bad file descriptor", 3)) == "Bad file descriptor"
    assert scoreboard_io._err(OSError(2, "No such file", b"/a/b/c.json")) == "No such file: c.json"


def test_r1_scrub_keeps_only_the_file_name_even_with_spaces():
    msg = scoreboard_io._scrub("Corrupt cache /Users/x/My Data/nse/desk_scoreboard.json: bad magic")
    assert msg == "Corrupt cache desk_scoreboard.json: bad magic"
    assert scoreboard_io._scrub("ratio 1/2 of 15/30") == "ratio 1/2 of 15/30"


def test_r1_backup_without_hard_link_support_leaves_cache_untouched(paths, monkeypatch):
    write_audit(paths[0], [row(PRE_OPEN_16)])
    paths[1].write_text("{not json", encoding="utf-8")

    def no_link(*a, **k):
        raise NotImplementedError("link unavailable on this platform")
    monkeypatch.setattr(scoreboard_io.os, "link", no_link)
    res = run(paths, Loader({"ABC": frame(WIN_BARS)}))
    assert res.outcomes[0].status is Status.WON
    assert "could not be backed up" in res.cache_note and paths[1].read_text() == "{not json"
