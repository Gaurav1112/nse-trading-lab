""""Holding up": stocks with the best 60-day relative strength vs the market's index, among those above their own
50-EMA. A watchlist for when the regime turns — never a trade, never audited, never scored. Pure: no network."""
from __future__ import annotations

import math

import pandas as pd

LOOKBACK = 60  # trading days
EMA_SPAN = 50
TOP_N = 10


def _closes(df) -> pd.Series | None:
    """The Close column as floats, or None when there is none."""
    try:
        return df["Close"].astype(float)
    except Exception:  # noqa: BLE001 - None, no Close column, unreadable values
        return None


def _ret(close: pd.Series) -> float | None:
    """Percent return over the last LOOKBACK bars; None without enough finite, positive data."""
    if len(close) < LOOKBACK + 1:
        return None
    now, then = float(close.iloc[-1]), float(close.iloc[-(LOOKBACK + 1)])
    if not (math.isfinite(now) and math.isfinite(then) and then > 0 and now > 0):
        return None
    return (now / then - 1.0) * 100


def index_problem(index_df) -> str | None:
    """Why the index can't anchor relative strength (the reason shown instead of a list), or None when it can."""
    close = _closes(index_df)
    if close is None or len(close) == 0:
        return "Regime index data unavailable — no holding-up list"
    if len(close) < LOOKBACK + 1:
        return f"Regime index has only {len(close)} bars (needs {LOOKBACK + 1}) — no holding-up list"
    if _ret(close) is None:
        return "Regime index 60-day return could not be computed — no holding-up list"
    return None


def holding_up(frames: dict[str, pd.DataFrame], index_df, *, n: int = TOP_N) -> list[tuple[str, float]]:
    """[(symbol, relative strength)] best first, at most ``n``: relative strength is the symbol's 60-day % return
    minus the index's, over the same bars (a symbol whose last bar is not the index's is left out). Only symbols
    whose last close is above their own 50-EMA qualify. Fails closed: an unusable index gives []; a symbol with
    missing or bad data is left out. Ties break on the symbol, so the list is deterministic."""
    if index_problem(index_df) is not None:
        return []
    idx_close = _closes(index_df)
    idx_ret = _ret(idx_close)
    idx_last = idx_close.index[-1]
    out: list[tuple[str, float]] = []
    for sym, df in frames.items():
        close = _closes(df)
        if close is None or len(close) < LOOKBACK + 1 or close.index[-1] != idx_last:
            continue
        r = _ret(close)
        ema = float(close.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1])
        last = float(close.iloc[-1])
        if r is None or not math.isfinite(ema) or not last > ema:
            continue
        out.append((str(sym), r - idx_ret))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out[:max(int(n), 0)]
