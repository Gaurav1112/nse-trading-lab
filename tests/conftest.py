"""Shared pytest fixtures for the nse_backtest test suite."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def trending_ohlcv() -> pd.DataFrame:
    """Synthetic deterministic trending OHLCV (~400 business days)."""
    np.random.seed(42)
    n = 400
    dates = pd.bdate_range("2022-01-03", periods=n)
    trend = np.linspace(100.0, 250.0, n)
    noise = np.random.normal(0, 1.5, n).cumsum() * 0.25
    close = trend + noise
    high = close + np.random.uniform(0.5, 2.5, n)
    low = close - np.random.uniform(0.5, 2.5, n)
    opn = close + np.random.uniform(-1.0, 1.0, n)
    vol = np.random.randint(500_000, 2_000_000, n)
    return pd.DataFrame(
        {"Open": opn, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


@pytest.fixture
def flat_ohlcv() -> pd.DataFrame:
    """Sideways/choppy series used to stress NaN-safety and boundary cases."""
    np.random.seed(1)
    n = 250
    dates = pd.bdate_range("2023-01-02", periods=n)
    base = np.full(n, 200.0) + np.random.normal(0, 1.0, n)
    high = base + np.random.uniform(0.3, 1.0, n)
    low = base - np.random.uniform(0.3, 1.0, n)
    return pd.DataFrame(
        {"Open": base, "High": high, "Low": low, "Close": base,
         "Volume": np.random.randint(100_000, 500_000, n)},
        index=dates,
    )


def _network_disabled(*args, **kwargs):
    raise RuntimeError("network disabled in tests")


class _NoSleepTime:
    """``time`` for nse_backtest.data with a no-op sleep: a blocked download is retried with 1.5s/60s backoffs,
    which must not stall the suite. Everything else is the real ``time``."""
    def __init__(self):
        import time
        self._time = time

    def sleep(self, seconds):
        return None

    def __getattr__(self, name):
        return getattr(self._time, name)


@pytest.fixture(autouse=True)
def _no_network_anywhere(monkeypatch, tmp_path_factory):
    """No test reaches the network or the user's data dir. yfinance (>= 1.x) fetches through curl_cffi (libcurl),
    which bypasses Python sockets, so it is blocked at the library too. A test that needs data patches these itself
    (its own monkeypatch runs after this and wins)."""
    import socket
    import urllib.request
    monkeypatch.setattr(socket.socket, "connect", _network_disabled)
    monkeypatch.setattr(socket, "create_connection", _network_disabled)
    monkeypatch.setattr(urllib.request, "urlopen", _network_disabled)
    try:
        import yfinance
        monkeypatch.setattr(yfinance, "download", _network_disabled)
        monkeypatch.setattr(yfinance.Ticker, "history", _network_disabled)
    except ImportError:
        pass
    try:
        import curl_cffi.requests
        monkeypatch.setattr(curl_cffi.requests.Session, "request", _network_disabled)
    except (ImportError, AttributeError):
        pass
    try:
        from nse_backtest import data as data_mod
        monkeypatch.setattr(data_mod, "time", _NoSleepTime())
    except ImportError:
        pass
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path_factory.mktemp("nse_lab_data")))
