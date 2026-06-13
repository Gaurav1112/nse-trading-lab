"""Liquidity filter — refuse setups in illiquid stocks.

Illiquid stocks suffer from gap-fills that slip the entry price, wide
bid-ask spreads that eat expectancy, and occasional circuit-locks that
trap positions. The walk-forward A/B doesn't model this slippage
beyond a flat 0.075%/side spread, so the actual hit is bigger for thin
names. Easier to just exclude them.

Rule:
  20-day average (close * volume) >= ₹50 crore (₹500,000,000)

Returns (downgrade: bool, reason: str).
"""
from __future__ import annotations

import os

import pandas as pd


_DEFAULT_MIN_INR = 50 * 10**7  # ₹50 crore
_MIN_INR = float(os.getenv("NSE_LIQUIDITY_MIN_INR", _DEFAULT_MIN_INR))


def is_liquid_enough(df: pd.DataFrame) -> tuple[bool, str]:
    """Returns (downgrade, reason). True downgrade => liquidity too thin."""
    if df is None or len(df) < 20 or "Volume" not in df.columns:
        return False, "Liquidity: insufficient history, skipped"

    last20 = df.tail(20)
    avg_inr = float((last20["Close"] * last20["Volume"]).mean())
    if avg_inr >= _MIN_INR:
        return False, f"Liquidity OK (20d avg ₹{avg_inr / 10**7:.1f} crore)"
    return True, (
        f"Liquidity too thin: 20d avg ₹{avg_inr / 10**7:.2f} crore "
        f"< threshold ₹{_MIN_INR / 10**7:.0f} crore"
    )
