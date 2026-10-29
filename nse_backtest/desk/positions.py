"""Open-position review: wraps position_monitor.daily_check, never raises."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from nse_backtest import position_monitor

_ORDER = {"DATA UNAVAILABLE": 0, "EXIT": 1, "TIGHTEN_STOP": 2, "HOLD": 3}
_REQUIRED = ("symbol", "buy_price", "stop_loss")


@dataclass(frozen=True)
class PositionAction:
    symbol: str
    action: str  # "EXIT" | "TIGHTEN_STOP" | "HOLD" | "DATA UNAVAILABLE"
    current_price: float | None
    entry: float | None  # None when the record's value is unreadable
    stop: float | None
    suggested_stop: float | None
    r_so_far: float | None
    pnl_pct: float | None
    reason: str


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _unavailable(p: dict, reason: str) -> PositionAction:
    return PositionAction(str(p.get("symbol", "?")), "DATA UNAVAILABLE", None, _num(p.get("buy_price")),
                          _num(p.get("stop_loss")), None, None, None, reason)


def _review_one(p: dict, frames: dict[str, pd.DataFrame]) -> PositionAction:
    missing = [k for k in _REQUIRED if p.get(k) is None]
    if missing:
        return _unavailable(p, f"Position record missing required field: {', '.join(missing)}")
    sym = p["symbol"]
    df = frames.get(sym)
    if df is None or len(df) == 0:
        return _unavailable(p, f"No price data for {sym}")
    try:
        v = position_monitor.daily_check(p, df)
    except Exception as e:  # noqa: BLE001 - surfaced as DATA UNAVAILABLE, never silent
        return _unavailable(p, f"Position check failed: {e}")
    entry, stop = _num(p["buy_price"]), _num(p["stop_loss"])
    init = _num(p.get("initial_stop"))
    if init is None:
        init = stop  # missing/None initial stop: measure R from the current stop
    px = _num(v.current_price)
    denom = (entry - init) if entry is not None and init is not None else None
    r = (px - entry) / denom if px is not None and denom is not None and denom > 0 else None
    return PositionAction(sym, v.action, v.current_price, entry, stop, v.suggested_sl, r, v.pnl_pct, v.reason)


def review_positions(positions: list[dict], frames: dict[str, pd.DataFrame], profile) -> list[PositionAction]:
    out = [_review_one(p, frames) for p in positions
           if not p.get("closed") and str(p.get("market") or "NSE").upper() == profile.code]
    return sorted(out, key=lambda a: _ORDER.get(a.action, 9))
