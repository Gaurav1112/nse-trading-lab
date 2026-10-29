"""H5b: the pooled ORB-short edge gate, the regime per historical day and the daily cache. No network."""
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from nse_backtest.desk import shortcheck
from nse_backtest.desk.markets import NSE, US
from nse_backtest.desk.models import ShortEdge
from nse_backtest.desk.shortcheck import (MIN_TRADES, load_edge, pooled_short_edge, regime_by_day, save_edge)
from nse_backtest.desk.timemachine import short_cost_fn, short_trades


# ---------------------------------------------------------------- frames with one known short per session

def _session(d, win: bool, base=100.0):
    """One 15-minute session: OR base-1..base+1 (trigger base-1, stop base), a breakdown at 09:45, then either the
    target (win) or the stop (loss) on 10:00."""
    b = base
    bars = [("09:15", b, b + 1, b - 1, b), ("09:30", b, b + 1, b - 0.9, b),
            ("09:45", b - 1, b - 0.5, b - 1.6, b - 1.2)]
    bars.append(("10:00", b - 1.2, b - 1.1, b - 3, b - 2.8) if win else ("10:00", b - 1.2, b + 0.5, b - 1.3, b + 0.2))
    return [(pd.Timestamp(f"{d.date()} {t}"), o, h, lo, c) for t, o, h, lo, c in bars]


def frame(n_days, win=True, start="2026-01-05"):
    """n_days replayable sessions plus a trailing latest session (left out of every replay)."""
    days = pd.bdate_range(start, periods=n_days + 1)
    rows = [r for k, d in enumerate(days) for r in _session(d, win if k < n_days else True)]
    df = pd.DataFrame([r[1:] for r in rows], columns=["Open", "High", "Low", "Close"],
                      index=pd.DatetimeIndex([r[0] for r in rows]))
    df["Volume"] = 1e5
    return df


def all_hostile(*frames, regime="HOSTILE"):
    return {d.date(): regime for f in frames for d in pd.unique(f.index.normalize())}


def test_each_synthetic_session_is_exactly_one_short():
    f = frame(3)
    ts = short_trades(f, NSE, all_hostile(f))
    assert [t.outcome for t in ts] == ["won"] * 3 and ts[0].entry == 99 and ts[0].stop == 100
    assert [t.outcome for t in short_trades(frame(2, win=False), NSE, all_hostile(frame(2, win=False)))] == ["lost"] * 2


# ---------------------------------------------------------------- the pooled gate

def test_200_winning_trades_at_2x_slippage_are_proven():
    a, b = frame(100), frame(100, start="2026-01-06")
    edge = pooled_short_edge(NSE, {"AAA": a, "BBB": b}, all_hostile(a, b))
    assert isinstance(edge, ShortEdge) and edge.n == 200 == MIN_TRADES
    fn = short_cost_fn(NSE, 2.0)
    assert edge.expectancy_r == pytest.approx(1.5 - fn(99.0, 97.5))
    assert edge.proven is True


def test_199_trades_are_not_proven_however_good():
    a, b = frame(100), frame(99)
    edge = pooled_short_edge(NSE, {"AAA": a, "BBB": b}, all_hostile(a, b))
    assert edge.n == 199 and edge.expectancy_r > 1 and edge.proven is False


def test_negative_expectancy_is_not_proven():
    a, b = frame(100, win=False), frame(100)
    edge = pooled_short_edge(NSE, {"AAA": a, "BBB": b}, all_hostile(a, b))
    fn = short_cost_fn(NSE, 2.0)
    assert edge.n == 200 and edge.expectancy_r == pytest.approx(
        ((-1 - fn(99.0, 100.0)) + (1.5 - fn(99.0, 97.5))) / 2)
    a2 = frame(150, win=False)
    edge2 = pooled_short_edge(NSE, {"AAA": a2, "BBB": frame(60)}, all_hostile(a2, frame(60)))
    assert edge2.n == 210 and edge2.expectancy_r < 0 and edge2.proven is False


def test_zero_expectancy_is_not_proven():
    assert ShortEdge.from_trades(250, 0.0).proven is False
    assert ShortEdge.from_trades(0, None).proven is False


def test_only_hostile_days_are_pooled():
    a, b = frame(100), frame(100, start="2026-01-06")
    reg = all_hostile(a, b)
    for d in list(reg)[::2]:
        reg[d] = "MIXED"
    edge = pooled_short_edge(NSE, {"AAA": a, "BBB": b}, reg)
    assert edge.n < 200 and edge.proven is False
    assert pooled_short_edge(NSE, {"AAA": a}, {}).n == 0


