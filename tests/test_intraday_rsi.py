"""Tests for the intraday 15-min RSI scanner."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nse_backtest.intraday.rsi_scanner import _wilders_rsi, scan_rsi


def _closes(values: list[float]) -> pd.Series:
    idx = pd.date_range("2026-06-19 09:15", periods=len(values), freq="15min")
    return pd.Series(values, index=idx)


def test_rsi_returns_none_on_short_series():
    s = _closes([100, 101, 102])
    assert _wilders_rsi(s, period=14) is None


def test_rsi_neutral_on_constant_series():
    s = _closes([100.0] * 50)
    # Constant series → 0 gain / 0 loss; we return 50 as neutral fallback
    out = _wilders_rsi(s, period=14)
    assert 49 <= out <= 51


def test_rsi_high_on_pure_uptrend():
    s = _closes([100 + i * 0.5 for i in range(50)])
    out = _wilders_rsi(s, period=14)
    assert out > 90


def test_rsi_low_on_pure_downtrend():
    s = _closes([100 - i * 0.5 for i in range(50)])
    out = _wilders_rsi(s, period=14)
    assert out < 10


def test_rsi_zero_loss_returns_100_for_gain():
    """When recent bars only have gains and no losses, RSI saturates at 100."""
    s = _closes([100 + i for i in range(30)])  # strict uptrend
    out = _wilders_rsi(s)
    assert out == 100.0


def test_scan_returns_empty_when_yfinance_fails(monkeypatch):
    """No exception leaks if the batch fetch returns None."""
    import nse_backtest.intraday.rsi_scanner as mod
    monkeypatch.setattr(mod, "_batch_fetch_15m", lambda syms, period="5d": None)
    out = scan_rsi(["FAKE"], rsi_threshold=15.0)
    assert out == []


def test_scan_filters_to_threshold(monkeypatch):
    """Symbols with RSI >= threshold are excluded; results sorted ascending."""
    import nse_backtest.intraday.rsi_scanner as mod

    # Stub the fetch with a fake grouped frame: one falling, one rising.
    n = 30
    idx = pd.date_range("2026-06-19 09:15", periods=n, freq="15min")
    fall_close = [100 - i for i in range(n)]
    rise_close = [100 + i for i in range(n)]
    cols = pd.MultiIndex.from_product(
        [["FALL.NS", "RISE.NS"], ["Open", "High", "Low", "Close", "Volume"]],
    )
    df = pd.DataFrame(index=idx, columns=cols, dtype=float)
    for col in ["Open", "High", "Low", "Close"]:
        df["FALL.NS", col] = fall_close
        df["RISE.NS", col] = rise_close
    df["FALL.NS", "Volume"] = 100_000
    df["RISE.NS", "Volume"] = 100_000

    monkeypatch.setattr(mod, "_batch_fetch_15m", lambda syms, period="5d": df)
    out = scan_rsi(["FALL", "RISE"], rsi_threshold=30.0)
    # RISE has RSI ~100 (excluded); FALL has RSI ~0 (included)
    assert len(out) == 1
    assert out[0].symbol == "FALL"
    assert out[0].rsi < 30
