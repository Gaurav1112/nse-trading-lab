"""Verdict: TAKE / WAIT / SKIP decision, plain-words ticket and input hash.

Adapted from github.com/Jakeschincariol/day-trader-skill (MIT): ticket shown in plain words; nothing is placed.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, replace
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

from . import calendars as cal
from . import fills
from . import models
from . import plan as plan_mod
from .checks import REGIME_HELD_OUT_PCT, SHORT_EDGE_UNPROVEN, signed_pct
from .markets import MarketProfile
from .models import (Action, DeskVerdict, Mode, Odds, Side, Sizing, Status, TradePlan, regime_name,
                     summary_fields)
from .sizing import loss_at_stop


def stop_limit(plan: TradePlan, profile: MarketProfile) -> float:
    """The intraday entry stop-limit's limit price (computed for a plan built without one): above the trigger for a
    long's buy, below it for a short's sell."""
    if plan.side is Side.SHORT:
        return fills.short_limit_or_default(plan.entry_limit, plan.entry, plan.stop, profile.tick)
    return fills.limit_or_default(plan.entry_limit, plan.entry, plan.stop, profile.tick)


SQUARE_OFF_CHARGE_NOTE = "broker auto square-off charges ₹59+"


def _short_ticket(plan: TradePlan, sizing: Sizing, profile: MarketProfile, current_price, d: date
                  ) -> tuple[str, ...]:
    """NSE MIS short. With the price at or below the trigger the entry is a plain sell LIMIT at the stop-limit's
    limit (a stop sell whose trigger is already above the last price is rejected); above it, the sell stop-limit."""
    sym, qty = plan.symbol, sizing.qty
    lim, cancel = stop_limit(plan, profile), cal.entry_cancel_by(profile, d)
    square = plan_mod.short_flat_by(profile, d)
    triggered = (isinstance(current_price, (int, float)) and not isinstance(current_price, bool)
                 and math.isfinite(current_price) and current_price <= plan.entry)
    entry = (f"SELL {qty} {sym} MIS LIMIT {lim:.2f}" if triggered else
             f"SELL {qty} {sym} MIS SL trigger {plan.entry:.2f} limit {lim:.2f}")
    return (entry,
            f"Cancel the entry if not filled by {cancel:%H:%M} IST",
            f"SL-M BUY {qty} {sym} trigger {plan.stop:.2f}",
            f"BUY {qty} {sym} LIMIT {plan.target_1:.2f}",
            f"Square off yourself by {square:%H:%M} IST ({SQUARE_OFF_CHARGE_NOTE})")


def market_day(ts, profile: MarketProfile) -> date | None:
    """The market-local date of an ISO timestamp / datetime (naive = market time); None if unreadable."""
    try:
        t = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
    except ValueError:
        return None
    return t.astimezone(ZoneInfo(profile.tz)).date() if t.tzinfo is not None else t.date()


def ticket_lines(plan: TradePlan, sizing: Sizing, profile: MarketProfile, *, current_price: float | None = None,
                 day: date | None = None) -> tuple[str, ...]:
    """The plain-words ticket. Intraday: with ``current_price`` at or above the trigger the entry is a plain LIMIT
    at the stop-limit's limit (a broker rejects a stop buy whose trigger is already below the last price); below
    it, or with no price, the buy stop-limit. ``day`` (default: the plan's last bar's date) sets the cancel-by
    and flat-by times, pulled in on an early close."""
    sym, qty = plan.symbol, sizing.qty
    intraday = plan.mode is Mode.INTRADAY
    if plan.side is Side.SHORT:
        if profile.code != "NSE" or not intraday:
            raise ValueError("shorts are NSE intraday only")
        return _short_ticket(plan, sizing, profile, current_price,
                             day or market_day(plan.last_bar, profile) or date.today())
    if intraday:
        d = day or market_day(plan.last_bar, profile) or date.today()
        lim, flat, cancel = stop_limit(plan, profile), cal.flat_by_for(profile, d), cal.entry_cancel_by(profile, d)
        triggered = (isinstance(current_price, (int, float)) and not isinstance(current_price, bool)
                     and math.isfinite(current_price) and current_price >= plan.entry)
    if profile.code == "US":
        if intraday:
            bracket = f"bracket: stop-loss {plan.stop:.2f}, take-profit {plan.target_1:.2f}, DAY"
            entry = (f"BUY {qty} {sym} LIMIT {lim:.2f} — {bracket}" if triggered else
                     f"BUY {qty} {sym} STOP-LIMIT stop {plan.entry:.2f} limit {lim:.2f} — {bracket}")
            return (entry, f"Cancel the entry if not filled by {cancel:%H:%M} ET", f"Flat by {flat:%H:%M} ET")
        return (f"BUY {qty} {sym} LIMIT {plan.entry:.2f} — bracket: stop-loss {plan.stop:.2f}, "
                f"take-profit {plan.target_1:.2f}, GTC",)
    if intraday:
        entry = (f"BUY {qty} {sym} MIS LIMIT {lim:.2f}" if triggered else
                 f"BUY {qty} {sym} MIS SL trigger {plan.entry:.2f} limit {lim:.2f}")
        return (entry,
                f"Cancel the entry if not filled by {cancel:%H:%M} IST",
                f"SL-M SELL {qty} {sym} trigger {plan.stop:.2f}",
                f"SELL {qty} {sym} LIMIT {plan.target_1:.2f}",
                f"Square off by {flat:%H:%M} IST")
    return (f"BUY {qty} {sym} CNC LIMIT {plan.entry:.2f}",
            f"GTT OCO {sym}: stop-loss {plan.stop:.2f} / target {plan.target_1:.2f}")


def is_quick(v: DeskVerdict) -> bool:
    """True when the verdict came from the daily plan's first pass: priced on the last close with no live quote, no
    earnings date and no robustness run, and never audited. Its ticket must not be used until a full check."""
    return bool(getattr(v, "first_pass", False)) and v.quote_as_of is None


# Every check run_checks emits, by what kind of reason it is (a derived view: Check carries no group field, so input
# hashes are unchanged). "market" and "odds" are the desk's advice and show amber; "data" BLOCKs mean the inputs are
# broken (re-check, no override); "limits" are the owner's own limits and the exchange's rules. Earnings never
# blocks: it is advice about the trade's odds (results may land inside it).
CHECK_GROUPS: dict[str, str] = {
    "regime": "market",
    "monte_carlo": "odds", "setup_history": "odds", "robustness": "odds", "earnings": "odds",
    "freshness": "data", "live_price": "data", "gap": "data",
    "size": "limits", "risk_governor": "limits", "daily_loss": "limits", "pdt": "limits", "ban_list": "limits",
    "stoploss_streak": "limits", "liquidity": "limits", "entry_window": "limits", "reward_risk": "limits",
    # shorts: a non-HOSTILE day is market advice; the edge gate and the volume behind the breakdown are odds; F&O
    # membership is "safety" (circuit-lock and auction risk): never overridable, like broken data
    "short_regime": "market", "short_edge": "odds", "short_monte_carlo": "odds", "relative_volume": "odds",
    "short_fno": "safety",
}
GROUPS = ("market", "odds", "data", "safety", "limits")
# Checks that are "safety" only on a short (a long's F&O ban is a WARN about derivatives, a limit).
SHORT_SAFETY = frozenset({"ban_list"})
ADVICE_GROUPS = frozenset({"market", "odds"})


def group_of(name: str, side: Side = Side.LONG) -> str:
    """The group of a check name for a plan of ``side``; an unknown name is treated as broken data (red, never advice,
    never overridable). On a short, ban_list is "safety"."""
    if side is Side.SHORT and name in SHORT_SAFETY:
        return "safety"
    return CHECK_GROUPS.get(name, "data")


REGIME_CHECKS = frozenset({"regime", "short_regime"})
REGIME_UNKNOWN = "Market regime unknown (index data unavailable)"


def _group(v: DeskVerdict, c: models.Check) -> str:
    """group_of, except that a regime check of a verdict whose regime is unknown (the index data failed) is "data":
    a broken input to re-check, not the desk's market advice."""
    if c.name in REGIME_CHECKS and regime_name(v.regime) is None:
        return "data"
    return group_of(c.name, v.plan.side)


