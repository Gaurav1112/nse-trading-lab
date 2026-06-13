"""Gap-up entry guard — refuse to chase a +2% open.

A common late-entry failure mode: the scorer fires GO on an overnight
news catalyst that gapped the stock open well above the previous
close. Entering at that elevated open captures the gap as an immediate
unrealised loss the moment the gap fills. The guard refuses entries
where today's open is more than +2% above yesterday's close.

Rule:
  today_open <= prev_close * 1.02

Returns (downgrade: bool, reason: str).
"""
from __future__ import annotations

import os

import pandas as pd


_DEFAULT_MAX_GAP_PCT = 2.0
_MAX_GAP_PCT = float(os.getenv("NSE_GAP_MAX_PCT", _DEFAULT_MAX_GAP_PCT))


def gap_within_tolerance(df: pd.DataFrame) -> tuple[bool, str]:
    """Returns (downgrade, reason). True downgrade => gap too large to chase."""
    if df is None or len(df) < 2:
        return False, "Gap guard: insufficient history, skipped"
    if "Open" not in df.columns:
        return False, "Gap guard: no Open column, skipped"

    today_open = float(df["Open"].iloc[-1])
    prev_close = float(df["Close"].iloc[-2])
    if prev_close <= 0:
        return False, "Gap guard: invalid prev close, skipped"
    gap_pct = (today_open - prev_close) / prev_close * 100
    if gap_pct <= _MAX_GAP_PCT:
        return False, f"Gap OK ({gap_pct:+.2f}% vs prev close)"
    return True, (
        f"Gap-up too large: today open {today_open:.2f} is "
        f"{gap_pct:+.2f}% above prev close — refusing chase"
    )
