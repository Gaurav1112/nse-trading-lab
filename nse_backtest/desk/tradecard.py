"""Trade card: the money figures behind a desk verdict. Pure; numbers only (the caller formats them)."""
from __future__ import annotations

from dataclasses import dataclass, replace

from .costs import round_trip_cost
from .markets import MarketProfile
from .models import Action, DeskVerdict, Limits, Mode, Sizing, Status
from .sizing import loss_at_stop, worst_entry
from .verdict import market_day, ticket_lines

GAP_NOTE = "A gap through the stop can lose more than this."
DAILY_LOSS_NOTE = ("The daily-loss line compares this trade alone with the daily loss limit; losses already taken "
                   "today are checked by the daily-loss check in Checks.")
LIMIT_FILL_NOTE = "An intraday fill can be up to {limit} (the order's limit), which is already in the max loss."
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
    entry_limit: float | None  # intraday: the stop-limit's limit (the ticket's; computed when the plan has none)
    risk_per_share: float
    cost: float | None  # round trip at T1, for qty; None when qty is 0
    max_loss: float | None  # sizing.loss_at_stop: qty*(worst entry - stop) + cost with the exit at the stop
    max_loss_at_trigger: float | None  # the same with the fill at the trigger (entry); None when qty is 0
    profit_t1: float | None
    profit_t2: float | None  # cost computed with the exit at T2
    rr_t1: float
    expected_value: float | None  # mc_expectancy_r * qty * risk_per_share; None without odds
    notional: float | None
    pct_capital_at_risk: float | None  # max_loss (worst fill, incl. costs) as % of capital: the sizer's rule
    pct_capital_deployed: float | None
    breaches: tuple[str, ...]
    gap_note: str
    ticket: tuple[str, ...]
    ticket_header: str
    reasons: tuple[str, ...]
    per_share: tuple[float, float, float]  # (max loss at qty 1, profit at T1, profit at T2) at qty 1
    unavailable_reason: str = ""
    # Intraday: expected value, outcomes and the account projection assume a fill at the trigger; this says how
    # much worse the ticket's fill can be. "" for swing (a LIMIT never fills above entry) and without shares.
    fill_note: str = ""


def _check_qty(qty) -> int:
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        raise ValueError(f"qty must be a whole number of at least 1, got {qty!r}")
    return qty


def _over(x: float, limit: float) -> str:
    """x (already over ``limit``) to one decimal, or two when one decimal would print it equal to the limit."""
    return f"{x:.1f}" if f"{x:.1f}" != f"{limit:.1f}" else f"{x:.2f}"


def _breaches(limits: Limits, risk_pct: float, deployed_pct: float, max_loss_pct: float) -> tuple[str, ...]:
    out = []
    if risk_pct > limits.risk_per_trade_pct + _EPS:
        out.append(f"risk per trade {_over(risk_pct, limits.risk_per_trade_pct)}% of capital "
                   f"(limit {limits.risk_per_trade_pct:.1f}%)")
    if deployed_pct > limits.max_position_pct + _EPS:
        out.append(f"position {_over(deployed_pct, limits.max_position_pct)}% of capital "
                   f"(limit {limits.max_position_pct:.1f}%)")
    if max_loss_pct > limits.daily_loss_limit_pct + _EPS:
        out.append(f"max loss {_over(max_loss_pct, limits.daily_loss_limit_pct)}% of capital exceeds the daily "
                   f"loss limit ({limits.daily_loss_limit_pct:.1f}%)")
    return tuple(out)


