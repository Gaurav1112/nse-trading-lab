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
