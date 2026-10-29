"""The desk's single stop-limit entry rule (intraday ORB), long and short. Used by the Time Machine and the scoreboard."""
from __future__ import annotations

import math

import numpy as np


def entry_limit(entry: float, stop: float, tick: float) -> float:
    """Limit price of the buy stop-limit: entry + min(0.25R, 0.5% of entry), rounded UP to the tick."""
    if not (entry > stop and tick > 0):
        raise ValueError("entry must be above stop and tick positive")
    raw = entry + min(0.25 * (entry - stop), 0.005 * entry)
    return round(math.ceil(round(raw / tick, 9)) * tick, 10)


def limit_or_default(limit: float | None, entry: float, stop: float, tick: float) -> float:
    """The logged/planned limit price, or entry_limit(entry, stop, tick) when there is none."""
    return limit if limit is not None else entry_limit(entry, stop, tick)


def stop_limit_fill(op, hi, lo, k_start: int, k_end: int, trigger: float, limit: float
                    ) -> tuple[int, float] | None:
    """(bar index, fill price) of a buy stop-limit over bars k_start..k_end inclusive, or None.
    Trigger bar = first bar with High >= trigger. Open <= limit there -> fill at max(Open, trigger).
    Open above limit -> the order rests as a buy limit at `limit`; fill at `limit` on the first bar
    (trigger bar included) whose Low <= limit. Price never better than the limit in that case."""
    op, hi, lo = (np.asarray(x, dtype=float) for x in (op, hi, lo))
    k = next((i for i in range(k_start, k_end + 1) if hi[i] >= trigger), None)
    if k is None:
        return None
    if op[k] <= limit:
        return k, float(max(op[k], trigger))
    j = next((i for i in range(k, k_end + 1) if lo[i] <= limit), None)
    return None if j is None else (j, float(limit))


# ---------------------------------------------------------------- shorts: the exact mirror of the long rule

def entry_limit_short(entry: float, stop: float, tick: float) -> float:
    """Limit price of the sell stop-limit: entry - min(0.25R, 0.5% of entry), rounded DOWN to the tick
    (R = stop - entry)."""
    if not (stop > entry and tick > 0):
        raise ValueError("stop must be above entry and tick positive")
    raw = entry - min(0.25 * (stop - entry), 0.005 * entry)
    return round(math.floor(round(raw / tick, 9)) * tick, 10)


def short_limit_or_default(limit: float | None, entry: float, stop: float, tick: float) -> float:
    """The logged/planned sell limit, or entry_limit_short(entry, stop, tick) when there is none."""
    return limit if limit is not None else entry_limit_short(entry, stop, tick)


def stop_limit_fill_short(op, hi, lo, k_start: int, k_end: int, trigger: float, limit: float
                          ) -> tuple[int, float] | None:
    """(bar index, fill price) of a sell stop-limit over bars k_start..k_end inclusive, or None.
    Trigger bar = first bar with Low <= trigger. Open >= limit there -> fill at min(Open, trigger).
    Open below limit -> the order rests as a sell limit at `limit`; fill at `limit` on the first bar
    (trigger bar included) whose High >= limit. Price never worse (lower) than the limit."""
    op, hi, lo = (np.asarray(x, dtype=float) for x in (op, hi, lo))
    k = next((i for i in range(k_start, k_end + 1) if lo[i] <= trigger), None)
    if k is None:
        return None
    if op[k] >= limit:
        return k, float(min(op[k], trigger))
    j = next((i for i in range(k, k_end + 1) if hi[i] >= limit), None)
    return None if j is None else (j, float(limit))