def test_the_gate_is_costed_at_twice_the_slippage():
    a = frame(30)
    one = pooled_short_edge(NSE, {"AAA": a}, all_hostile(a), slippage_mult=1.0)
    two = pooled_short_edge(NSE, {"AAA": a}, all_hostile(a))
    assert two.expectancy_r < one.expectancy_r
    assert two.expectancy_r == pytest.approx(1.5 - short_cost_fn(NSE, 2.0)(99.0, 97.5))


def test_the_gate_is_deterministic_and_order_free():
    a, b = frame(40), frame(37, win=False, start="2026-02-02")
    reg = all_hostile(a, b)
    x = pooled_short_edge(NSE, {"AAA": a, "BBB": b}, reg)
    y = pooled_short_edge(NSE, {"BBB": b, "AAA": a}, reg)
    assert x == y and repr(x.expectancy_r) == repr(y.expectancy_r)


def test_missing_or_broken_frames_are_skipped_not_fatal_and_reported():
    a = frame(20)
    bad = a.drop(columns=["Close"])
    edge = pooled_short_edge(NSE, {"AAA": a, "BAD": bad, "NONE": None}, all_hostile(a))
    assert edge.n == 20 and edge.skipped == 2
    assert edge.skip_reason.startswith("BAD: KeyError")  # the first one, in symbol order
    assert pooled_short_edge(NSE, {"AAA": a}, all_hostile(a)).skipped == 0


def test_the_gate_is_nse_only():
    with pytest.raises(ValueError, match="NSE"):
        pooled_short_edge(US, {}, {})


# ---------------------------------------------------------------- the regime of each past day, without look-ahead

def index_frame(n=500, end="2026-10-29", seed=4):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=n)
    close = 20000 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n)))
    return pd.DataFrame({"Open": close, "High": close * 1.004, "Low": close * 0.996, "Close": close,
                         "Volume": 1e6}, index=idx)


def test_regime_of_day_d_is_the_tape_classifier_on_the_bars_before_d():
    from nse_backtest import tape_monitor
    idx = index_frame()
    days = [d.date() for d in idx.index[-40:]]
    reg = regime_by_day(idx, days)
    assert set(reg) == set(days)
    for d in days[::7]:
        assert reg[d] == tape_monitor.assess_tape(idx[idx.index.normalize() < pd.Timestamp(d)]).regime


def test_regime_of_day_d_never_looks_at_day_d_or_later():
    idx = index_frame()
    idx["Close"] = 20000 * 1.003 ** np.arange(len(idx))  # a clean uptrend: TRENDING every day
    days = [d.date() for d in idx.index[-40:]]
    reg = regime_by_day(idx, days)
    d = days[20]
    crashed = idx.copy()
    later = crashed.index.normalize() >= pd.Timestamp(d)
    crashed.loc[later, ["Open", "High", "Low", "Close"]] *= 0.5  # day d's own bar and every later one
    reg2 = regime_by_day(crashed, days)
    assert set(reg.values()) == {"TRENDING"}
    assert all(reg2[x] == reg[x] for x in days if x <= d)
    assert any(reg2[x] != reg[x] for x in days if x > d)  # the crash is visible from the next day on


def test_a_crash_before_day_d_does_show_on_day_d():
    idx = index_frame(seed=8)
    d = idx.index[-5].date()
    up = idx.copy()
    up["Close"] = 20000 * 1.003 ** np.arange(len(up))  # a clean uptrend: TRENDING
    assert regime_by_day(up, [d])[d] == "TRENDING"
    up.iloc[-60:-5, up.columns.get_loc("Close")] *= 0.8  # a crash in the 60 days before d
    assert regime_by_day(up, [d])[d] == "HOSTILE"


def test_days_without_enough_or_recent_index_history_are_left_out():
    idx = index_frame(n=300)
    first = idx.index[150].date()  # only 150 bars before it: the classifier needs 200
    assert first not in regime_by_day(idx, [first])
    late = date(2026, 12, 15)  # weeks after the index's last bar: stale, not classified
    assert regime_by_day(idx, [late]) == {}
    assert regime_by_day(None, [first]) == {}


# ---------------------------------------------------------------- the daily cache

DAY = date(2026, 10, 30)


