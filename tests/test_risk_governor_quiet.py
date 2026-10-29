import sys

import pytest

from components import risk_governor as rg


class _Spy:
    touched = False

    def get(self, *a, **k):
        _Spy.touched = True
        return 123


def test_lr_setting_outside_streamlit_run_never_touches_session_state_and_is_quiet(monkeypatch, capfd):
    import streamlit as st
    monkeypatch.setattr(st, "session_state", _Spy())
    v = rg._setting("max_open_positions", 99)
    assert not _Spy.touched
    assert v == rg.DEFAULTS["max_open_positions"]
    assert "ScriptRunContext" not in capfd.readouterr().err


def test_lr_setting_unknown_name_returns_fallback():
    assert rg._setting("no_such_setting", 7) == 7


def test_lr_setting_survives_missing_streamlit(monkeypatch):
    monkeypatch.setitem(sys.modules, "streamlit", None)
    monkeypatch.delitem(sys.modules, "streamlit.runtime", raising=False)
    assert rg._setting("max_open_positions", 99) == rg.DEFAULTS["max_open_positions"]
