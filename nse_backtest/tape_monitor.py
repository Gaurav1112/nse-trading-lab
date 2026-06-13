"""Tape regime monitor — what kind of market are we in TODAY?

Classifies the current Nifty 50 tape into one of three regimes:
  TRENDING — v1 engine edge is HIGH (2023-like). Trade normally.
  MIXED    — v1 engine edge is MODEST (2024-like). Be selective, score 75+ only.
  HOSTILE  — v1 engine edge is ABSENT (2025-like). Paper-trade or sit out.

The user reads this BEFORE deciding to take any pick.
Owner: Rohan Mehta (A.5).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import pandas as pd


class TapeRegime:
    TRENDING: Final[str] = "TRENDING"
    MIXED: Final[str] = "MIXED"
    HOSTILE: Final[str] = "HOSTILE"


@dataclass
class TapeAssessment:
    regime: str
    nifty_close: float
    return_60d_pct: float
    above_50ema: bool
    above_200ema: bool
    ema_50_above_200: bool      # golden cross active
    ema_200_slope_pct_20d: float  # 200-EMA slope over last 20 bars, %
    recommendation: str          # human-readable, what to do today
    color: str                   # hex, for UI


def assess_tape(nifty_df: pd.DataFrame | None) -> TapeAssessment | None:
    """Classify today's tape into TRENDING / MIXED / HOSTILE.

    nifty_df: yfinance ^NSEI OHLCV. Must have >=200 bars for EMA computation.
    Returns None if data is insufficient.
    """
    if nifty_df is None or len(nifty_df) < 200:
        return None

    close = nifty_df["Close"]
    cur = float(close.iloc[-1])

    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    last_ema50 = float(ema50.iloc[-1])
    last_ema200 = float(ema200.iloc[-1])

    above_50 = cur > last_ema50
    above_200 = cur > last_ema200
    golden_cross = last_ema50 > last_ema200

    ret_60d = (cur / float(close.iloc[-61]) - 1.0) * 100 if len(close) >= 61 else 0.0

    if len(ema200) >= 21:
        slope_200 = (last_ema200 / float(ema200.iloc[-21]) - 1.0) * 100
    else:
        slope_200 = 0.0

    # Regime classification:
    #   TRENDING — broad uptrend confirmed: above 50EMA + golden cross + 60d return > +5%
    #              AND 200-EMA actually slope up
    #   HOSTILE  — broad downtrend: below 200EMA OR 60d return < -3% OR 200-EMA sloping down
    #   MIXED    — everything else (uptrend without strong confirmation)
    if above_50 and golden_cross and ret_60d > 5.0 and slope_200 > 0.5:
        regime = TapeRegime.TRENDING
        rec = ("Edge is HIGH. v1 engine historically returned ~+7.5% per trade in this regime. "
               "Trade normally — take top-3 picks with score ≥65 and standard 2% risk sizing.")
        color = "#00FF87"
    elif not above_200 or ret_60d < -3.0 or slope_200 < -0.5:
        regime = TapeRegime.HOSTILE
        rec = ("Edge is ABSENT. v1 engine was break-even or worse in this regime. "
               "PAPER-TRADE only. Real money: sit out until tape confirms.")
        color = "#FF4D4D"
    else:
        regime = TapeRegime.MIXED
        rec = ("Edge is MODEST. v1 engine returned ~+2% per trade in this regime. "
               "Be selective: take only score ≥75 with R:R ≥2.5. Skip the rest.")
        color = "#FFB800"

    return TapeAssessment(
        regime=regime, nifty_close=cur, return_60d_pct=ret_60d,
        above_50ema=above_50, above_200ema=above_200,
        ema_50_above_200=golden_cross,
        ema_200_slope_pct_20d=slope_200,
        recommendation=rec, color=color,
    )
