import pytest
from nse_backtest.desk import loaders


@pytest.fixture
def offline_desk(tmp_path, monkeypatch):
    """Isolated data dir and no network: daily bars, regime, earnings and the ban list are stubbed."""
    from tests.desk.test_cli_loaders import series
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
