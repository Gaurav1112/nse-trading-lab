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
    """yfinance 1.4 download(tickers=[...], group_by='ticker') shape: (Ticker, Price) columns in history order
    (no sort), union index, index name None."""
    out = pd.concat(list(frames.values()), axis=1, sort=True, keys=list(frames.keys()), names=["Ticker", "Price"])
    out.index.name = None
    return out


def single(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance 1.4 download(ticker) shape (group_by='column'): (Price, Ticker) columns sorted by Price,
    index name None."""
    out = df.copy()
    out.columns = pd.MultiIndex.from_tuples([(c, ticker) for c in df.columns], names=["Price", "Ticker"])
    out = out.sort_index(axis=1, level=0)
    out.index.name = None
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
        pd.testing.assert_frame_equal(pd.read_parquet(path), frames[sym], check_freq=False)  # parquet drops freq


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


def _batch_only(response):
    """The batch call (a list of tickers) gets `response`; a per-symbol retry (one ticker string) gets nothing."""
    return lambda args, kwargs: response.copy() if not isinstance(kwargs.get("tickers", args[0] if args else None),
                                                                     str) else pd.DataFrame()


def test_ticker_missing_from_response_is_an_error_entry(monkeypatch):
    # fix round 1: a missing ticker is retried per symbol first; it is an error only when that fails too
    b = _bars(base=200)
    calls = _record(monkeypatch, _batch_only(multi({"BBB.NS": b})))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert "BBB" in frames and "CCC" not in frames
    assert errors["CCC"].startswith("CCC: ") and "per-symbol retry failed" in errors["CCC"]
    assert len(calls) == 1 + 3  # the batch, then fetch_nse's 3 attempts for CCC
    assert not os.path.exists(data_mod._cache_path("CCC", "NS", START, END))


def test_failed_ticker_all_nan_is_an_error_entry(monkeypatch):
    b = _bars(base=200)
    failed = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"], dtype=float)
    _record(monkeypatch, _batch_only(multi({"BBB.NS": b, "CCC.NS": failed})))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert "BBB" in frames and errors.keys() == {"CCC"}


def test_batch_raises_falls_back_to_fetch_nse_per_symbol(monkeypatch):
    _record(monkeypatch, RuntimeError("rate limited"))
    seen = []

    def per_symbol(sym, start, end=None, exchange="NS", use_cache=True, adjusted=True):
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


# ---- fix round 1: the REAL yf.download (yfinance.multi) driven by a fake Ticker.history, no network ----
TZ = "Asia/Kolkata"


def _hist(n, end, base):
    idx = pd.bdate_range(end=end, periods=n, tz=TZ, name="Date")
    c = base + np.arange(n, dtype=float)
    return pd.DataFrame({"Open": c - 0.5, "High": c + 1.0, "Low": c - 1.0, "Close": c,
                         "Volume": (np.arange(n) * 100 + 1000).astype("int64")}, index=idx)


HIST = {"AAA.NS": _hist(40, "2026-02-27", 100), "BBB.NS": _hist(30, "2026-02-20", 200)}


@pytest.fixture
def real_yf(monkeypatch):
    """Real yf.download; Ticker.history serves HIST. `fail` maps ticker -> number of failing calls (-1 = always)."""
    import socket
    def blocked(*a, **k):
        raise OSError("network blocked in this test")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    state = {"fail": {}, "calls": []}

    def history(self, *a, **k):
        state["calls"].append(self.ticker)
        left = state["fail"].get(self.ticker, 0)
        if left:
            state["fail"][self.ticker] = left - 1 if left > 0 else left
            raise RuntimeError("Too Many Requests. Rate limited.")
        return HIST[self.ticker].copy()

    monkeypatch.setattr(yf.Ticker, "history", history)
    return state


def test_real_yf_batch_frames_equal_fetch_nse(real_yf):
    frames, errors = data_mod.fetch_many_daily(["AAA", "BBB"], START, END, "NS", use_cache=False)
    assert errors == {}
    for sym in ("AAA", "BBB"):
        pd.testing.assert_frame_equal(frames[sym], data_mod.fetch_nse(sym, START, END, "NS", use_cache=False))


def test_real_yf_ticker_failing_once_in_batch_is_retried_per_symbol(real_yf):
    real_yf["fail"]["BBB.NS"] = 1  # fails inside the batch, works on the per-symbol retry
    frames, errors = data_mod.fetch_many_daily(["AAA", "BBB"], START, END, "NS", use_cache=False)
    assert errors == {} and set(frames) == {"AAA", "BBB"}
    assert real_yf["calls"].count("BBB.NS") == 2 and real_yf["calls"].count("AAA.NS") == 1
    pd.testing.assert_frame_equal(frames["BBB"], data_mod.fetch_nse("BBB", START, END, "NS", use_cache=False))


def test_real_yf_ticker_failing_always_is_a_truthful_error(real_yf):
    real_yf["fail"]["BBB.NS"] = -1
    frames, errors = data_mod.fetch_many_daily(["AAA", "BBB"], START, END, "NS", use_cache=False)
    assert set(frames) == {"AAA"} and set(errors) == {"BBB"}
    msg = errors["BBB"]
    assert msg.startswith("BBB: ") and "batch download" in msg and "per-symbol retry" in msg
    assert not msg.startswith("BBB: No data found")  # not the bare 'check the symbol' text


def test_malformed_bars_in_batch_stay_an_error_without_retry(monkeypatch):
    b = _bars(base=200)
    c = _bars(base=300).assign(High=1.0)  # every bar High < Low: real bars, all malformed
    calls = _record(monkeypatch, multi({"BBB.NS": b, "CCC.NS": c}))
    monkeypatch.setattr(data_mod, "fetch_nse", lambda *a, **k: pytest.fail("no per-symbol retry for bad bars"))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert "BBB" in frames and set(errors) == {"CCC"} and len(calls) == 1


def test_missing_ticker_is_retried_per_symbol(monkeypatch):
    b, c = _bars(base=200), _bars(base=300)
    _record(monkeypatch, multi({"BBB.NS": b}))
    seen = []
    monkeypatch.setattr(data_mod, "fetch_nse",
                        lambda sym, start, end=None, exchange="NS", use_cache=True, adjusted=True: seen.append((sym, use_cache)) or c)
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS")
    assert errors == {} and seen == [("CCC", True)] and frames["CCC"] is c


def test_unreadable_cache_only_affects_its_symbol(monkeypatch):
    bad = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5], "Close": [1.5], "Volume": [10]},
                       index=["not-a-date"])
    bad.to_parquet(data_mod._cache_path("BAD", "NS", START, END))
    calls = _record(monkeypatch, multi({"GOOD.NS": _bars(base=200), "BAD.NS": _bars(base=50)}))

    def refetch_if(sym, df):
        return pd.Timestamp(df.index[-1]).year == 2099  # raises on the non-date index
    frames, errors = data_mod.fetch_many_daily(["GOOD", "BAD"], START, END, "NS", refetch_if=refetch_if)
    assert errors == {} and set(frames) == {"GOOD", "BAD"}
    assert sorted(t for t in calls[0][1]["tickers"]) == ["BAD.NS", "GOOD.NS"]
    assert frames["BAD"]["Close"].iloc[0] == 50.0  # downloaded, the bad cache file ignored


# ---- review round R1: unadjusted daily bars for the scoreboard (adjusted=False)

def test_adjusted_true_keeps_the_existing_cache_key():
    import hashlib
    raw = f"BBB|NS|{START}|{END}"
    old = f"BBB_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}.parquet"
    assert os.path.basename(data_mod._cache_path("BBB", "NS", START, END)) == old
    assert data_mod._cache_path("BBB", "NS", START, END, adjusted=True) == data_mod._cache_path("BBB", "NS", START, END)
    assert data_mod._cache_path("BBB", "NS", START, END, adjusted=False) != data_mod._cache_path("BBB", "NS", START, END)


def test_fetch_nse_unadjusted_uses_its_own_cache_and_auto_adjust_false(monkeypatch):
    b = _bars(base=200)
    calls = _record(monkeypatch, single(b, "BBB.NS"))
    data_mod.fetch_nse("BBB", START, END, "NS", adjusted=False)
    assert calls[-1][1]["auto_adjust"] is False
    assert os.path.exists(data_mod._cache_path("BBB", "NS", START, END, adjusted=False))
    assert not os.path.exists(data_mod._cache_path("BBB", "NS", START, END))
    data_mod.fetch_nse("BBB", START, END, "NS")
    assert calls[-1][1]["auto_adjust"] is True and len(calls) == 2  # an adjusted read never serves raw bars


def test_fetch_many_daily_unadjusted(monkeypatch):
    b, c = _bars(base=200), _bars(base=300)
    calls = _record(monkeypatch, multi({"BBB.NS": b, "CCC.NS": c}))
    frames, errors = data_mod.fetch_many_daily(["BBB", "CCC"], START, END, "NS", adjusted=False)
    assert errors == {} and calls[0][1]["auto_adjust"] is False
    assert os.path.exists(data_mod._cache_path("CCC", "NS", START, END, adjusted=False))
    assert not os.path.exists(data_mod._cache_path("CCC", "NS", START, END))


def test_fetch_many_daily_unadjusted_retry_stays_unadjusted(monkeypatch):
    _record(monkeypatch, RuntimeError("rate limited"))
    seen = []
    monkeypatch.setattr(data_mod, "fetch_nse", lambda sym, start, end=None, exchange="NS", use_cache=True,
                        adjusted=True: seen.append(adjusted) or _bars(base=200))
    data_mod.fetch_many_daily(["BBB"], START, END, "NS", adjusted=False)
    assert seen == [False]