def block_groups(v: DeskVerdict) -> dict[str, list[models.Check]]:
    """The verdict's BLOCK and WARN checks by group, in check order; PASS checks are left out. Keys are always
    "market", "odds", "data", "safety", "limits" in that order. An unknown regime's checks are "data"."""
    out: dict[str, list[models.Check]] = {g: [] for g in GROUPS}
    for c in v.checks:
        if c.status is not Status.PASS:
            out[_group(v, c)].append(c)
    return out


def safety_blocked(v: DeskVerdict) -> tuple[str, ...]:
    """The reasons of the verdict's "safety" BLOCKs: non-empty means the trade can't be taken at all (a short on a
    non-F&O or banned stock, or with either list unverified) — no override, anywhere."""
    return tuple(c.reason for c in block_groups(v)["safety"] if c.status is Status.BLOCK)


def data_broken(v: DeskVerdict) -> tuple[str, ...]:
    """The reasons of the verdict's "data" BLOCKs: non-empty means the inputs are broken and the verdict must be
    re-checked before anyone chooses it. An unknown regime's BLOCKs read as one REGIME_UNKNOWN reason."""
    out: list[str] = []
    for c in block_groups(v)["data"]:
        if c.status is not Status.BLOCK:
            continue
        reason = REGIME_UNKNOWN if c.name in REGIME_CHECKS else c.reason
        if reason not in out:
            out.append(reason)
    return tuple(out)