def test_cache_round_trip_for_the_same_day(tmp_path):
    from dataclasses import replace as _r
    p = tmp_path / "desk_shortcheck.json"
    from tests.desk.test_f2_review import covered  # F2/M6+M11: a cached figure carries its coverage and digest
    e = covered(_r(ShortEdge.from_trades(212, 0.0712), skipped=3, skip_reason="ZZZ: no bars"), pooled=31,
                eligible=34)
    save_edge(p, NSE, DAY, e, symbols=31)
    got, why = load_edge(p, NSE, DAY)
    assert got == e and why == "" and (got.skipped, got.skip_reason) == (3, "ZZZ: no bars")
    d = json.loads(p.read_text())
    assert d["day"] == "2026-10-30" and d["computed_at"].endswith("+05:30")  # market time, not the laptop's


def test_cache_from_another_day_is_not_used(tmp_path):
    p = tmp_path / "s.json"
    save_edge(p, NSE, date(2026, 10, 29), ShortEdge.from_trades(250, 0.2), symbols=40)
    got, why = load_edge(p, NSE, DAY)
    assert got is None and "not computed today" in why


def test_missing_cache(tmp_path):
    got, why = load_edge(tmp_path / "nope.json", NSE, DAY)
    assert got is None and "not computed today" in why


@pytest.mark.parametrize("body", ["{not json", "[]", '{"version": 1}', ""])
def test_unreadable_cache_fails_closed(tmp_path, body):
    p = tmp_path / "s.json"
    p.write_text(body)
    got, why = load_edge(p, NSE, DAY)
    assert got is None and "unreadable" in why


@pytest.mark.parametrize("patch", [
    {"proven": True, "n": 150},             # a proven flag the numbers do not support
    {"n": -3}, {"n": 2.5}, {"n": True},
    {"expectancy_r": "0.3"}, {"expectancy_r": float("nan")},
    {"slippage_mult": 1.0},                 # computed under another rule
    {"min_trades": 50},
    {"market": "US"},
])
def test_tampered_or_foreign_cache_fails_closed(tmp_path, patch):
    p = tmp_path / "s.json"
    save_edge(p, NSE, DAY, ShortEdge.from_trades(250, 0.2), symbols=40)
    d = json.loads(p.read_text())
    d.update(patch)
    p.write_text(json.dumps(d))
    got, why = load_edge(p, NSE, DAY)
    assert got is None and why


def test_cache_write_is_atomic_and_leaves_no_temp_files(tmp_path):
    p = tmp_path / "s.json"
    from tests.desk.test_f2_review import covered  # F2/M6+M11: a cached figure carries its coverage and digest
    save_edge(p, NSE, DAY, covered(ShortEdge.from_trades(10, -0.1), pooled=5, eligible=5), symbols=5)
    save_edge(p, NSE, DAY, covered(ShortEdge.from_trades(11, -0.2), pooled=5, eligible=5), symbols=5)
    assert [x.name for x in tmp_path.iterdir()] == ["s.json"]
    assert load_edge(p, NSE, DAY)[0].n == 11


def test_edge_text():
    assert shortcheck.edge_line(ShortEdge.from_trades(143, 0.0512)) == \
        ("143 trades, +0.05R at 2× slippage, HOSTILE days: not proven (needs at least 200 trades and a positive R)"
         "; universe = today's F&O list, past bans not replayed")
    assert shortcheck.edge_line(ShortEdge.from_trades(0, None)).startswith("0 trades, no expectancy yet")
    assert shortcheck.edge_line(ShortEdge.from_trades(240, 0.11)).endswith(
        ": proven; universe = today's F&O list, past bans not replayed")


# ---------------------------------------------------------------- session: run_shortcheck and the context it feeds
from datetime import datetime  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from nse_backtest.desk import loaders, session  # noqa: E402

NOW = datetime(2026, 10, 30, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))


def _index_hostile():
    idx = index_frame(n=500, end="2026-10-29")
    idx["Close"] = 20000 * 0.999 ** np.arange(len(idx))  # a steady fall: HOSTILE every day
    return idx


@pytest.fixture
def offline_short(tmp_path, monkeypatch):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(session, "universe", lambda p: ["AAA", "BBB", "CCC"])
    monkeypatch.setattr(loaders, "fno_underlyings", lambda p, now=None: (frozenset({"AAA", "BBB"}), None))
    monkeypatch.setattr(loaders, "load_regime", lambda p: _index_hostile())
    loaded = []

    def intraday(p, s):
        loaded.append(s)
        return frame(100, start="2026-06-01") if s == "AAA" else frame(100, start="2026-06-02")
    monkeypatch.setattr(loaders, "load_intraday", intraday)
    return loaded


