import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_inject_css_has_key_selectors():
    from components.theme import inject_css, BG, GREEN, RED, BLUE
    css = inject_css()
    assert "stMetric" in css
    assert "verdict-go" in css
    assert "JetBrains" in css


def test_color_constants_are_hex():
    from components.theme import BG, GREEN, RED, BLUE, AMBER
    for c in [BG, GREEN, RED, BLUE, AMBER]:
        assert c.startswith("#") and len(c) == 7


# ── state tests ──
import types, unittest.mock as mock


def _session(initial=None):
    session = initial or {}
    fake_st = types.SimpleNamespace(session_state=session)
    return mock.patch.dict('sys.modules', {'streamlit': fake_st}), fake_st


def test_init_session_sets_defaults():
    patch, fake_st = _session()
    with patch:
        import importlib, components.state as s
        importlib.reload(s)
        s.init_session()
        assert s.get_capital() == 100_000.0
        assert s.get_risk_pct() == 2.0
        assert s.get_watchlist() == ["RELIANCE","TCS","COALINDIA","NTPC","SBIN"]


def test_set_capital():
    patch, fake_st = _session()
    with patch:
        import importlib, components.state as s
        importlib.reload(s)
        s.init_session(); s.set_capital(75_000.0)
        assert s.get_capital() == 75_000.0


def test_add_remove_position():
    patch, fake_st = _session()
    with patch:
        import importlib, components.state as s
        importlib.reload(s)
        s.init_session()
        s.add_position({"symbol":"TCS","qty":10,"buy_price":3500.0,
                        "stop_loss":3200.0,"target":3800.0,"date":"2026-05-28","invested":35000.0})
        assert len(s.get_positions()) == 1
        s.remove_position(0)
        assert len(s.get_positions()) == 0


def test_watchlist_is_independent_copy():
    patch, fake_st = _session()
    with patch:
        import importlib, components.state as s
        importlib.reload(s)
        s.init_session()
        w = s.get_watchlist(); w.append("ZOMATO")
        assert "ZOMATO" not in s.get_watchlist()
