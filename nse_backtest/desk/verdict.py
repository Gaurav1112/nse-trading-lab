"""Verdict: TAKE / WAIT / SKIP decision, plain-words ticket and input hash.

Adapted from github.com/Jakeschincariol/day-trader-skill (MIT): ticket shown in plain words; nothing is placed.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict
from datetime import datetime
from enum import Enum

from .markets import MarketProfile
from .models import Action, Check, DeskVerdict, Mode, Odds, Sizing, Status, TradePlan


def ticket_lines(plan: TradePlan, sizing: Sizing, profile: MarketProfile) -> tuple[str, ...]:
    sym, qty = plan.symbol, sizing.qty
    intraday = plan.mode is Mode.INTRADAY
    if profile.code == "US":
        lines = (f"BUY {qty} {sym} LIMIT {plan.entry:.2f} — bracket: stop-loss {plan.stop:.2f}, "
                 f"take-profit {plan.target_1:.2f}, {'DAY' if intraday else 'GTC'}",)
        if intraday:
            lines += (f"Flat by {profile.flat_by:%H:%M} ET",)
        return lines
    if intraday:
        return (f"BUY {qty} {sym} MIS LIMIT {plan.entry:.2f}",
                f"SL-M SELL {qty} {sym} trigger {plan.stop:.2f}",
                f"SELL {qty} {sym} LIMIT {plan.target_1:.2f}",
                f"Square off by {profile.flat_by:%H:%M} IST")
    return (f"BUY {qty} {sym} CNC LIMIT {plan.entry:.2f}",
            f"GTT OCO {sym}: stop-loss {plan.stop:.2f} / target {plan.target_1:.2f}")


def _plain(o):
    """Recursively convert enums to their .value so hashing never depends on str(Enum)."""
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    return o


def input_hash(plan: TradePlan, sizing: Sizing, odds: Odds | None, checks, current_price: float) -> str:
    payload = {
        "plan": asdict(plan),
        "sizing": asdict(sizing),
        "odds": asdict(odds) if odds is not None else None,
        "checks": [asdict(c) for c in checks],
        "current_price": current_price,
    }
    blob = json.dumps(_plain(payload), sort_keys=True, default=str, allow_nan=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def decide(plan: TradePlan, sizing: Sizing, odds: Odds | None, checks, profile: MarketProfile,
           current_price: float, now: datetime) -> DeskVerdict:
    checks = tuple(checks)
    m, sym = profile.money, plan.symbol
    lines = ticket_lines(plan, sizing, profile)
    blocks = [c.name for c in checks if c.status is Status.BLOCK]
    ticket: tuple[str, ...] = ()
    trigger: float | None = None
    price_ok = (isinstance(current_price, (int, float)) and not isinstance(current_price, bool)
                and math.isfinite(current_price) and current_price > 0)
    if not price_ok:
        action = Action.SKIP
        headline = f"SKIP {sym} — current price unavailable"
    elif blocks:
        action = Action.SKIP
        headline = f"SKIP {sym} — blocked by: {', '.join(blocks)}"
    elif current_price < plan.entry * (1 - 0.001):
        action, trigger = Action.WAIT, plan.entry
        ticket = ("IF TRIGGERED:",) + lines
        headline = f"WAIT {sym} — buy only above {m(plan.entry)} (now {m(current_price)})"
    elif current_price > plan.entry * 1.01:
        action, trigger = Action.WAIT, plan.entry
        ticket = ("IF TRIGGERED:",) + lines
        headline = f"WAIT {sym} — price ran past entry; buy only on a pullback to {m(plan.entry)}"
    else:
        action, ticket = Action.TAKE, lines
        headline = f"TAKE {sym} — BUY {sizing.qty} @ {m(plan.entry)}, max loss {m(sizing.risk_amount)}"
    return DeskVerdict(action, headline, plan, sizing, odds, checks, ticket, trigger, current_price,
                       input_hash(plan, sizing, odds, checks, current_price), now.isoformat())
