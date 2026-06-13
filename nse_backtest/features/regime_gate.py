"""Market-regime gate: downgrade BUY verdicts when broader tape is hostile.

Phase 2B (spec §6): blocks new GO verdicts in non-trending or high-vol Nifty.
Owner: Rohan Mehta (A.5).

Two checks (either blocks):
  1. Trend filter: Nifty 50 below its own 20-day EMA — no broader uptrend.
  2. Vol filter:   Nifty 50 20d realized vol > 1.5× its 60d realized vol
                   — volatility expansion, hostile to swing.

Toggle via REGIME_GATE_ENABLED env var (default "1" when engine=v2).
"""
from __future__ import annotations

import os

import pandas as pd


def _enabled() -> bool:
    return os.getenv("REGIME_GATE_ENABLED", "1") != "0"


def regime_block(nifty_df: pd.DataFrame | None) -> tuple[bool, str]:
    """Return (block_buy, reason).

    block_buy=True means the caller should downgrade any GO verdict to WAIT.
    """
    if not _enabled():
        return False, "Regime gate disabled (REGIME_GATE_ENABLED=0)"
    if nifty_df is None or len(nifty_df) < 61:
        return False, "Regime gate: insufficient nifty data"

    nifty_close = nifty_df["Close"]
    last = nifty_close.iloc[-1]

    ema20 = nifty_close.ewm(span=20, adjust=False).mean().iloc[-1]
    if last < ema20:
        return True, f"Regime block: Nifty {last:.0f} below 20-EMA {ema20:.0f} — non-trending tape"

    returns = nifty_close.pct_change().dropna()
    if len(returns) >= 60:
        vol_20 = returns.iloc[-20:].std()
        vol_60 = returns.iloc[-60:].std()
        if vol_60 > 0 and vol_20 > 1.5 * vol_60:
            return True, (f"Regime block: Nifty 20d vol {vol_20*100:.2f}% "
                          f">1.5× 60d vol {vol_60*100:.2f}% — hostile to swing")

    return False, "Tape supportive (Nifty above 20-EMA, vol stable)"
