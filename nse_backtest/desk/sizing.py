"""Position sizing: the most shares whose stop-out loss (incl. costs) stays within max loss.

Adapted from github.com/Jakeschincariol/day-trader-skill (MIT).

The risk quantity is found by binary search over [0, floor(budget / R)] for the largest q with
q*R + round_trip_cost(q) <= budget. This equals the brief's linear decrement loop because
q*R + cost(q) is non-decreasing in q (cost is non-decreasing), so the predicate is monotone.
"""
from __future__ import annotations

import math

from .costs import round_trip_cost
from .markets import MarketProfile
from .models import Limits, Sizing, TradePlan

_ORDER = ("max loss per trade", "position cap", "cash after buffer")


def size_position(plan: TradePlan, limits: Limits, profile: MarketProfile, *,
                  spread_per_side: float = 0.00075, halve: bool = False) -> Sizing:
    if not (math.isfinite(limits.capital) and limits.capital > 0):
        raise ValueError("capital must be a positive finite number")
    if not (math.isfinite(plan.entry) and math.isfinite(plan.stop)):
        raise ValueError("entry and stop must be finite")
    if not plan.stop > 0:
        raise ValueError("stop must be positive")
    R = plan.risk_per_share
    if not R > 0:
        raise ValueError("risk per share must be positive (stop below entry)")
    if plan.entry <= 0:
        raise ValueError("entry must be positive")

    def cost(q: int) -> float:
        return round_trip_cost(profile, plan.mode, plan.entry, plan.stop, q, spread_per_side)

    budget = limits.capital * limits.risk_per_trade_pct / 100
    if halve:
        budget /= 2

    lo, hi = 0, max(0, math.floor(budget / R))
    while lo < hi:  # largest q in [lo, hi] with q*R + cost(q) <= budget
        mid = (lo + hi + 1) // 2
        if mid * R + cost(mid) <= budget:
            lo = mid
        else:
            hi = mid - 1
    qty_risk = lo

    qty_pos = math.floor(limits.capital * limits.max_position_pct / 100 / plan.entry)
    qty_cash = math.floor(limits.capital * (1 - limits.cash_buffer_pct / 100) / plan.entry)

    candidates = list(zip(_ORDER, (qty_risk, qty_pos, qty_cash)))
    binding_limit, qty = min(candidates, key=lambda c: c[1])  # min() keeps the first on ties
    qty = max(0, qty)

    if qty < 1:
        if qty_risk < 1:
            cost_1 = cost(1)
            note = (f"Stop too wide for your max loss: 1 share risks {profile.money(R + cost_1)} "
                    f"but your limit is {profile.money(budget)}")
            return Sizing(0, _ORDER[0], 0.0, 0.0, 0.0, halve, note)
        if binding_limit == "position cap":
            limit_amount = limits.capital * limits.max_position_pct / 100
        else:
            limit_amount = limits.capital * (1 - limits.cash_buffer_pct / 100)
        note = (f"Price too high for your {binding_limit}: 1 share costs {profile.money(plan.entry)} "
                f"but that limit allows {profile.money(limit_amount)}")
        return Sizing(0, binding_limit, 0.0, 0.0, 0.0, halve, note)

    costs = cost(qty)
    note = f"{qty} shares, limited by {binding_limit}"
    if halve:
        note += " (size halved after 3 stop-outs in a row)"
    return Sizing(qty, binding_limit, qty * R + costs, qty * plan.entry, costs, halve, note)
