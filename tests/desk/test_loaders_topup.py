"""H1: a start/end daily download that lags the latest completed session is topped up by ONE period='5d' call.

Observed live 2026-10-30 00:02 IST: yf.download(start, end) for RELIANCE.NS ended 2026-10-28 while
yf.download(period='5d') already had 2026-10-29, so the freshness check BLOCKed every symbol. No network here.
"""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from nse_backtest import data as data_mod
from nse_backtest.desk import calendars as cal, checks, loaders
from nse_backtest.desk.markets import NSE
from nse_backtest.desk.models import Status
from tests.test_data_batch import multi, single

TZ = loaders.ZoneInfo(NSE.tz)
NIGHT = datetime(2026, 10, 30, 0, 2, tzinfo=TZ)   # the bug: latest completed session is Thu 2026-10-29
D = date(2026, 10, 29)
IN_SESSION = datetime(2026, 10, 30, 11, 0, tzinfo=TZ)  # Fri session open: latest completed is still 10-29


def _bars(last: str, n=60, base=100.0):
    idx = pd.bdate_range(end=last, periods=n, name="Date")
    close = base + np.arange(n, dtype=float)
    return pd.DataFrame({"Open": close - 0.5, "High": close + 1.0, "Low": close - 1.0, "Close": close,
                         "Volume": np.arange(n, dtype="int64") * 100 + 1000}, index=idx)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(data_mod.time, "sleep", lambda s: None)


def _fake(monkeypatch, ranged, period):
    """ranged/period: frame, Exception, or callable(tickers) -> frame, for start/end and period calls."""
    calls = []

    def fake(*args, **kwargs):
        t = kwargs.get("tickers", args[0] if args else None)
        tickers = [t] if isinstance(t, str) else sorted(t)
        kind = "period" if "period" in kwargs else "range"
        calls.append((kind, tickers, kwargs, data_mod._YF_LOCK.locked()))
        resp = period if kind == "period" else ranged
        if isinstance(resp, Exception):
            raise resp
        return resp(tickers) if callable(resp) else resp.copy()

    monkeypatch.setattr(yf, "download", fake)
    return calls


def _td(df):
    """What the desk keeps: bars on NSE trading days only (holidays inside the bdate_range are dropped)."""
    return df[[cal.is_trading_day(NSE, d) for d in df.index.date]]


def _fresh(df, now):
    return checks._swing_freshness(NSE, pd.Timestamp(df.index[-1]).date(), now)


# ------------------------------------------------------------------ single path (load_daily -> fetch_nse)

def test_lagging_range_is_topped_up_to_latest_completed_session(monkeypatch):
    full = _bars("2026-10-29")
    calls = _fake(monkeypatch, single(full.iloc[:-1], "RELIANCE.NS"), single(full.iloc[-5:], "RELIANCE.NS"))
    df = loaders.load_daily(NSE, "RELIANCE", now=NIGHT)
    assert df.index[-1].date() == D
    assert [c[0] for c in calls] == ["range", "period"]
    kw = calls[1][2]
    assert kw["period"] == "5d" and kw["interval"] == "1d" and kw["auto_adjust"] is True
    assert kw["progress"] is False and kw["threads"] is False and calls[1][3] is True  # under _YF_LOCK
    pd.testing.assert_frame_equal(df, _td(full), check_freq=False, check_names=False, check_like=True)
    assert _fresh(df, NIGHT).status == Status.PASS


def test_unadjusted_top_up_stays_unadjusted(monkeypatch):
    full = _bars("2026-10-29")
    calls = _fake(monkeypatch, single(full.iloc[:-1], "RELIANCE.NS"), single(full.iloc[-5:], "RELIANCE.NS"))
    loaders.load_daily(NSE, "RELIANCE", now=NIGHT, adjusted=False)
    assert calls[1][2]["auto_adjust"] is False


def test_top_up_with_nothing_newer_keeps_frame_and_freshness_blocks(monkeypatch):
    lag = _bars("2026-10-28")
    _fake(monkeypatch, single(lag, "RELIANCE.NS"), single(lag.iloc[-5:], "RELIANCE.NS"))
    df = loaders.load_daily(NSE, "RELIANCE", now=NIGHT)
    pd.testing.assert_frame_equal(df, _td(lag), check_freq=False, check_names=False, check_like=True)
    ck = _fresh(df, NIGHT)
    assert ck.status == Status.BLOCK and "misses 1 trading session" in ck.reason


