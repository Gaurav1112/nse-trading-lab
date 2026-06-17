"""Rolling-window pairwise correlations across the open book.

Used by the correlation-aware Kelly haircut on the Picks page Save flow:
when the user is about to add a position, we compute the candidate's
60-day return correlation against each currently-open position and pass
the list into kelly_size(open_book_correlations=...).

Cheap: pulls daily closes via the same yfinance/cache path the engine
already uses. Cached at session level.
"""
from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

_log = logging.getLogger(__name__)


def _returns_series(df: pd.DataFrame, lookback: int = 60) -> pd.Series:
    """Daily simple returns over the last `lookback` bars. Empty Series if
    the dataframe is unusable."""
    if df is None or len(df) < lookback + 1 or "Close" not in df.columns:
        return pd.Series(dtype=float)
    closes = df["Close"].tail(lookback + 1)
    return closes.pct_change().dropna()


def book_correlations(
    candidate_df: pd.DataFrame,
    open_position_dfs: dict[str, pd.DataFrame],
    lookback: int = 60,
) -> list[float]:
    """Return list of correlation coefficients between candidate and each
    open position's price series.

    Args:
      candidate_df: daily OHLCV for the candidate symbol
      open_position_dfs: {symbol: daily df} for each currently-open position
      lookback: rolling window in bars (default 60 trading days ≈ 3 months)

    Returns one float per open position, in iteration order. NaN-valued
    correlations are dropped (occurs when a series has zero variance).
    """
    cand_r = _returns_series(candidate_df, lookback)
    if cand_r.empty:
        return []
    out: list[float] = []
    for sym, df in open_position_dfs.items():
        held_r = _returns_series(df, lookback)
        if held_r.empty:
            continue
        # Inner join on dates; correlation is sample-size-sensitive
        joined = pd.concat([cand_r, held_r], axis=1, join="inner").dropna()
        if len(joined) < 20:
            continue
        try:
            rho = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
            if pd.notna(rho):
                out.append(rho)
        except Exception as e:
            _log.debug("correlation failed for %s: %s", sym, e)
    return out
