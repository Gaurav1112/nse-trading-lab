"""S4: loaders.load_daily_many — batch daily loading with frames identical to load_daily."""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from nse_backtest import data as data_mod
from nse_backtest.desk import loaders, session
from nse_backtest.desk.markets import NSE
from tests.test_data_batch import multi, single

TZ = loaders.ZoneInfo(NSE.tz)


def via_load_daily(profile, symbols, years=4, *, now=None):
    """Test double for load_daily_many that delegates to whatever loaders.load_daily is (patched or not)."""
    frames, errors = {}, {}
    for s in symbols:
        try:
            frames[s] = loaders.load_daily(profile, s)
        except Exception as e:  # noqa: BLE001
            errors[s] = str(e)
    return frames, errors


def _daily_to(last: str, n=60, base=100.0):
    idx = pd.bdate_range(end=last, periods=n, name="Date")
    close = base + np.arange(n, dtype=float)
    return pd.DataFrame({"Open": close - 0.5, "High": close + 1.0, "Low": close - 1.0, "Close": close,
                         "Volume": np.arange(n, dtype="int64") * 100 + 1000}, index=idx)


def _with_muhurat(df, day="2026-11-08"):
    row = df.iloc[[-1]].copy()
    row.index = pd.DatetimeIndex([pd.Timestamp(day)], name="Date")
    return pd.concat([df, row]).sort_index()


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(data_mod.time, "sleep", lambda s: None)
    return tmp_path / "cache"


def _record(monkeypatch, response):
    calls = []

    def fake(*args, **kwargs):
        t = kwargs.get("tickers", args[0] if args else None)
        calls.append([t] if isinstance(t, str) else sorted(t))
        return response(calls[-1]) if callable(response) else response.copy()

    monkeypatch.setattr(yf, "download", fake)
    return calls


@pytest.mark.parametrize("now", [
    datetime(2026, 11, 9, 10, 0, tzinfo=TZ),   # session open: today's partial bar dropped
    datetime(2026, 11, 9, 19, 0, tzinfo=TZ),   # after close (beyond the 3h re-fetch window): bar kept
])
def test_frames_equal_load_daily(monkeypatch, tmp_path, now):
    raw = {"INFY": _with_muhurat(_daily_to("2026-11-09", base=100)),
           "TCS": _with_muhurat(_daily_to("2026-11-06", n=50, base=300))}  # different dates -> NaN padding
    monkeypatch.setattr(data_mod.time, "sleep", lambda s: None)
    expected = {}
    for sym, df in raw.items():
        monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path / f"single_{sym}"))
        _record(monkeypatch, single(df, f"{sym}.NS"))
        expected[sym] = loaders.load_daily(NSE, sym, now=now)
    monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path / "batch"))
    calls = _record(monkeypatch, multi({f"{s}.NS": df for s, df in raw.items()}))
    frames, errors = loaders.load_daily_many(NSE, ["INFY", "TCS"], now=now)
    assert errors == {} and calls == [["INFY.NS", "TCS.NS"]]
    for sym in raw:
        pd.testing.assert_frame_equal(frames[sym], expected[sym])
        assert date(2026, 11, 8) not in set(frames[sym].index.date)  # Muhurat Sunday dropped
    assert frames["INFY"].index[-1].date() == (date(2026, 11, 6) if now.hour == 10 else date(2026, 11, 9))


def test_shares_cache_files_with_load_daily(monkeypatch, cache_dir):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
    df = _daily_to("2026-11-06")
    _record(monkeypatch, single(df, "INFY.NS"))
    loaders.load_daily(NSE, "INFY", now=now)  # writes the cache
    calls = _record(monkeypatch, multi({"TCS.NS": _daily_to("2026-11-06", base=300)}))
    frames, errors = loaders.load_daily_many(NSE, ["INFY", "TCS"], now=now)
    assert errors == {} and calls == [["TCS.NS"]]  # INFY came from load_daily's cache file


def test_just_after_close_cached_frame_with_todays_bar_is_downloaded(monkeypatch, cache_dir):
    now = datetime(2026, 11, 9, 16, 0, tzinfo=TZ)  # close 15:30 + 30 min
    stale = _daily_to("2026-11-09", base=100)
    _record(monkeypatch, single(stale, "INFY.NS"))
    data_mod.fetch_nse("INFY", "2022-11-09", end="2026-11-10", exchange="NS")  # seeds the cache, last bar today
    fresh = stale.copy()
    fresh.iloc[-1, fresh.columns.get_loc("Close")] = 999.0
    calls = _record(monkeypatch, multi({"INFY.NS": fresh}))
    frames, errors = loaders.load_daily_many(NSE, ["INFY"], now=now)
    assert errors == {} and calls == [["INFY.NS"]]
    assert frames["INFY"]["Close"].iloc[-1] == 999.0


def test_cached_frame_with_todays_bar_in_session_is_served_from_cache(monkeypatch, cache_dir):
    now = datetime(2026, 11, 9, 11, 0, tzinfo=TZ)
    _record(monkeypatch, single(_daily_to("2026-11-09"), "INFY.NS"))
    data_mod.fetch_nse("INFY", "2022-11-09", end="2026-11-10", exchange="NS")
    calls = _record(monkeypatch, pd.DataFrame())
    frames, errors = loaders.load_daily_many(NSE, ["INFY"], now=now)
    assert errors == {} and calls == [] and frames["INFY"].index[-1].date() == date(2026, 11, 6)