@pytest.mark.parametrize("period", [RuntimeError("429 rate limited"), pd.DataFrame()])
def test_top_up_failure_or_empty_keeps_frame(monkeypatch, period):
    lag = _bars("2026-10-28")
    calls = _fake(monkeypatch, single(lag, "RELIANCE.NS"), period)
    df = loaders.load_daily(NSE, "RELIANCE", now=NIGHT)
    pd.testing.assert_frame_equal(df, _td(lag), check_freq=False, check_names=False, check_like=True)
    assert [c[0] for c in calls] == ["range", "period"]  # exactly ONE top-up attempt
    assert _fresh(df, NIGHT).status == Status.BLOCK


def test_top_up_in_session_still_drops_todays_partial_bar(monkeypatch):
    full = _bars("2026-10-30")  # the period response carries today's unfinished bar
    _fake(monkeypatch, single(full.iloc[:-2], "RELIANCE.NS"), single(full.iloc[-5:], "RELIANCE.NS"))
    df = loaders.load_daily(NSE, "RELIANCE", now=IN_SESSION)
    assert df.index[-1].date() == D
    assert date(2026, 10, 30) not in set(df.index.date)


def test_top_up_never_overwrites_an_existing_bar(monkeypatch):
    full = _bars("2026-10-29")
    topup = full.iloc[-5:].copy()
    topup.loc[pd.Timestamp("2026-10-28"), "Close"] = 1.0  # a revised 10-28 must not replace ours
    topup.loc[pd.Timestamp("2026-10-28"), "Low"] = 0.5
    _fake(monkeypatch, single(full.iloc[:-1], "RELIANCE.NS"), single(topup, "RELIANCE.NS"))
    df = loaders.load_daily(NSE, "RELIANCE", now=NIGHT)
    assert df.loc[pd.Timestamp("2026-10-28"), "Close"] == full.loc[pd.Timestamp("2026-10-28"), "Close"]
    assert not df.index.duplicated().any() and df.index.is_monotonic_increasing
    assert df.index[-1].date() == D


def test_current_range_makes_no_top_up_call(monkeypatch):
    full = _bars("2026-10-29")
    calls = _fake(monkeypatch, single(full, "RELIANCE.NS"), RuntimeError("must not be called"))
    loaders.load_daily(NSE, "RELIANCE", now=NIGHT)
    assert [c[0] for c in calls] == ["range"]


def test_topped_up_frame_is_cached(monkeypatch):
    full = _bars("2026-10-29")
    _fake(monkeypatch, single(full.iloc[:-1], "RELIANCE.NS"), single(full.iloc[-5:], "RELIANCE.NS"))
    loaders.load_daily(NSE, "RELIANCE", now=NIGHT)
    calls = _fake(monkeypatch, RuntimeError("no network"), RuntimeError("no network"))
    df = loaders.load_daily(NSE, "RELIANCE", now=NIGHT)
    assert calls == [] and df.index[-1].date() == D


def test_fetch_nse_without_latest_completed_is_unchanged(monkeypatch):
    lag = _bars("2026-10-28")
    calls = _fake(monkeypatch, single(lag, "RELIANCE.NS"), RuntimeError("must not be called"))
    data_mod.fetch_nse("RELIANCE", "2022-10-30", end="2026-10-31", use_cache=False)
    assert [c[0] for c in calls] == ["range"]


def test_regime_index_is_topped_up_and_stays_fail_closed(monkeypatch):
    full = _bars("2026-10-29")
    _fake(monkeypatch, single(full.iloc[:-1], "^NSEI"), single(full.iloc[-5:], "^NSEI"))
    assert loaders.load_regime(NSE, now=NIGHT).index[-1].date() == D
    data_mod.CACHE_DIR += "_2"
    _fake(monkeypatch, single(full.iloc[:-1], "^NSEI"), RuntimeError("down"))
    assert loaders.load_regime(NSE, now=NIGHT).index[-1].date() == date(2026, 10, 28)


@pytest.mark.parametrize("now, expected", [
    (NIGHT, D),
    (IN_SESSION, D),
    (datetime(2026, 10, 30, 15, 30, tzinfo=TZ), date(2026, 10, 30)),
    (datetime(2026, 10, 25, 12, 0, tzinfo=TZ), date(2026, 10, 23)),  # Sunday -> Friday
])
def test_latest_completed_session(now, expected):
    assert loaders.latest_completed_session(NSE, now) == expected


# ------------------------------------------------------------------ batch path (load_daily_many -> fetch_many_daily)

