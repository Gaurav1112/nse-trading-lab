"""Streamlit UI smoke test using streamlit.testing.v1.AppTest.

This boots the app in headless mode and asserts that no exception escapes.
Network calls are not mocked — the app falls back to demo data when fetches
fail, so this remains deterministic offline.
"""
import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest


def test_ui_loads_without_exceptions():
    at = AppTest.from_file("ui.py", default_timeout=60)
    at.run()
    # `at.exception` is a list-like of any uncaught exceptions during the run.
    excs = list(at.exception) if at.exception else []
    assert not excs, f"UI raised: {excs}"
