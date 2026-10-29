"""S4: fetch_many_daily — one yfinance call for the uncached symbols, identical frames to fetch_nse."""
import os

import numpy as np
import pandas as pd
import pytest
import yfinance as yf

from nse_backtest import data as data_mod

START, END = "2026-01-01", "2026-03-01"


def _bars(n=40, end="2026-02-27", base=100.0):
    idx = pd.bdate_range(end=end, periods=n, name="Date")
    close = base + np.arange(n, dtype=float)
    return pd.DataFrame({"Open": close - 0.5, "High": close + 1.0, "Low": close - 1.0, "Close": close,
                         "Volume": np.arange(n, dtype="int64") * 1000 + 5000}, index=idx)


def multi(frames: dict) -> pd.DataFrame:
    """yfinance.download(tickers=[...], group_by='ticker') shape: (Ticker, Price) columns, union index."""
    return pd.concat(list(frames.values()), axis=1, sort=True, keys=list(frames.keys()), names=["Ticker", "Price"])


def single(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance.download(ticker) shape (group_by='column'): (Price, Ticker) columns."""
    out = df.copy()
    out.columns = pd.MultiIndex.from_tuples([(c, ticker) for c in df.columns], names=["Price", "Ticker"])
    return out


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(data_mod, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(data_mod.time, "sleep", lambda s: None)


def _record(monkeypatch, response):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs, data_mod._YF_LOCK.locked()))
        if isinstance(response, Exception):
            raise response
        return response(args, kwargs) if callable(response) else response.copy()

    monkeypatch.setattr(yf, "download", fake)
    return calls


def _tickers(call):
    args, kwargs, _ = call
    t = kwargs.get("tickers", args[0] if args else None)
    return [t] if isinstance(t, str) else list(t)


def test_one_call_for_uncached_symbols_cached_one_not_downloaded(monkeypatch):
    a, b, c = _bars(base=100), _bars(base=200), _bars(base=300)
    data_mod._atomic_write_parquet(a, data_mod._cache_path("AAA", "NS", START, END))
    calls = _record(monkeypatch, multi({"BBB.NS": b, "CCC.NS": c}))
    frames, errors = data_mod.fetch_many_daily(["AAA", "BBB", "CCC"], START, END, "NS")
    assert errors == {}
    assert len(calls) == 1
    assert sorted(_tickers(calls[0])) == ["BBB.NS", "CCC.NS"]
    kw = calls[0][1]
    assert kw["group_by"] == "ticker" and kw["auto_adjust"] is True and kw["threads"] is False
    assert kw["progress"] is False and kw["start"] == START and kw["end"] == END
    assert calls[0][2] is True  # under _YF_LOCK
    pd.testing.assert_frame_equal(frames["AAA"], pd.read_parquet(data_mod._cache_path("AAA", "NS", START, END)))
    for sym in ("BBB", "CCC"):
        path = data_mod._cache_path(sym, "NS", START, END)
        assert os.path.exists(path)
        pd.testing.assert_frame_equal(pd.read_parquet(path), frames[sym])


def test_frames_equal_fetch_nse_for_same_data(monkeypatch, tmp_path):
    b = _bars(n=40, end="2026-02-27", base=200)
    c = _bars(n=35, end="2026-02-20", base=300)  # different dates: the union index pads B and C with NaN rows
    c.iloc[3, c.columns.get_loc("High")] = 1.0  # malformed bar, dropped by both paths
    _record(monkeypatch, multi({"BBB.NS": b, "CCC.NS": c}))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS", use_cache=False)
    assert errors == {}
    for sym, df in (("BBB", b), ("CCC", c)):
        _record(monkeypatch, single(df, f"{sym}.NS"))
        expected = data_mod.fetch_nse(sym, START, END, "NS", use_cache=False)
        pd.testing.assert_frame_equal(frames[sym], expected)


def test_single_uncached_symbol_without_ticker_level(monkeypatch):
    b = _bars(base=200)
    calls = _record(monkeypatch, b)  # flat columns: no ticker level at all
    frames, errors = data_mod.fetch_many_daily(["BBB"], START, END, "NS")
    assert errors == {} and len(calls) == 1
    assert list(frames["BBB"]["Close"]) == list(b["Close"])


def test_single_uncached_symbol_with_ticker_level(monkeypatch):
    b = _bars(base=200)
    _record(monkeypatch, multi({"BBB.NS": b}))
    frames, errors = data_mod.fetch_many_daily(["BBB"], START, END, "NS")
    assert errors == {} and list(frames["BBB"]["Close"]) == list(b["Close"])


def test_ticker_missing_from_response_is_an_error_entry(monkeypatch):
    b = _bars(base=200)
    _record(monkeypatch, multi({"BBB.NS": b}))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert "BBB" in frames and "CCC" not in frames
    assert errors["CCC"].startswith("CCC: ")
    assert not os.path.exists(data_mod._cache_path("CCC", "NS", START, END))


def test_failed_ticker_all_nan_is_an_error_entry(monkeypatch):
    b = _bars(base=200)
    failed = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"], dtype=float)
    _record(monkeypatch, multi({"BBB.NS": b, "CCC.NS": failed}))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert "BBB" in frames and errors.keys() == {"CCC"}


def test_batch_raises_falls_back_to_fetch_nse_per_symbol(monkeypatch):
    _record(monkeypatch, RuntimeError("rate limited"))
    seen = []

    def per_symbol(sym, start, end=None, exchange="NS", use_cache=True):
        seen.append((sym, start, end, exchange, use_cache))
        if sym == "CCC":
            raise ValueError("No data found for CCC.NS")
        return _bars(base=200)

    monkeypatch.setattr(data_mod, "fetch_nse", per_symbol)
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert [s[0] for s in seen] == ["BBB", "CCC"] and all(s[1:4] == (START, END, "NS") for s in seen)
    assert "BBB" in frames and "CCC" in errors and "No data found" in errors["CCC"]


def test_entirely_empty_batch_falls_back(monkeypatch):
    calls = _record(monkeypatch, pd.DataFrame())
    monkeypatch.setattr(data_mod, "fetch_nse", lambda sym, *a, **k: _bars(base=200))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert len(calls) == 3  # the _yf_download retry policy: empty is retried
    assert errors == {} and set(frames) == {"BBB", "CCC"}


def test_invalid_symbol_is_an_error_entry_and_others_fetched(monkeypatch):
    b = _bars(base=200)
    calls = _record(monkeypatch, multi({"BBB.NS": b}))
    frames, errors = data_mod.fetch_many_daily(["../etc", "BBB"], START, END, "NS")
    assert "BBB" in frames and "../etc" in errors and len(calls) == 1
    assert _tickers(calls[0]) == ["BBB.NS"]


def test_empty_list_makes_no_call(monkeypatch):
    calls = _record(monkeypatch, pd.DataFrame())
    assert data_mod.fetch_many_daily([], START, END, "NS") == ({}, {})
    assert calls == []


def test_refetch_if_sends_a_cached_symbol_to_the_download(monkeypatch):
    a_old, a_new = _bars(base=100), _bars(base=150)
    data_mod._atomic_write_parquet(a_old, data_mod._cache_path("AAA", "NS", START, END))
    calls = _record(monkeypatch, multi({"AAA.NS": a_new}))
    frames, errors = data_mod.fetch_many_daily(["AAA"], START, END, "NS", refetch_if=lambda sym, df: sym == "AAA")
    assert len(calls) == 1 and _tickers(calls[0]) == ["AAA.NS"]
    assert frames["AAA"]["Close"].iloc[-1] == a_new["Close"].iloc[-1]


def test_cache_write_failure_is_warning_only(monkeypatch):
    b = _bars(base=200)
    _record(monkeypatch, multi({"BBB.NS": b}))

    def boom(df, path):
        raise OSError("disk full")
    monkeypatch.setattr(data_mod, "_atomic_write_parquet", boom)
    frames, errors = data_mod.fetch_many_daily(["BBB"], START, END, "NS")
    assert errors == {} and "BBB" in frames


def test_fetch_nse_retry_still_uses_the_shared_policy(monkeypatch):
    """Refactor guard: fetch_nse retries an empty response 3 times then raises 'No data found'."""
    calls = _record(monkeypatch, pd.DataFrame())
    with pytest.raises(ValueError, match="No data found"):
        data_mod.fetch_nse("BBB", START, END, "NS", use_cache=False)
    assert len(calls) == 3