def sat_out_refusal(v: DeskVerdict, profile: MarketProfile) -> str | None:
    """Why the verdict is not a "desk sat out" (None when it is): a full-checked swing long whose ONLY BLOCK is the
    market regime (read from block_groups, never from text), and which the desk would have taken without that
    block (decide re-run with the regime check passed: the same price rules a TAKE meets)."""
    if v.plan.mode is not Mode.SWING:
        return "only swing verdicts are logged as sat out"
    if v.plan.side is not Side.LONG:
        return "only long verdicts are logged as sat out"
    if is_quick(v):
        return "a quick (first-pass) verdict is never logged: run a full check"
    if regime_name(v.regime) is None:  # missing regime data blocks fail-closed: that is not the desk choosing cash
        return "the regime is unknown (missing data), not a known regime the desk sat out"
    blocks = {g: [c.name for c in cs if c.status is Status.BLOCK] for g, cs in block_groups(v).items()}
    if blocks.pop("market") != ["regime"] or any(blocks.values()):
        return "the market regime is not the only BLOCK"
    try:
        now = datetime.fromisoformat(v.created_at)
    except (TypeError, ValueError):
        return "the verdict's time is unreadable"
    passed = [replace(c, status=Status.PASS) if c.name == "regime" else c for c in v.checks]
    if decide(v.plan, v.sizing, v.odds, passed, profile, v.current_price, now).action is not Action.TAKE:
        return "it would not have been a TAKE without the regime block"
    return None


