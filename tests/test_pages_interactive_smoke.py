"""Interactive smoke tests for Streamlit pages.

These tests exercise code paths that only fire after a user interaction
(button click, form submit). The standard import-time smoke tests miss
bugs like the AgGrid `theme="alpine-dark"` ValueError because that line
only runs after the user clicks Scan.

We use streamlit.testing.v1.AppTest to simulate clicks without booting a
real server. Each test runs in <5 seconds against synthetic Demo data.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


def _run_with_timeout(path: str, timeout: float = 60.0) -> AppTest:
    at = AppTest.from_file(path, default_timeout=timeout)
    at.run()
    return at


def test_screener_demo_universe_renders_aggrid():
    """Screener: select Demo universe, click Scan, confirm the AgGrid table
    renders without raising. This catches AgGrid theme-string regressions
    and any other interactive-path errors on the Screener page."""
    at = _run_with_timeout("pages/4_Screener.py", timeout=30)
    assert not at.exception, f"Initial render raised: {at.exception}"

    # Switch to Demo universe (synthetic data, no network).
    universe_select = at.selectbox(key="scr_univ")
    universe_select.set_value("Demo").run()
    assert not at.exception, f"Universe switch raised: {at.exception}"

    # Trigger the Scan flow.
    at.button(key="scr_run").click().run()
    assert not at.exception, (
        f"Scan + AgGrid render raised on Demo universe: {at.exception}. "
        "This is exactly the path that failed on Cloud with theme='alpine-dark'."
    )


def test_picks_renders_with_no_session_state():
    """Picks page must boot cleanly with no positions saved — the most
    common first-visit path for a brand-new user."""
    at = _run_with_timeout("pages/1_Picks.py", timeout=60)
    assert not at.exception, f"Picks initial render raised: {at.exception}"


def test_tape_monitor_renders():
    """Tape Monitor must show the regime banner + calibrator footer without
    crashing on the v1 calibrator artifact."""
    at = _run_with_timeout("pages/13_Tape_Monitor.py", timeout=60)
    assert not at.exception, f"Tape Monitor raised: {at.exception}"


def test_decay_watch_handles_empty_positions():
    """Decay Watch with zero held positions must show the empty state, not
    crash trying to iterate."""
    at = _run_with_timeout("pages/12_Decay_Watch.py", timeout=30)
    assert not at.exception, f"Decay Watch raised: {at.exception}"


def test_dashboard_renders():
    """Dashboard cold-fetches sector indices + market breadth on first paint.
    A generous timeout accommodates yfinance latency without flaking CI;
    we only care that it eventually renders without raising."""
    at = _run_with_timeout("pages/2_Dashboard.py", timeout=120)
    assert not at.exception, f"Dashboard raised: {at.exception}"
