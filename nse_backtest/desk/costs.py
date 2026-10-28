"""Round-trip trading cost per market and mode."""
from __future__ import annotations

from nse_backtest.picker_replay import _delivery_costs_pct

from .markets import MarketProfile
from .models import Mode


def round_trip_cost(profile: MarketProfile, mode: Mode, entry: float, exit_price: float,
                    qty: int, spread_per_side: float = 0.00075) -> float:
    """Total round-trip cost in local currency (>= 0). qty <= 0 costs nothing."""
    if qty <= 0:
        return 0.0
    buy, sell = entry * qty, exit_price * qty
    if profile.code == "NSE":
        if mode is Mode.SWING:
            return _delivery_costs_pct(entry, exit_price, qty, spread_per_side) / 100 * exit_price * qty
        brk = min(20, 0.0003 * buy) + min(20, 0.0003 * sell)
        stt = 0.00025 * sell
        stamp = 0.00003 * buy
        txn = (buy + sell) * (0.0000297 + 0.000001 + 0.0000001)
        gst = 0.18 * (brk + txn)
        spread = (buy + sell) * spread_per_side
        return brk + stt + stamp + txn + gst + spread
    sec = 0.0000278 * sell
    taf = min(0.000166 * qty, 8.30)
    return sec + taf + (buy + sell) * spread_per_side
