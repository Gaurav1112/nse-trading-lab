"""Tests for VIX-driven sizing multiplier."""
from __future__ import annotations

import os

import pytest

import nse_backtest.features.vix_sizing as vix_mod
from nse_backtest.features.vix_sizing import vix_size_multiplier


@pytest.fixture(autouse=True)
def reset_vix_cache(monkeypatch):
    vix_mod._CACHE["value"] = None
    vix_mod._CACHE["fetched_at"] = 0.0
    monkeypatch.delenv("NSE_BACKTEST_MODE", raising=False)
    yield
    vix_mod._CACHE["value"] = None
    vix_mod._CACHE["fetched_at"] = 0.0


def test_vix_neutral_in_backtest_mode(monkeypatch):
    monkeypatch.setenv("NSE_BACKTEST_MODE", "1")
    mult, reason = vix_size_multiplier()
    assert mult == 1.0
    assert "backtest" in reason.lower()


def test_vix_normal_full_size(monkeypatch):
    monkeypatch.setattr(vix_mod, "_fetch_vix", lambda: 15.0)
    mult, reason = vix_size_multiplier()
    assert mult == 1.0
    assert "normal" in reason.lower()


def test_vix_mild_elevation(monkeypatch):
    monkeypatch.setattr(vix_mod, "_fetch_vix", lambda: 22.0)
    mult, reason = vix_size_multiplier()
    assert mult == 0.8
    assert "mild" in reason.lower()


def test_vix_high_halves_size(monkeypatch):
    monkeypatch.setattr(vix_mod, "_fetch_vix", lambda: 27.0)
    mult, reason = vix_size_multiplier()
    assert mult == 0.5


def test_vix_panic_quarters_size(monkeypatch):
    monkeypatch.setattr(vix_mod, "_fetch_vix", lambda: 35.0)
    mult, reason = vix_size_multiplier()
    assert mult == 0.25


def test_vix_unknown_defaults_full(monkeypatch):
    monkeypatch.setattr(vix_mod, "_fetch_vix", lambda: None)
    mult, reason = vix_size_multiplier()
    assert mult == 1.0
    assert "unavailable" in reason.lower()