def test_run_shortcheck_pools_the_fno_universe_and_caches_for_the_day(offline_short, tmp_path):
    res = session.run_shortcheck(NSE, now=NOW)
    assert offline_short == ["AAA", "BBB"]  # CCC is not an F&O stock: not eligible, not pooled
    assert res.edge.n == 200 and res.edge.proven and not res.cached and res.symbols == 2
    assert json.loads((tmp_path / "desk_shortcheck.json").read_text())["day"] == "2026-10-30"
    again = session.run_shortcheck(NSE, now=NOW)
    assert again.cached and again.edge == res.edge and again.symbols == 2 and offline_short == ["AAA", "BBB"]
    fresh = session.run_shortcheck(NSE, now=NOW, refresh=True)
    assert not fresh.cached and offline_short == ["AAA", "BBB"] * 2
    assert session.short_edge_today(NSE, NOW) == (res.edge, "")


def test_run_shortcheck_fails_closed_without_the_fno_list_or_the_index(offline_short, monkeypatch, tmp_path):
    monkeypatch.setattr(loaders, "fno_underlyings", lambda p, now=None: (None, None))
    with pytest.raises(ValueError, match="could not verify F&O eligibility"):
        session.run_shortcheck(NSE, now=NOW)
    monkeypatch.setattr(loaders, "fno_underlyings", lambda p, now=None: (frozenset({"AAA"}), None))

    def boom(p):
        raise OSError("down")
    monkeypatch.setattr(loaders, "load_regime", boom)
    with pytest.raises(ValueError, match="regime index unavailable"):
        session.run_shortcheck(NSE, now=NOW)
    assert not (tmp_path / "desk_shortcheck.json").exists()
    with pytest.raises(ValueError, match="NSE only"):
        session.run_shortcheck(US, now=NOW)


def test_run_shortcheck_skips_a_symbol_without_bars(offline_short, monkeypatch):
    def intraday(p, s):
        if s == "BBB":
            raise ValueError("no intraday data for BBB")
        return frame(100, start="2026-06-01")
    monkeypatch.setattr(loaders, "load_intraday", intraday)
    warns = []
    res = session.run_shortcheck(NSE, now=NOW, on_warning=warns.append)
    assert res.edge.n == 100 and not res.edge.proven and res.failures == (("BBB", "no intraday data for BBB"),)
    assert any("BBB" in w for w in warns)


def test_short_context_carries_the_cached_edge_or_why_not(offline_short, tmp_path):
    ctx = session.context_for(NSE, session.loaders.default_limits(NSE), regime="HOSTILE")
    ctx = type(ctx)(**{**ctx.__dict__, "now": NOW})
    warns = []
    c = session._with_fno(ctx, NSE, warns.append)
    assert c.short_edge is None and "not computed today" in c.short_edge_why
    assert any("short edge check unavailable" in w for w in warns)
    assert c.regime_by_day and set(c.regime_by_day.values()) == {"HOSTILE"}
    session.run_shortcheck(NSE, now=NOW)
    c = session._with_fno(ctx, NSE, None)
    assert c.short_edge is not None and c.short_edge.proven and c.short_edge_why == ""
    (tmp_path / "desk_shortcheck.json").write_text("{broken")
    c = session._with_fno(ctx, NSE, None)
    assert c.short_edge is None and "unreadable" in c.short_edge_why


# ---------------------------------------------------------------- CLI: shortcheck and check --side short
from nse_backtest.desk import cli  # noqa: E402


@pytest.fixture
def cli_now(monkeypatch):
    monkeypatch.setattr(cli, "market_now", lambda p: NOW)


def test_cli_shortcheck_prints_and_caches(offline_short, cli_now, capsys, tmp_path):
    assert cli.main(["shortcheck"]) == 0
    out = capsys.readouterr().out
    assert "Pooled ORB-short history NSE" in out
    assert "trades 200" in out and "expectancy +" in out and "proven yes" in out
    assert "2 eligible symbols" in out and "computed now, cached for 2026-10-30" in out
    assert (tmp_path / "desk_shortcheck.json").exists()
    assert cli.main(["shortcheck", "--market", "NSE"]) == 0
    assert "cached for 2026-10-30" in capsys.readouterr().out and offline_short == ["AAA", "BBB"]
    assert cli.main(["shortcheck", "--refresh"]) == 0
    assert offline_short == ["AAA", "BBB"] * 2


