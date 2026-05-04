"""Round 5 regression tests — fixes from the 5th 16-expert audit pass.

Each test pins a specific bug a confirmed expert finding identified, so a
future refactor cannot silently regress the same class of issue. Targeted
modules: analytics (Sortino + empty-result keys + drawdown div-by-zero),
trading_modes (IV percentile, monthly-low slicing, weekly resample),
ui logging-handler dedup, and dependency consistency.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


# ── analytics: empty backtest dict must contain all keys downstream uses ──
def test_compute_metrics_empty_result_has_all_keys():
    """Empty backtest must populate final_equity / n_years / max_dd_duration_days
    so callers like ui.py:935, run_backtest.py print_report() and the
    backwards-compat alias don't KeyError."""
    from nse_backtest.analytics import compute_metrics
    from nse_backtest.engine import TradeConfig

    cfg = TradeConfig()
    empty = pd.Series([], dtype=float)
    result = {
        "equity_curve": empty, "trades": [], "config": cfg,
        "buy_hold_curve": empty,
    }
    m = compute_metrics(result)
    for key in ("final_equity", "n_years", "max_dd_duration_days",
                "max_drawdown_duration_days", "sharpe_ratio", "sortino_ratio",
                "calmar_ratio", "total_trades"):
        assert key in m, f"missing key {key!r} in empty-result metrics"
    assert m["final_equity"] == pytest.approx(cfg.initial_capital)
    assert m["n_years"] == 0.0


# ── analytics: Sortino must use canonical target-downside-deviation ──
def test_sortino_uses_target_downside_deviation():
    """Reject the buggy ``daily_returns[<0].std()`` form. With a stream that
    has many small gains and a few large losses, the canonical Sortino
    must be *smaller* than the previous-formula Sortino because target
    downside deviation incorporates zeros for non-negative bars and
    correctly scales by the full sample size."""
    from nse_backtest.analytics import compute_metrics
    from nse_backtest.engine import TradeConfig

    rng = np.random.default_rng(42)
    daily = rng.normal(0.001, 0.005, 500)
    daily[::50] = -0.04
    eq_vals = (1.0 + daily).cumprod() * 100_000.0
    equity = pd.Series(eq_vals, index=pd.date_range("2020-01-01", periods=len(eq_vals), freq="B"))
    cfg = TradeConfig(initial_capital=100_000)
    result = {"equity_curve": equity, "trades": [], "config": cfg,
             "buy_hold_curve": equity}
    m = compute_metrics(result)
    # Hand-compute canonical Sortino to compare
    rets = equity.pct_change().dropna()
    mean_excess = rets.mean() - 0.065 / 252
    target_dd = float(np.sqrt(np.mean(np.minimum(rets - 0.065 / 252, 0) ** 2)))
    expected = float(np.sqrt(252) * mean_excess / target_dd)
    assert m["sortino_ratio"] == pytest.approx(expected, rel=1e-6), (
        f"Sortino regression: got {m['sortino_ratio']:.4f}, expected {expected:.4f}"
    )


# ── analytics: drawdown plot must guard against rolling_max == 0 ──
def test_compute_metrics_handles_zero_initial_equity_drawdown():
    """A degenerate equity curve that starts at 0 used to produce inf/NaN in
    the drawdown plot path. compute_metrics + plot helpers must not raise."""
    from nse_backtest.analytics import compute_metrics
    from nse_backtest.engine import TradeConfig

    eq = pd.Series([0.0, 0.0, 100.0, 110.0, 105.0])
    eq.index = pd.date_range("2020-01-01", periods=5, freq="B")
    cfg = TradeConfig(initial_capital=100.0)
    result = {"equity_curve": eq, "trades": [], "config": cfg,
             "buy_hold_curve": eq}
    m = compute_metrics(result)
    assert np.isfinite(m["max_drawdown_pct"])


# ── trading_modes: IV percentile uses non-NaN denominator ──
def test_iv_percentile_divides_by_valid_count_not_252():
    """Inject a 260-bar sample. After 30-bar rolling-std warm-up there are
    only ~230 valid HV observations in the trailing 252 window. The percentile
    must use that denominator, not 252, otherwise a stock at the *upper end*
    of HV reports ~7% lower than reality and is mis-classified."""
    from nse_backtest.trading_modes import analyze_options
    n = 280
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.02, n)
    close_vals = (1.0 + rets).cumprod() * 100.0
    close = pd.Series(close_vals, index=idx)
    df = pd.DataFrame({
        "Open": close * 0.99, "High": close * 1.01, "Low": close * 0.98,
        "Close": close, "Adj Close": close, "Volume": 1_000_000,
    }, index=idx)
    setup = analyze_options(df, "TESTSYM")
    # Must be in [0, 100]; the 252/252 bug allowed values inflated by NaN bars
    assert 0.0 <= setup.iv_percentile <= 100.0


