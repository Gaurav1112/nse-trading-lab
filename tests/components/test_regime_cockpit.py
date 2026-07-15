from components.regime_cockpit import build_cockpit_html


def test_cockpit_html_contains_all_four_cells():
    html = build_cockpit_html(regime="MIXED", nifty_close=21400.0, vix=14.2, breadth_pct=62.0, ema_slope=0.15)
    assert "TAPE" in html and "MIXED" in html
    assert "VIX" in html and "14.2" in html
    assert "BREADTH" in html and "62" in html
    assert "200EMA" in html
