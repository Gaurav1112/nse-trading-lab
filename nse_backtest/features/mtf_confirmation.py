"""Multi-timeframe (MTF) trend confirmation.

A common failure mode of intermediate-timeframe (daily) scoring is
firing GO in a stock that is in an uptrend on daily but a downtrend on
weekly. These trades catch the bounce of a larger downtrend and revert
quickly. The MTF gate requires the weekly trend to confirm the daily
view before the engine endorses a GO.

Rule:
  weekly close > weekly 50-EMA
  AND weekly 50-EMA slope over last 4 weeks >= 0

Both must hold or the gate fires (caller downgrades GO -> WAIT).

Returns (downgrade: bool, reason: str). The reason string is always
human-readable so it surfaces in adv_reasons on the score breakdown.
"""
from __future__ import annotations

import pandas as pd


_MIN_WEEKLY_BARS = 50  # ~1 year of weekly bars; lower = unreliable EMA


def mtf_confirms(df: pd.DataFrame) -> tuple[bool, str]:
    """Returns (downgrade, reason). True downgrade => fail confirmation."""
    if df is None or len(df) < 60:
        return False, "MTF: insufficient daily history (<60 bars), skipped"

    weekly = df["Close"].resample("W-FRI").last().dropna()
    if len(weekly) < _MIN_WEEKLY_BARS:
        return False, f"MTF: insufficient weekly bars ({len(weekly)} < {_MIN_WEEKLY_BARS}), skipped"

    ema50_w = weekly.ewm(span=50, adjust=False).mean()
    wk_close = float(weekly.iloc[-1])
    wk_ema = float(ema50_w.iloc[-1])
    above = wk_close > wk_ema

    slope_pct = (wk_ema - float(ema50_w.iloc[-5])) / float(ema50_w.iloc[-5]) * 100 if len(ema50_w) >= 5 else 0.0
    rising = slope_pct >= 0.0

    if above and rising:
        return False, (
            f"MTF: weekly trend confirms (close ₹{wk_close:.0f} > weekly-50EMA "
            f"₹{wk_ema:.0f}, 4w slope {slope_pct:+.2f}%)"
        )

    fails = []
    if not above:
        fails.append(f"weekly close ₹{wk_close:.0f} < weekly-50EMA ₹{wk_ema:.0f}")
    if not rising:
        fails.append(f"weekly-50EMA falling ({slope_pct:+.2f}% over 4w)")
    return True, "MTF disconfirmation: " + "; ".join(fails)
