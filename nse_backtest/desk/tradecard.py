"""Trade card: the money figures behind a desk verdict. Pure; numbers only (the caller formats them)."""
from __future__ import annotations

from dataclasses import dataclass, replace

from .costs import round_trip_cost
from .markets import MarketProfile
from .models import Action, DeskVerdict, Limits, Sizing, Status
from .verdict import ticket_lines

GAP_NOTE = "A gap through the stop can lose more than this."
_EPS = 1e-9


@dataclass(frozen=True)
class TradeCard:
    verdict: DeskVerdict
    qty: int
    qty_is_override: bool
    sizing: Sizing  # effective sizing: the desk's, or rebuilt for an override qty
    entry: float
    stop: float
    target_1: float
    target_2: float
    entry_limit: float | None
    risk_per_share: float
    cost: float | None  # round trip at T1, for qty; None when qty is 0
    max_loss: float | None  # qty*(entry-stop) + cost; positive = money lost
    profit_t1: float | None
    profit_t2: float | None  # cost computed with the exit at T2
    rr_t1: float
    expected_value: float | None  # mc_expectancy_r * qty * risk_per_share; None without odds
    notional: float | None
    pct_capital_at_risk: float | None  # qty*risk_per_share (before costs) as % of capital
    pct_capital_deployed: float | None
    breaches: tuple[str, ...]
    gap_note: str
    ticket: tuple[str, ...]
    ticket_header: str
    reasons: tuple[str, ...]
    per_share: tuple[float, float, float]  # (loss incl. cost, profit at T1, profit at T2) at qty 1
    unavailable_reason: str = ""


def _check_qty(qty) -> int:
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise ValueError(f"qty must be a whole number of at least 1, got {qty!r}")
    return qty


def _breaches(limits: Limits, risk_pct: float, deployed_pct: float, max_loss_pct: float) -> tuple[str, ...]:
    out = []
    if risk_pct > limits.risk_per_trade_pct + _EPS:
        out.append(f"risk per trade {risk_pct:.1f}% of capital (limit {limits.risk_per_trade_pct:.1f}%)")
    if deployed_pct > limits.max_position_pct + _EPS:
        out.append(f"position {deployed_pct:.1f}% of capital (limit {limits.max_position_pct:.1f}%)")
    if max_loss_pct > limits.daily_loss_limit_pct + _EPS:
        out.append(f"max loss {max_loss_pct:.1f}% of capital exceeds the daily loss limit "
                   f"({limits.daily_loss_limit_pct:.1f}%)")
    return tuple(out)


def build_card(v: DeskVerdict, profile: MarketProfile, limits: Limits, *, qty: int | None = None,
               override_confirmed: bool = False) -> TradeCard:
    plan, base = v.plan, v.sizing
    if qty is not None:
        _check_qty(qty)
    n = base.qty if qty is None else qty
    is_override = qty is not None and qty != base.qty
    rps = plan.entry - plan.stop
    if rps <= 0:
        raise ValueError("entry must be above stop")
    c1 = round_trip_cost(profile, plan.mode, plan.entry, plan.target_1, 1)
    c2 = round_trip_cost(profile, plan.mode, plan.entry, plan.target_2, 1)
    per_share = (rps + c1, plan.target_1 - plan.entry - c1, plan.target_2 - plan.entry - c2)
    taken = v.action is Action.TAKE
    reasons = () if taken else tuple(f"{c.status.value} {c.name} — {c.reason}"
                                     for c in v.checks if c.status in (Status.BLOCK, Status.WARN))
    common = dict(verdict=v, qty=n, qty_is_override=is_override, entry=plan.entry, stop=plan.stop,
                  target_1=plan.target_1, target_2=plan.target_2, entry_limit=plan.entry_limit,
                  risk_per_share=rps, rr_t1=(plan.target_1 - plan.entry) / rps, gap_note=GAP_NOTE,
                  reasons=reasons, per_share=per_share)
    if n == 0:
        why = base.note or base.binding_limit
        return TradeCard(**common, sizing=base, cost=None, max_loss=None, profit_t1=None, profit_t2=None,
                         expected_value=None, notional=None, pct_capital_at_risk=None,
                         pct_capital_deployed=None, breaches=(), ticket=(), ticket_header="",
                         unavailable_reason=f"The desk sized 0 shares ({why}) — enter a quantity to see the money")
    cost = round_trip_cost(profile, plan.mode, plan.entry, plan.target_1, n)
    cost_t2 = round_trip_cost(profile, plan.mode, plan.entry, plan.target_2, n)
    notional = n * plan.entry
    risk_pct = n * rps / limits.capital * 100
    deployed_pct = notional / limits.capital * 100
    max_loss = n * rps + cost
    ev = None if v.odds is None else v.odds.mc_expectancy_r * n * rps
    breaches = _breaches(limits, risk_pct, deployed_pct, max_loss / limits.capital * 100) if is_override else ()
    sizing = base if n == base.qty else replace(base, qty=n, risk_amount=n * rps, notional=notional,
                                                costs=cost, note=f"{n} shares (your quantity)")
    shown = taken or override_confirmed
    return TradeCard(
        **common, sizing=sizing, cost=cost, max_loss=max_loss,
        profit_t1=n * (plan.target_1 - plan.entry) - cost,
        profit_t2=n * (plan.target_2 - plan.entry) - cost_t2,
        expected_value=ev, notional=notional,
        pct_capital_at_risk=risk_pct, pct_capital_deployed=deployed_pct, breaches=breaches,
        ticket=ticket_lines(plan, sizing, profile) if shown else (),
        ticket_header=(f"YOUR OVERRIDE — the desk said {v.action.value}" if shown and not taken else ""))