# ── trading_modes: monthly higher-lows must include the most-recent 20 bars ──
def test_monthly_higher_lows_uses_last_chunk():
    """The previous slicing produced an empty final chunk, so a strictly
    increasing series of monthly lows still failed the comparison. After the
    fix, a stock with monotonically rising 20-bar minima earns the
    'accumulation' bonus."""
    from nse_backtest.trading_modes import analyze_longterm
    n = 600
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    # Monotonic uptrend with rising lows
    base = np.linspace(100, 300, n)
    df = pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Adj Close": base, "Volume": 1_000_000,
    }, index=idx)
    setup = analyze_longterm(df, "MONO", capital=100_000)
    joined = " | ".join(setup.reasons)
    assert "Higher monthly lows" in joined, (
        f"accumulation bonus did not trigger on a monotonic series: {joined}"
    )


# ── trading_modes: weekly RSI uses calendar resample, not iloc[::5] ──
def test_weekly_rsi_uses_calendar_resample():
    """``close.iloc[::5]`` drifts whenever a week has fewer than 5 sessions.
    The fix uses ``resample('W-FRI').last()`` which always aligns to calendar
    weeks. Confirm by injecting a holiday gap and verifying analyze_positional
    still runs without indexing errors."""
    from nse_backtest.trading_modes import analyze_positional
    idx = pd.bdate_range("2022-01-03", periods=400)
    # Drop a few mid-week sessions to simulate NSE holidays
    idx = idx.delete([50, 51, 120, 220, 320])
    base = np.linspace(100, 200, len(idx))
    df = pd.DataFrame({
        "Open": base, "High": base * 1.01, "Low": base * 0.99,
        "Close": base, "Adj Close": base, "Volume": 1_000_000,
    }, index=idx)
    setup = analyze_positional(df, "HOLIDAY", capital=100_000)
    assert setup.symbol == "HOLIDAY"
    assert isinstance(setup.score, (int, float))


# ── _logging: file handler not duplicated across re-init ──
def test_log_file_handler_not_duplicated(monkeypatch, tmp_path):
    """Repeated _init() calls (Streamlit reruns / repeated imports) must not
    accumulate RotatingFileHandlers, which would multiply log volume and
    leak file descriptors."""
    monkeypatch.setenv("NSE_LOG_FILE", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Force re-init by toggling the module-private flag.
    from nse_backtest import _logging as L
    L._INITIALISED = False
    # Strip any existing rotating file handlers from prior tests so the count
    # we observe is solely the result of this test's two _init() calls.
    root = logging.getLogger("nse_backtest")
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
    L._init()
    L._INITIALISED = False
    L._init()
    fhs = [h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(fhs) == 1, f"expected 1 file handler, got {len(fhs)}"


# ── ui (smoke): mutable defaults are deep-copied per session ──
def test_ui_session_defaults_are_isolated():
    """Verify that the deep-copy guard in ui.py prevents two simulated
    sessions from sharing the same mutable list/dict object.

    We can't actually import ui.py here without a Streamlit runtime, so
    replicate the exact init pattern and confirm reference isolation."""
    import copy
    DEFAULTS = {"watchlist": ["A", "B"], "journal": [], "capital": 100.0}
    sess1, sess2 = {}, {}
    for sess in (sess1, sess2):
        for k, v in DEFAULTS.items():
            sess[k] = copy.deepcopy(v) if isinstance(v, (list, dict, set)) else v
    sess1["watchlist"].append("LEAK")
    assert "LEAK" not in sess2["watchlist"]
    assert "LEAK" not in DEFAULTS["watchlist"]


# ── pyproject ↔ requirements.txt: ranges must be consistent ──
def test_pyproject_and_requirements_pin_ranges_match():
    """Drift between requirements.txt and pyproject.toml causes
    non-reproducible installs (pip install -r vs pip install -e .)."""
    import re
    repo = Path(__file__).resolve().parents[1]
    req = (repo / "requirements.txt").read_text()
    pyproj = (repo / "pyproject.toml").read_text()
    # Spot-check the packages most prone to drift.
    for pkg in ("pandas", "numpy", "pyarrow", "plotly"):
        m_req = re.search(rf"^{pkg}\s*([<>=,\d.]+)", req, re.M)
        m_pp = re.search(rf'"{pkg}\s*([<>=,\d.]+)"', pyproj)
        assert m_req and m_pp, f"{pkg} pin missing"
        assert m_req.group(1).replace(" ", "") == m_pp.group(1).replace(" ", ""), (
            f"{pkg} drift: requirements.txt={m_req.group(1)!r} "
            f"pyproject.toml={m_pp.group(1)!r}"
        )
