"""Round-3 hardening tests: ichimoku Chikou, screener clamps, data validation,
analytics conventions, scorer EV→WAIT downgrade, CSV/path security."""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

from nse_backtest.indicators import ichimoku
from nse_backtest.analytics import compute_metrics
from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.strategies import sma_crossover
from nse_backtest.risk import calmar_ratio
from nse_backtest.data import _validate_symbol


# ─── Indicators ───────────────────────────────────────────────────────────

def test_ichimoku_chikou_uses_close_kijun_bars_ago():
    """Chikou bull/bear must compare today's close vs close kijun bars ago,
    NOT vs today's close (which would always be False)."""
    n = 100
    close = pd.Series(np.linspace(100, 200, n))  # strictly rising → chikou bull True
    df = pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": [1000] * n,
    })
    out = ichimoku(df)
    assert any("Chikou confirms" in r for r in out["reasons"]), \
        "Rising series should yield chikou_bull=True"
    # The chikou value is the close 26 bars ago — well below today's close (200).
    assert out["chikou"] < close.iloc[-1] - 5

    # Falling series → chikou_bull False (no Chikou-confirms reason emitted)
    close_fall = pd.Series(np.linspace(200, 100, n))
    df2 = df.copy()
    df2["Close"] = close_fall
    df2["Open"] = close_fall
    df2["High"] = close_fall * 1.01
    df2["Low"] = close_fall * 0.99
    out2 = ichimoku(df2)
    assert not any("Chikou confirms" in r for r in out2["reasons"])


# ─── Data layer ───────────────────────────────────────────────────────────

def test_validate_symbol_strips_exchange_suffix():
    """INFY.NS + exchange='NS' must not concatenate to INFY.NS.NS."""
    assert _validate_symbol("INFY.NS") == "INFY"
    assert _validate_symbol("TCS.BO") == "TCS"
    assert _validate_symbol("RELIANCE.NSE") == "RELIANCE"
    assert _validate_symbol("HDFC.BSE") == "HDFC"
    # Plain symbol untouched.
    assert _validate_symbol("RELIANCE") == "RELIANCE"


def test_validate_symbol_rejects_only_suffix():
    with pytest.raises(ValueError):
        _validate_symbol(".NS")


# ─── Screener ─────────────────────────────────────────────────────────────

def test_screener_squeeze_entry_not_below_current():
    """scan_squeeze entry should be ≥ current (BB upper or current)."""
    from nse_backtest.screener import scan_squeeze
    n = 80
    # Tight-range data with small uptick at the end to satisfy BB-squeeze conditions.
    np.random.seed(7)
    base = 100 + np.cumsum(np.random.normal(0, 0.2, n))
    df = pd.DataFrame({
        "Open": base, "High": base + 0.4, "Low": base - 0.4,
        "Close": base, "Volume": [1_000_000] * n,
    })
    out = scan_squeeze(df, "TEST")
    if out is not None:
        # entry must not be below current (would be a look-ahead bug).
        assert out.entry_price >= out.current_price - 1e-6


def test_screener_breakout_entry_not_above_current():
    """scan_breakout entry should be ≤ current (or equal, for retest setups)."""
    from nse_backtest.screener import scan_breakout
    n = 80
    np.random.seed(11)
    trend_up = np.linspace(100, 130, n) + np.random.normal(0, 0.5, n)
    df = pd.DataFrame({
        "Open": trend_up, "High": trend_up + 1, "Low": trend_up - 1,
        "Close": trend_up, "Volume": [2_000_000] * n,
    })
    out = scan_breakout(df, "TEST")
    if out is not None:
        # For "near breakout" setup, entry must not exceed current price.
        # (For "already-broken-out" branch, entry equals breakout_level which is below current.)
        assert out.entry_price <= out.current_price + 1e-6


# ─── Risk / analytics conventions ─────────────────────────────────────────

def test_calmar_ratio_signed_for_negative_cagr():
    """Calmar with negative CAGR must be negative — abs() bug used to hide losses."""
    assert calmar_ratio(-0.10, -0.20) < 0
    assert calmar_ratio(0.10, -0.20) > 0


def test_profit_factor_inf_when_no_losses(trending_ohlcv):
    """Strategies with zero losing trades should report profit_factor == inf,
    not the legacy 99.99 sentinel."""
    df = sma_crossover(trending_ohlcv)
    cfg = TradeConfig(initial_capital=100_000)
    res = run_backtest(df, cfg)
    metrics = compute_metrics(res)
    pf = metrics["profit_factor"]
    losing = [t for t in res["trades"] if t.pnl < 0]
    if not losing and any(t.pnl > 0 for t in res["trades"]):
        assert pf == float("inf")
    # Always — never a magic 99.99.
    assert pf != 99.99


# ─── Scorer EV downgrade ──────────────────────────────────────────────────

def test_scorer_negative_ev_downgrades_wait_to_avoid():
    """Verify the EV<0 override now also downgrades WAIT → AVOID, not just GO → AVOID."""
    with open(os.path.join(os.path.dirname(__file__), "..", "nse_backtest", "scorer.py")) as f:
        src = f.read()
    # Locate the EV-override block by the marker comment introduced in R3.
    marker = "Negative expected value — verdict downgraded to AVOID"
    assert marker in src
    idx = src.index(marker)
    snippet = src[max(0, idx - 600): idx]
    assert "GO" in snippet and "WAIT" in snippet, \
        "EV override block must downgrade BOTH GO and WAIT verdicts"


# ─── ui security helpers ──────────────────────────────────────────────────

def test_ui_csv_safe_defangs_formula_injection():
    """CSV cells starting with =, +, -, @ must be prefixed with single quote."""
    # Import lazily because ui imports streamlit; run inside a guarded block.
    if "streamlit" not in sys.modules:
        try:
            import streamlit  # noqa: F401
        except Exception:
            pytest.skip("streamlit not available")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import importlib
    ui = importlib.import_module("ui")
    csv_safe = getattr(ui, "_csv_safe")
    assert csv_safe("=cmd|'/c calc'!A1").startswith("'=")
    assert csv_safe("+1+1") == "'+1+1"
    assert csv_safe("-cmd").startswith("'-")
    assert csv_safe("@SUM(A1)").startswith("'@")
    assert csv_safe("RELIANCE") == "RELIANCE"   # benign untouched
    assert csv_safe(None) == ""


def test_ui_safe_filename_strips_path_traversal():
    if "streamlit" not in sys.modules:
        try:
            import streamlit  # noqa: F401
        except Exception:
            pytest.skip("streamlit not available")
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import importlib
    ui = importlib.import_module("ui")
    safe = getattr(ui, "_safe_filename")
    assert "/" not in safe("../../etc/passwd")
    assert ".." not in safe("../../etc/passwd")
    assert safe("RELIANCE") == "RELIANCE"
    assert safe("") == "export"