def test_cli_shortcheck_json(offline_short, cli_now, capsys):
    assert cli.main(["shortcheck", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["n"] == 200 and d["proven"] is True and d["day"] == "2026-10-30" and d["cached"] is False
    assert d["expectancy_r"] == pytest.approx(1.5 - short_cost_fn(NSE, 2.0)(99.0, 97.5))


def test_cli_shortcheck_not_proven_says_why(offline_short, cli_now, capsys, monkeypatch):
    monkeypatch.setattr(loaders, "fno_underlyings", lambda p, now=None: (frozenset({"AAA"}), None))
    assert cli.main(["shortcheck"]) == 0
    out = capsys.readouterr().out
    assert "trades 100" in out and "proven no" in out
    assert "universe = today's F&O list, past bans not replayed" in out
    assert "Every short stays at most WAIT: edge not proven on the desk's own history yet (100 trades, +" in out


def test_cli_shortcheck_errors(offline_short, cli_now, capsys, monkeypatch):
    assert cli.main(["shortcheck", "--market", "US"]) == 2
    assert "NSE only" in capsys.readouterr().err
    monkeypatch.setattr(loaders, "fno_underlyings", lambda p, now=None: (None, None))
    assert cli.main(["shortcheck"]) == 2
    assert "could not verify F&O eligibility" in capsys.readouterr().err


def _fake_short_verdict():
    from nse_backtest.desk.checks import CheckContext
    from nse_backtest.desk.models import Limits, Mode, Side
    from nse_backtest.desk.service import check_trade
    from tests.desk.test_verdict_service import _breakdown_frame, _pin_ctx
    df, _ = _breakdown_frame()
    c = _pin_ctx(now=datetime(2026, 10, 29, 9, 55, tzinfo=ZoneInfo("Asia/Kolkata")), regime="HOSTILE",
                 fno=frozenset({"ABC"}), short_edge=ShortEdge.from_trades(143, 0.0512))
    assert isinstance(c, CheckContext)
    return check_trade(df, "ABC", NSE, Mode.INTRADAY, Limits(capital=100_000), c, side=Side.SHORT, audit=False,
                       replays=1000, robustness=False)


def test_cli_check_side_short_runs_a_short_check(offline_short, cli_now, capsys, monkeypatch):
    from nse_backtest.desk.models import Mode, Side
    v = _fake_short_verdict()
    asked = []
    monkeypatch.setattr(session, "run_check", lambda p, sym, mode, limits, **k: asked.append((mode, k)) or v)
    assert cli.main(["check", "ABC", "--mode", "intraday", "--side", "short"]) == 0
    out = capsys.readouterr().out
    assert asked[0][0] is Mode.INTRADAY and asked[0][1]["side"] is Side.SHORT
    assert "Plan (intraday, short)" in out and "short odds arrive" not in out
    assert "Odds (1000 replays): P(target)" in out
    assert "edge not proven on the desk's own history yet (143 trades, +0.05R)" in out
    assert cli.main(["check", "ABC", "--mode", "intraday"]) == 0
    assert asked[1][1]["side"] is Side.LONG


def test_cli_check_side_short_is_nse_intraday_only(offline_short, cli_now, capsys):
    assert cli.main(["check", "ABC", "--side", "short"]) == 2
    assert "--side short needs --mode intraday" in capsys.readouterr().err
    assert cli.main(["check", "AAPL", "--market", "US", "--mode", "intraday", "--side", "short"]) == 2
    assert "shorts are NSE only" in capsys.readouterr().err


def test_cli_short_without_odds_says_unavailable_plainly(capsys):
    from dataclasses import replace as _replace
    v = _replace(_fake_short_verdict(), odds=None)
    text = "\n".join(cli.format_verdict(v, NSE))
    assert "Odds: unavailable (not enough history for a reliable estimate)" in text
    assert "next update" not in text


def test_cli_shortcheck_reports_skipped_frames(offline_short, cli_now, capsys, monkeypatch):
    def intraday(p, s):
        f = frame(100, start="2026-06-01")
        return f.drop(columns=["Close"]) if s == "BBB" else f
    monkeypatch.setattr(loaders, "load_intraday", intraday)
    assert cli.main(["shortcheck"]) == 0
    out = capsys.readouterr().out
    assert "Skipped 1 frame (first: BBB: KeyError" in out
