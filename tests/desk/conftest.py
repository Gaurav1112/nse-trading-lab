import pytest
from nse_backtest.desk import loaders

_REAL_LIVE_QUOTE = getattr(loaders, "live_quote", None)


@pytest.fixture
def real_live_quote():
    """The unstubbed loaders.live_quote (tests patch yfinance underneath it)."""
    return _REAL_LIVE_QUOTE


@pytest.fixture(autouse=True)
def _no_live_quote(monkeypatch):
    """No network: the live quote is unavailable unless a test stubs one in."""
    if hasattr(loaders, "live_quote"):
        monkeypatch.setattr(loaders, "live_quote", lambda p, s: None)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Desk tests never touch the network: stub the scorer's earnings lookup and fail any socket connect."""
    import socket
    from nse_backtest.features import earnings_guard
    monkeypatch.setattr(earnings_guard, "_next_earnings_date", lambda symbol: None)

    def _blocked(*args, **kwargs):
        raise OSError("network access is blocked in desk tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


@pytest.fixture
def offline_desk(tmp_path, monkeypatch):
    """Isolated data dir and no network: daily bars, regime, earnings and the ban list are stubbed."""
    from tests.desk.test_cli_loaders import series
    from tests.desk.test_loaders_many import via_load_daily
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_daily_many", via_load_daily)  # S4: load_frames batches
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