def test_after_close_fallback_does_not_reread_the_stale_cache(monkeypatch, cache_dir):
    now = datetime(2026, 11, 9, 16, 0, tzinfo=TZ)
    _record(monkeypatch, single(_daily_to("2026-11-09"), "INFY.NS"))
    data_mod.fetch_nse("INFY", "2022-11-09", end="2026-11-10", exchange="NS")
    _record(monkeypatch, pd.DataFrame())  # batch empty -> fallback
    seen = []
    monkeypatch.setattr(data_mod, "fetch_nse",
                        lambda sym, start, end=None, exchange="NS", use_cache=True, adjusted=True: seen.append(use_cache)
                        or _daily_to("2026-11-09"))
    frames, errors = loaders.load_daily_many(NSE, ["INFY"], now=now)
    assert errors == {} and seen == [False]


def test_one_bad_symbol_does_not_affect_others(monkeypatch, cache_dir):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
    only_today = _daily_to("2026-11-09", n=1)  # its only bar is today's partial bar -> no completed bars
    batch = multi({"INFY.NS": _daily_to("2026-11-06"), "NEW.NS": only_today})
    # the batch call gets `batch`; GONE's per-symbol retry (fix round 1) gets nothing
    _record(monkeypatch, lambda t: batch.copy() if len(t) > 1 else pd.DataFrame())
    frames, errors = loaders.load_daily_many(NSE, ["INFY", "NEW", "GONE", "../x"], now=now)
    assert set(frames) == {"INFY"}
    assert set(errors) == {"NEW", "GONE", "../x"} and "no completed bars" in errors["NEW"]


def test_batch_failure_falls_back_per_symbol(monkeypatch, cache_dir):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)

    def boom(*a, **k):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(yf, "download", boom)
    monkeypatch.setattr(data_mod, "fetch_nse", lambda sym, *a, **k: _daily_to("2026-11-06"))
    frames, errors = loaders.load_daily_many(NSE, ["INFY", "TCS"], now=now)
    assert errors == {} and set(frames) == {"INFY", "TCS"}


def test_keys_are_the_symbols_as_given(monkeypatch, cache_dir):
    now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
    calls = _record(monkeypatch, multi({"INFY.NS": _daily_to("2026-11-06")}))
    frames, errors = loaders.load_daily_many(NSE, ["infy", "INFY"], now=now)
    assert errors == {} and set(frames) == {"infy", "INFY"} and calls == [["INFY.NS"]]


def test_empty_symbol_list(monkeypatch):
    def never(*a, **k):
        raise AssertionError("no download for an empty list")
    monkeypatch.setattr(yf, "download", never)
    assert loaders.load_daily_many(NSE, []) == ({}, {})


def test_load_frames_uses_batch_and_maps_failures_to_none(monkeypatch):
    from tests.desk.test_cli_loaders import series
    got = []

    def many(profile, symbols, years=4, *, now=None):
        got.append(list(symbols))
        return {"OK": series()}, {"BAD": "no data", "WORSE": "boom"}

    monkeypatch.setattr(loaders, "load_daily_many", many)

    def never(*a, **k):
        raise AssertionError("load_frames must not load per symbol")
    monkeypatch.setattr(loaders, "load_daily", never)
    warns = []
    out = session.load_frames(NSE, ["OK", "BAD", "WORSE"], warns.append)
    assert got == [["OK", "BAD", "WORSE"]]
    assert out["OK"] is not None and out["BAD"] is None and out["WORSE"] is None
    assert list(out) == ["OK", "BAD", "WORSE"]
    assert warns == ["no data for BAD: no data", "no data for WORSE: boom"]


# ---- fix round 1 ----
def test_load_frames_through_the_real_batch_path(monkeypatch, cache_dir):
    """No via_load_daily stub: load_frames -> load_daily_many -> fetch_many_daily -> (fake) yf.download."""
    real_now = loaders._market_now
    monkeypatch.setattr(loaders, "_market_now", lambda p, now=None: real_now(p, now or datetime(2026, 11, 9, 10, 0, tzinfo=TZ)))
    ok = _daily_to("2026-11-06")

    def download(*args, **kwargs):
        t = kwargs.get("tickers", args[0] if args else None)
        if isinstance(t, (list, tuple)):  # the batch call: only OK has data
            return multi({"OK.NS": ok})
        return pd.DataFrame()  # per-symbol retry of BAD: nothing
    monkeypatch.setattr(yf, "download", download)
    warns = []
    out = session.load_frames(NSE, ["OK", "BAD"], warns.append)
    assert list(out) == ["OK", "BAD"] and out["BAD"] is None
    assert out["OK"].index[-1].date() == date(2026, 11, 6) and len(out["OK"]) > 0
    assert len(warns) == 1 and warns[0].startswith("no data for BAD: BAD: ")


def test_unreadable_cache_file_does_not_fail_the_batch(monkeypatch, cache_dir):
    """probe_poison: a cache file that loads but has a non-date index affects only its own symbol."""
    now = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)
    bad = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10]},
                       index=["not-a-date"])
    data_mod.CACHE_DIR  # created by _cache_path
    bad.to_parquet(data_mod._cache_path("BAD", "NS", "2022-11-09", "2026-11-10"))
    _record(monkeypatch, multi({"GOOD.NS": _daily_to("2026-11-06")}))
    frames, errors = loaders.load_daily_many(NSE, ["GOOD", "BAD"], now=now)
    assert "GOOD" in frames and len(frames["GOOD"]) > 0
    assert ("BAD" in frames) != ("BAD" in errors)  # BAD downloaded or errors on its own
