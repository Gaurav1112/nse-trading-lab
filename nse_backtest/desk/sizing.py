"""Position sizing: the most shares whose stop-out loss (incl. costs) stays within max loss.

Adapted from github.com/Jakeschincariol/day-trader-skill (MIT).

R is the worst-fill risk per share: entry - stop for swing (a LIMIT buy never fills above entry), and
entry_limit - stop for intraday (a buy stop-limit can fill anywhere up to its limit). The cost is the round trip
from that worst entry to an exit at the stop. ``loss_at_stop`` is the one figure the headline, the audit row and the
trade card all show.

The risk quantity is found by binary search over [0, floor(budget / R)] for the largest q with
q*R + round_trip_cost(q) <= budget. This equals the brief's linear decrement loop because
q*R + cost(q) is non-decreasing in q (cost is non-decreasing), so the predicate is monotone.
"""
from __future__ import annotations

import math

from . import fills
from .costs import DEFAULT_SPREAD, round_trip_cost
from .markets import MarketProfile
from .models import Limits, Mode, Sizing, TradePlan

_ORDER = ("max loss per trade", "position cap", "cash after buffer")


def worst_entry(plan: TradePlan, profile: MarketProfile) -> float:
    """The worst price the entry order can fill at: the stop-limit's limit for intraday, the entry for swing."""
    if plan.mode is Mode.INTRADAY:
        return fills.limit_or_default(plan.entry_limit, plan.entry, plan.stop, profile.tick)
    return plan.entry


def loss_at_stop(plan: TradePlan, profile: MarketProfile, qty: int, *, entry: float | None = None,
                 spread_per_side: float = DEFAULT_SPREAD) -> float:
    """Money lost by qty shares filled at ``entry`` (default: the worst fill) and stopped out, incl. the round-trip
    cost with the exit at the stop. A gap through the stop can lose more."""
    e = worst_entry(plan, profile) if entry is None else entry
    return qty * (e - plan.stop) + round_trip_cost(profile, plan.mode, e, plan.stop, qty, spread_per_side)


def size_position(plan: TradePlan, limits: Limits, profile: MarketProfile, *,
                  spread_per_side: float = DEFAULT_SPREAD, halve: bool = False) -> Sizing:
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

    worst = worst_entry(plan, profile)
    R = worst - plan.stop  # worst-fill risk per share (intraday: up to the stop-limit's limit)

    def cost(q: int) -> float:
        return round_trip_cost(profile, plan.mode, worst, plan.stop, q, spread_per_side)

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
            return Sizing(0, _ORDER[0], 0.0, 0.0, 0.0, halve, note, spread_per_side)
        if binding_limit == "position cap":
            limit_amount = limits.capital * limits.max_position_pct / 100
        else:
            limit_amount = limits.capital * (1 - limits.cash_buffer_pct / 100)
        note = (f"Price too high for your {binding_limit}: 1 share costs {profile.money(plan.entry)} "
                f"but that limit allows {profile.money(limit_amount)}")
        return Sizing(0, binding_limit, 0.0, 0.0, 0.0, halve, note, spread_per_side)

    costs = cost(qty)
    note = f"{qty} shares, limited by {binding_limit}"
    if halve:
        note += " (size halved after 3 stop-outs in a row)"
    return Sizing(qty, binding_limit, qty * R + costs, qty * plan.entry, costs, halve, note,  # loss_at_stop
                  spread_per_side)