def _fill_note(v: DeskVerdict, profile: MarketProfile, n: int) -> str:
    plan = v.plan
    if plan.mode is not Mode.INTRADAY or n < 1:
        return ""
    lim, cp, m = worst_entry(plan, profile), v.current_price, profile.money
    if isinstance(cp, (int, float)) and plan.entry <= cp <= lim:  # the ticket is a plain LIMIT, filled at ~cp
        return (f"assumes a fill at the trigger; the ticket is a limit at the current price {m(cp)}, so about "
                f"{m(n * (cp - plan.entry))} less")
    return f"assumes a fill at the trigger; up to {m(n * (lim - plan.entry))} less at the limit"


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
    sp = base.spread_per_side  # the spread the sizer costed: one cost basis for sizing, headline and card
    c1 = round_trip_cost(profile, plan.mode, plan.entry, plan.target_1, 1, sp)
    c2 = round_trip_cost(profile, plan.mode, plan.entry, plan.target_2, 1, sp)
    per_share = (loss_at_stop(plan, profile, 1, spread_per_side=sp), plan.target_1 - plan.entry - c1, plan.target_2 - plan.entry - c2)
    gap_note = GAP_NOTE
    if plan.mode is Mode.INTRADAY:
        gap_note += " " + LIMIT_FILL_NOTE.format(limit=profile.money(worst_entry(plan, profile)))
    taken = v.action is Action.TAKE
    reasons = () if taken else tuple(f"{c.status.value} {c.name} — {c.reason}"
                                     for c in v.checks if c.status in (Status.BLOCK, Status.WARN))
    common = dict(verdict=v, qty=n, qty_is_override=is_override, entry=plan.entry, stop=plan.stop,
                  target_1=plan.target_1, target_2=plan.target_2,
                  entry_limit=worst_entry(plan, profile) if plan.mode is Mode.INTRADAY else None,
                  risk_per_share=rps, rr_t1=(plan.target_1 - plan.entry) / rps, gap_note=gap_note,
                  reasons=reasons, per_share=per_share)
    if n == 0:
        why = base.note or base.binding_limit
        return TradeCard(**common, sizing=base, cost=None, max_loss=None, max_loss_at_trigger=None,
                         profit_t1=None, profit_t2=None,
                         expected_value=None, notional=None, pct_capital_at_risk=None,
                         pct_capital_deployed=None, breaches=(), ticket=(), ticket_header="",
                         unavailable_reason=f"The desk sized 0 shares ({why}) — enter a quantity to see the money")
    cost = round_trip_cost(profile, plan.mode, plan.entry, plan.target_1, n, sp)
    cost_t2 = round_trip_cost(profile, plan.mode, plan.entry, plan.target_2, n, sp)
    notional = n * plan.entry
    max_loss = loss_at_stop(plan, profile, n, spread_per_side=sp)  # the sizer's and the headline's figure
    max_loss_at_trigger = loss_at_stop(plan, profile, n, entry=plan.entry, spread_per_side=sp)
    risk_pct = max_loss / limits.capital * 100  # incl. costs and the worst fill: the sizer's own rule
    deployed_pct = notional / limits.capital * 100
    ev = None if v.odds is None else v.odds.mc_expectancy_r * n * rps
    breaches = _breaches(limits, risk_pct, deployed_pct, risk_pct) if is_override else ()
    sizing = base if n == base.qty else replace(base, qty=n, risk_amount=max_loss, notional=notional,
                                                costs=cost, note=f"{n} shares (your quantity)")
    shown = taken or override_confirmed
    return TradeCard(
        **common, sizing=sizing, cost=cost, max_loss=max_loss, max_loss_at_trigger=max_loss_at_trigger,
        fill_note=_fill_note(v, profile, n),
        profit_t1=n * (plan.target_1 - plan.entry) - cost,
        profit_t2=n * (plan.target_2 - plan.entry) - cost_t2,
        expected_value=ev, notional=notional,
        pct_capital_at_risk=risk_pct, pct_capital_deployed=deployed_pct, breaches=breaches,
        ticket=(ticket_lines(plan, sizing, profile, current_price=v.current_price,
                             day=market_day(v.created_at, profile)) if shown else ()),
        ticket_header=(f"YOUR OVERRIDE — the desk said {v.action.value}" if shown and not taken else ""))
