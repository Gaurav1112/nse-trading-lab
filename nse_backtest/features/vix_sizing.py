"""Volatility regime sizing modifier.

Looks up the latest India VIX value (yfinance ^INDIAVIX) and returns a
multiplier in [0.0, 1.0] that the caller applies to its Kelly position
size.

Rationale: realised drawdown distributions widen materially when VIX is
elevated. Halving the Kelly fraction when VIX > 25 is a documented
heuristic among Indian retail-quant practitioners; it doesn't change
expectancy direction, but it sizes-down right when the worst draws
historically occur.

Thresholds (overridable via env):
  VIX_LOW_PCT   = 20   → multiplier 1.0  (normal regime)
  VIX_HIGH_PCT  = 25   → multiplier 0.5  (high-vol regime)
  > 30                 → multiplier 0.25 (panic regime)

Cached for 5 minutes. Fetch failures default to multiplier=1.0 (no block).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

_log = logging.getLogger(__name__)

VIX_LOW = float(os.environ.get("NSE_VIX_LOW", 20))
VIX_HIGH = float(os.environ.get("NSE_VIX_HIGH", 25))
VIX_PANIC = float(os.environ.get("NSE_VIX_PANIC", 30))

_CACHE: dict = {"value": None, "fetched_at": 0.0}
_TTL_SEC = 300


def _fetch_vix() -> Optional[float]:
    now = time.time()
    if _CACHE["value"] is not None and now - _CACHE["fetched_at"] < _TTL_SEC:
        return _CACHE["value"]
    try:
        import yfinance as yf
        t = yf.Ticker("^INDIAVIX")
        hist = t.history(period="5d", interval="1d")
        if hist is not None and len(hist) > 0:
            v = float(hist["Close"].iloc[-1])
            _CACHE["value"] = v
            _CACHE["fetched_at"] = now
            return v
    except Exception as e:
        _log.debug("VIX fetch failed: %s", e)
    return None


def vix_size_multiplier() -> tuple[float, str]:
    """Returns (multiplier_0_to_1, reason). multiplier=1 when VIX unknown."""
    if os.environ.get("NSE_BACKTEST_MODE") == "1":
        return 1.0, "VIX sizing: backtest mode, neutral multiplier"

    v = _fetch_vix()
    if v is None:
        return 1.0, "VIX unavailable — neutral multiplier"

    if v >= VIX_PANIC:
        return 0.25, f"VIX {v:.1f} ≥ {VIX_PANIC:.0f} (panic) — sizing × 0.25"
    if v >= VIX_HIGH:
        return 0.5, f"VIX {v:.1f} ≥ {VIX_HIGH:.0f} (elevated) — sizing × 0.50"
    if v >= VIX_LOW:
        return 0.8, f"VIX {v:.1f} ≥ {VIX_LOW:.0f} (mild elevation) — sizing × 0.80"
    return 1.0, f"VIX {v:.1f} normal — full sizing"
