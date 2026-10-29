import numpy as np, pandas as pd
from streamlit.testing.v1 import AppTest


def series(n=400):
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize() - pd.Timedelta(days=1), periods=n)
    close = 100 * 1.004 ** np.arange(1, n + 1); prev = close / 1.004
    return pd.DataFrame({"Open": prev, "High": prev * 1.012, "Low": prev * 0.995, "Close": close, "Volume": 5e6}, index=idx)


def test_desk_page_renders_and_checks_a_trade(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from nse_backtest.desk import loaders
    monkeypatch.setattr(loaders, "load_daily", lambda p, s, years=4: series())
    monkeypatch.setattr(loaders, "load_regime", lambda p: series())
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, True))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Today's Plan", "Check a Trade", "My Positions", "Intraday"]
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    assert any(w in " ".join(m.value for m in at.markdown) for w in ("TAKE", "WAIT", "SKIP"))


def test_desk_page_survives_loader_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_LAB_DATA_DIR", str(tmp_path))
    from nse_backtest.desk import loaders

    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(loaders, "load_daily", boom)
    monkeypatch.setattr(loaders, "load_regime", boom)
    monkeypatch.setattr(loaders, "next_earnings", lambda p, s: (None, False))
    monkeypatch.setattr(loaders.nse_lists, "fetch_fno_ban",
                        lambda d, t, fetcher=None: loaders.nse_lists.BanList(frozenset(), "ok", None, ""))
    at = AppTest.from_file("pages/0_Desk.py", default_timeout=120).run()
    assert not at.exception
    assert any("HOSTILE" in w.value for w in at.warning)
    at.text_input(key="desk_symbol").set_value("ABC")
    at.button(key="desk_check").click().run()
    assert not at.exception
    assert any("network down" in e.value for e in at.error)
