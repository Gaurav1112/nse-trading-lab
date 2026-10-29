"""Pipeline modules must import without their optional third-party packages.

fyers_apiv3 and pywebpush are only needed when the bot actually talks to Fyers
or sends a web push; collecting the test suite (or importing the module) must
not require them. Absence is simulated by putting None in sys.modules, which
makes `import x` raise ImportError. No network is touched.
"""
import importlib
import sys

import pytest

import pipeline
import pipeline.fetch
import pipeline.push


def _fresh_import(monkeypatch, name):
    """Import a fresh copy of pipeline.<name>; monkeypatch restores the original.

    A plain importlib.reload would replace the module's classes (e.g.
    FyersAuthError) for every other test that imported them, so the original
    module object is put back in sys.modules and on the package on teardown.
    """
    full = f"pipeline.{name}"
    monkeypatch.setattr(pipeline, name, sys.modules[full])
    monkeypatch.delitem(sys.modules, full)
    return importlib.import_module(full)


@pytest.fixture
def no_fyers(monkeypatch):
    monkeypatch.setitem(sys.modules, "fyers_apiv3", None)
    monkeypatch.setenv("FYERS_APP_ID", "fictional-app")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "fictional-token")
    return _fresh_import(monkeypatch, "fetch")


@pytest.fixture
def no_pywebpush(monkeypatch):
    monkeypatch.setitem(sys.modules, "pywebpush", None)
    return _fresh_import(monkeypatch, "push")


def test_fetch_imports_without_fyers(no_fyers):
    assert hasattr(no_fyers, "fetch_ltp_with_fallback")


def test_fyers_client_raises_clear_error_without_fyers(no_fyers):
    with pytest.raises(RuntimeError, match="fyers_apiv3 is not installed"):
        no_fyers._fyers_client()


def test_missing_fyers_falls_back_to_yfinance(no_fyers, monkeypatch):
    from datetime import datetime, timezone

    quote = no_fyers.LTPQuote("RELIANCE", 100.0, datetime.now(timezone.utc), "yfinance")
    monkeypatch.setattr(no_fyers, "fetch_yfinance_ltp", lambda syms: {"RELIANCE": quote})
    result, source = no_fyers.fetch_ltp_with_fallback(["RELIANCE"])
    assert source == "yfinance"
    assert result["RELIANCE"].source == "yfinance"


def test_missing_fyers_credentials_fall_back_to_yfinance(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.delenv("FYERS_APP_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    quote = pipeline.fetch.LTPQuote("RELIANCE", 100.0, datetime.now(timezone.utc), "yfinance")
    monkeypatch.setattr(pipeline.fetch, "fetch_yfinance_ltp", lambda syms: {"RELIANCE": quote})
    result, source = pipeline.fetch.fetch_ltp_with_fallback(["RELIANCE"])
    assert source == "yfinance"


def test_push_imports_without_pywebpush(no_pywebpush):
    assert hasattr(no_pywebpush, "send_push")


def test_push_without_pywebpush_reports_all_failed(no_pywebpush, monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY_PEM", "fictional-key")
    subs = [{"endpoint": "https://push.example.invalid/a", "keys": {"p256dh": "x", "auth": "y"}}]
    assert no_pywebpush.send_push({"title": "T"}, subs) == ["https://push.example.invalid/a"]
