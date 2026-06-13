"""Relative-strength booster: stock returns vs Nifty 50.

Spec §6 (Phase 2): +10 if 20d AND 60d outperformance vs Nifty 50 > threshold%.
Threshold defaults to 5% but is configurable via RS_OUTPERFORMANCE_PCT env var
so A/B sweeps can search for the value with real edge without code churn.
Owner: Karthik Subramanian (A.3).
"""
from __future__ import annotations

import os

import pandas as pd


def _pct_return(df: pd.DataFrame, bars: int) -> float | None:
    if len(df) < bars + 1:
        return None
    close = df["Close"]
    return (close.iloc[-1] / close.iloc[-1 - bars] - 1.0) * 100


def _threshold() -> float:
    """Outperformance % required on BOTH 20d and 60d to trigger the boost.

    Default 8.0 was the local optimum in a sweep across 5/7/8/10 on Nifty 50
    2024 (stride=5): only 8% delivered positive Δ expectancy vs v1 baseline
    (+0.10pp, vs -0.03 to -0.12pp at the other thresholds). Override with
    RS_OUTPERFORMANCE_PCT env var to re-tune.
    """
    try:
        return float(os.getenv("RS_OUTPERFORMANCE_PCT", "8.0"))
    except ValueError:
        return 8.0


def rs_vs_nifty_boost(stock_df: pd.DataFrame, nifty_df: pd.DataFrame) -> tuple[int, str]:
    """Compute the rs_vs_nifty additive booster (0 or +10) and a reason string.

    Requires both 20d and 60d outperformance > threshold% to trigger the boost.
    Threshold read from RS_OUTPERFORMANCE_PCT env var (default 5.0).
    Pure function — caller fetches nifty_df via nse_backtest.data.fetch_nifty50.
    """
    if nifty_df is None or len(nifty_df) < 61:
        return 0, "RS vs Nifty: insufficient nifty data"

    s20 = _pct_return(stock_df, 20)
    n20 = _pct_return(nifty_df, 20)
    s60 = _pct_return(stock_df, 60)
    n60 = _pct_return(nifty_df, 60)

    if None in (s20, n20, s60, n60):
        return 0, "RS vs Nifty: insufficient stock history"

    out20 = s20 - n20
    out60 = s60 - n60
    thr = _threshold()

    if out20 > thr and out60 > thr:
        return 10, f"RS vs Nifty: outperforming by {out20:.1f}% (20d) / {out60:.1f}% (60d) [thr={thr:.0f}%]"
    if out20 < -2.0 or out60 < -2.0:
        return 0, f"RS vs Nifty: lagging ({out20:+.1f}% 20d, {out60:+.1f}% 60d)"
    return 0, f"RS vs Nifty: in-line ({out20:+.1f}% 20d, {out60:+.1f}% 60d)"
