""""Holding up": stocks with the best 60-day relative strength vs the market's index, among those above their own
50-EMA. A watchlist for when the regime turns — never a trade, never audited, never scored. Pure: no network."""
from __future__ import annotations

import math
from collections import Counter

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


MISMATCH = "index and stock data end on different days (index {index}, stocks {stocks}) — holding-up list unavailable"
LEFT_OUT = "{k} stock(s) left out: their data ends on a different day"
EMPTY = "No stock is beating the index while above its 50-EMA today"
NOT_A_TRADE = "ready for when the regime turns — not a trade"


def _day(ts) -> str:
    try:
        return pd.Timestamp(ts).date().isoformat()
    except Exception:  # noqa: BLE001
        return str(ts)


def holding_up_report(frames: dict[str, pd.DataFrame], index_df, *, n: int = TOP_N
                      ) -> tuple[list[tuple[str, float]], str | None]:
    """(list, reason). The list: [(symbol, relative strength)] best first, at most ``n``, where relative strength is
    the symbol's 60-day % return minus the index's over the same bars. Only symbols that BEAT the index (> 0) while
    closing above their own 50-EMA qualify; ties break on the symbol. Fails closed with a reason (an empty list must
    never read as a market fact): an unusable index, or an index whose last bar is not the most common last bar among
    the stocks. When the index agrees with most stocks, the stocks ending on another day are left out and the reason
    says how many. A symbol with missing or bad data is left out."""
    why = index_problem(index_df)
    if why is not None:
        return [], why
    idx_close = _closes(index_df)
    idx_ret = _ret(idx_close)
    idx_last = idx_close.index[-1]
    usable = [(str(sym), c) for sym, df in frames.items()
              if (c := _closes(df)) is not None and len(c) >= LOOKBACK + 1]
    days = Counter(c.index[-1] for _, c in usable)
    # the index must agree with most stocks; a few stocks off that day (a halt, a late feed) are left out, noted
    if days and days[idx_last] < max(days.values()):
        off = sorted(_day(d) for d in days if d != idx_last)
        return [], MISMATCH.format(index=_day(idx_last), stocks=", ".join(off))
    out: list[tuple[str, float]] = []
    left_out = 0
    for sym, close in usable:
        if close.index[-1] != idx_last:
            left_out += 1
            continue
        r = _ret(close)
        ema = float(close.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1])
        last = float(close.iloc[-1])
        if r is None or not math.isfinite(ema) or not last > ema:
            continue
        rs = r - idx_ret
        if rs > 0:
            out.append((sym, rs))
    out.sort(key=lambda t: (-t[1], t[0]))
    note = LEFT_OUT.format(k=left_out) if left_out else None
    return out[:max(int(n), 0)], note


def holding_up(frames: dict[str, pd.DataFrame], index_df, *, n: int = TOP_N) -> list[tuple[str, float]]:
    """holding_up_report's list alone ([] whenever it has a reason)."""
    return holding_up_report(frames, index_df, n=n)[0]