def regime_override_note(v: DeskVerdict, profile: MarketProfile, *, notional: float | None = None) -> str:
    """The override header for a verdict the market regime blocks, with the held-out cost of going against it on
    this position (``notional``, default the desk's sizing); "" when the regime check is not a BLOCK. The regime
    name is the verdict's regime value (unknown: "an unknown regime (treated as HOSTILE)"). A (market, regime)
    with no measured held-out figure, or no positive notional, gets the sentence without the number."""
    if not any(c.name == "regime" and c.status is Status.BLOCK for c in v.checks):
        return ""
    regime = regime_name(v.regime)
    if regime is None:
        return "YOUR OVERRIDE — against an unknown regime (treated as HOSTILE)"
    head = f"YOUR OVERRIDE — against the market regime ({regime})"
    pct = REGIME_HELD_OUT_PCT.get((profile.code, regime))
    n = v.sizing.notional if notional is None else notional
    if pct is None or not (isinstance(n, (int, float)) and math.isfinite(n) and n > 0):
        return head
    return (f"{head}: the desk's held-out longs here averaged {signed_pct(pct)}% per trade ≈ a loss of "
            f"{profile.money(abs(pct) / 100 * n)} on this position")


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
    plan_d = asdict(plan)
    if plan.side is Side.LONG:  # long hashes stay what they were before shorts existed
        plan_d.pop("side", None)
    payload = {
        "plan": plan_d,
        "sizing": asdict(sizing),
        "odds": summary_fields(odds) if odds is not None else None,  # flat dataclass; mc_rs left out
        "checks": [asdict(c) for c in checks],
        "current_price": current_price,
    }
    blob = json.dumps(_plain(payload), sort_keys=True, default=str, allow_nan=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def short_edge_cap(plan: TradePlan, checks) -> str | None:
    """Why a short verdict is held at WAIT, or None when its short_edge check PASSes (checks.short_edge_check: the
    pooled ORB-short history is proven); always None for a long. A short with no short_edge result is not proven."""
    if plan.side is not Side.SHORT:
        return None
    edge = [c for c in checks if c.name == "short_edge"]
    if edge and all(c.status is Status.PASS for c in edge):
        return None
    return edge[0].reason if edge and edge[0].reason else SHORT_EDGE_UNPROVEN


def _decide_short(plan: TradePlan, sizing: Sizing, checks: tuple, profile: MarketProfile, current_price: float,
                  lines: tuple[str, ...]) -> tuple[Action, str, tuple[str, ...], float | None]:
    """(action, headline, ticket, trigger) for a short with a valid price and no BLOCK: the mirror of the long
    rules (sell only at or below the trigger, never below the sell limit), capped at WAIT by short_edge_cap."""
    m, sym = profile.money, f"{plan.symbol} SHORT"
    cap = short_edge_cap(plan, checks)
    if cap is not None:
        return Action.WAIT, f"WAIT {sym} — {cap}", (), None
    if current_price > plan.entry * (1 + 0.001):
        return (Action.WAIT, f"WAIT {sym} — sell short only below {m(plan.entry)} (now {m(current_price)})",
                ("IF TRIGGERED:",) + lines, plan.entry)
    if current_price < (limit := stop_limit(plan, profile)):
        return (Action.WAIT, f"WAIT {sym} — Price {m(current_price)} is below the stop-limit's limit {m(limit)} — "
                "the order would not fill", ("IF TRIGGERED:",) + lines, plan.entry)
    loss = loss_at_stop(plan, profile, sizing.qty, spread_per_side=sizing.spread_per_side)  # == card max loss
    return (Action.TAKE, f"TAKE {sym} — SELL {sizing.qty} @ {m(plan.entry)}, loss at stop ≈ {m(loss)} "
            "(a gap can lose more)", lines, None)


def decide(plan: TradePlan, sizing: Sizing, odds: Odds | None, checks, profile: MarketProfile,
           current_price: float, now: datetime) -> DeskVerdict:
    checks = tuple(checks)
    m, sym = profile.money, plan.symbol + (" SHORT" if plan.side is Side.SHORT else "")  # the badge in every headline
    lines = ticket_lines(plan, sizing, profile, current_price=current_price, day=market_day(now, profile))
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
    elif plan.side is Side.SHORT:
        action, headline, ticket, trigger = _decide_short(plan, sizing, checks, profile, current_price, lines)
    elif current_price < plan.entry * (1 - 0.001):
        action, trigger = Action.WAIT, plan.entry
        ticket = ("IF TRIGGERED:",) + lines
        headline = f"WAIT {sym} — buy only above {m(plan.entry)} (now {m(current_price)})"
    elif plan.mode is Mode.INTRADAY and current_price > (limit := stop_limit(plan, profile)):
        action, trigger = Action.WAIT, plan.entry
        ticket = ("IF TRIGGERED:",) + lines
        headline = (f"WAIT {sym} — Price {m(current_price)} is above the stop-limit's limit {m(limit)} — "
                    "the order would not fill")
    elif plan.mode is Mode.SWING and current_price > plan.entry * 1.01:
        action, trigger = Action.WAIT, plan.entry
        ticket = ("IF TRIGGERED:",) + lines
        headline = f"WAIT {sym} — price ran past entry; buy only on a pullback to {m(plan.entry)}"
    else:
        action, ticket = Action.TAKE, lines
        loss = loss_at_stop(plan, profile, sizing.qty, spread_per_side=sizing.spread_per_side)  # == card max loss
        headline = (f"TAKE {sym} — BUY {sizing.qty} @ {m(plan.entry)}, "
                    f"loss at stop ≈ {m(loss)} (a gap can lose more)")
    return DeskVerdict(action, headline, plan, sizing, odds, checks, ticket, trigger, current_price,
                       input_hash(plan, sizing, odds, checks, current_price), now.isoformat())