def test_batch_tops_up_all_lagging_tickers_in_one_call(monkeypatch):
    full = {"INFY": _bars("2026-10-29", base=100), "TCS": _bars("2026-10-29", base=300),
            "SBIN": _bars("2026-10-29", base=500)}
    ranged = multi({"INFY.NS": full["INFY"].iloc[:-1], "TCS.NS": full["TCS"].iloc[:-1], "SBIN.NS": full["SBIN"]})
    period = multi({f"{s}.NS": df.iloc[-5:] for s, df in full.items() if s != "SBIN"})
    calls = _fake(monkeypatch, ranged, period)
    frames, errors = loaders.load_daily_many(NSE, ["INFY", "TCS", "SBIN"], now=NIGHT)
    assert errors == {}
    assert [(c[0], c[1]) for c in calls] == [("range", ["INFY.NS", "SBIN.NS", "TCS.NS"]),
                                            ("period", ["INFY.NS", "TCS.NS"])]
    kw = calls[1][2]
    assert kw["period"] == "5d" and kw["interval"] == "1d" and kw["group_by"] == "ticker" and calls[1][3] is True
    for sym in full:
        assert frames[sym].index[-1].date() == D
        pd.testing.assert_frame_equal(frames[sym], _td(full[sym]), check_freq=False, check_names=False, check_like=True)
    # cached: the same frames come back with no network
    again = _fake(monkeypatch, RuntimeError("no network"), RuntimeError("no network"))
    frames2, errors2 = loaders.load_daily_many(NSE, ["INFY", "TCS", "SBIN"], now=NIGHT)
    assert errors2 == {} and again == []
    for sym in full:
        assert frames2[sym].index[-1].date() == D


def test_batch_frames_equal_load_daily_after_top_up(monkeypatch, tmp_path):
    full = {"INFY": _bars("2026-10-29", base=100), "TCS": _bars("2026-10-29", n=50, base=300)}
    expected = {}
    for sym, df in full.items():
        monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path / f"single_{sym}"))
        _fake(monkeypatch, single(df.iloc[:-1], f"{sym}.NS"), single(df.iloc[-5:], f"{sym}.NS"))
        expected[sym] = loaders.load_daily(NSE, sym, now=NIGHT)
    monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path / "batch"))
    _fake(monkeypatch, multi({f"{s}.NS": df.iloc[:-1] for s, df in full.items()}),
          multi({f"{s}.NS": df.iloc[-5:] for s, df in full.items()}))
    frames, errors = loaders.load_daily_many(NSE, list(full), now=NIGHT)
    assert errors == {}
    for sym in full:
        pd.testing.assert_frame_equal(frames[sym], expected[sym])


@pytest.mark.parametrize("period", [RuntimeError("429"), pd.DataFrame()])
def test_batch_top_up_failure_keeps_frames(monkeypatch, period):
    lag = {"INFY": _bars("2026-10-28", base=100), "TCS": _bars("2026-10-28", base=300)}
    calls = _fake(monkeypatch, multi({f"{s}.NS": df for s, df in lag.items()}), period)
    frames, errors = loaders.load_daily_many(NSE, list(lag), now=NIGHT)
    assert errors == {} and [c[0] for c in calls] == ["range", "period"]
    for sym, df in lag.items():
        pd.testing.assert_frame_equal(frames[sym], _td(df), check_freq=False, check_names=False, check_like=True)
        assert _fresh(frames[sym], NIGHT).status == Status.BLOCK


def test_batch_top_up_partial_answer_and_no_overwrite(monkeypatch):
    full = {"INFY": _bars("2026-10-29", base=100), "TCS": _bars("2026-10-29", base=300)}
    infy_top = full["INFY"].iloc[-5:].copy()
    infy_top.loc[pd.Timestamp("2026-10-28"), ["Close", "Low"]] = [1.0, 0.5]
    period = multi({"INFY.NS": infy_top, "TCS.NS": full["TCS"].iloc[-5:-1]})  # TCS: nothing newer
    _fake(monkeypatch, multi({f"{s}.NS": df.iloc[:-1] for s, df in full.items()}), period)
    frames, errors = loaders.load_daily_many(NSE, list(full), now=NIGHT)
    assert errors == {}
    pd.testing.assert_frame_equal(frames["INFY"], _td(full["INFY"]), check_freq=False, check_names=False, check_like=True)  # 10-28 not overwritten
    assert frames["TCS"].index[-1].date() == date(2026, 10, 28)


def test_batch_top_up_in_session_drops_todays_partial_bar(monkeypatch):
    full = _bars("2026-10-30")
    _fake(monkeypatch, multi({"INFY.NS": full.iloc[:-2]}), multi({"INFY.NS": full.iloc[-5:]}))
    frames, errors = loaders.load_daily_many(NSE, ["INFY"], now=IN_SESSION)
    assert errors == {} and frames["INFY"].index[-1].date() == D
