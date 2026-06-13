"""Relative-strength booster: stock returns vs Nifty 50.

Spec §6 (Phase 2): +10 if 20d AND 60d outperformance vs Nifty 50 > +5%.
Owner: Karthik Subramanian (A.3).
"""
from __future__ import annotations

import pandas as pd


def _pct_return(df: pd.DataFrame, bars: int) -> float | None:
    if len(df) < bars + 1:
        return None
    close = df["Close"]
    return (close.iloc[-1] / close.iloc[-1 - bars] - 1.0) * 100


def rs_vs_nifty_boost(stock_df: pd.DataFrame, nifty_df: pd.DataFrame) -> tuple[int, str]:
    """Compute the rs_vs_nifty additive booster (0 or +10) and a reason string.

    Requires both 20d and 60d outperformance >5% to trigger the boost.
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

    if out20 > 5.0 and out60 > 5.0:
        return 10, f"RS vs Nifty: outperforming by {out20:.1f}% (20d) / {out60:.1f}% (60d)"
    if out20 < -2.0 or out60 < -2.0:
        return 0, f"RS vs Nifty: lagging ({out20:+.1f}% 20d, {out60:+.1f}% 60d)"
    return 0, f"RS vs Nifty: in-line ({out20:+.1f}% 20d, {out60:+.1f}% 60d)"
