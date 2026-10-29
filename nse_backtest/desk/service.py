"""check_trade: stock frame in, TAKE / WAIT / SKIP verdict with hand-typed ticket out. Nothing is placed."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nse_backtest.picker_replay import spread_pct_for_df

from . import timemachine
from .audit import append_verdict, data_dir
from .checks import CheckContext, run_checks, stoploss_streak
from .costs import DEFAULT_SPREAD, side_cost
from .markets import MarketProfile
from .models import DeskVerdict, LiveQuote, Limits, Mode, Side
from .plan import build_plan, or_relative_volume
from .sizing import size_position
from .verdict import decide

log = logging.getLogger(__name__)


QUOTE_MAX_AGE = timedelta(minutes=5)


def _quote_fields(quote: LiveQuote | None, created: datetime, used_at: datetime | None, profile) -> dict:
    """quote_live and quote_as_of for the verdict. Live = the quote is from created_at's market session (same
    market date, at or after the open) and at most 5 minutes old when used."""
    if quote is None or not isinstance(quote.as_of, datetime) or quote.as_of.tzinfo is None:
        return {"quote_live": False, "quote_as_of": None}
    tz = ZoneInfo(profile.tz)
    as_of = quote.as_of.astimezone(tz)
    made = created.astimezone(tz) if created.tzinfo is not None else created.replace(tzinfo=tz)
    used = (used_at or datetime.now(tz)).astimezone(tz)
    age = used - as_of
    live = (as_of.date() == made.date() and as_of.time() >= profile.session_open
            and timedelta(minutes=-1) <= age <= QUOTE_MAX_AGE)
    return {"quote_live": live, "quote_as_of": as_of.isoformat()}


def check_trade(df: pd.DataFrame, symbol: str, profile: MarketProfile, mode: Mode, limits: Limits,
                ctx: CheckContext, *, entry: float | None = None, stop: float | None = None,
                target: float | None = None, robustness: bool = True, replays: int = 10_000,
                audit: bool = True, audit_path: Path | None = None,
                quote: LiveQuote | None = None, used_at: datetime | None = None,
                side: Side = Side.LONG) -> DeskVerdict:
    """`quote` is the live price; without one the verdict uses the plan's last close and the
    live_price check WARNs. The context's live-price fields always follow `quote`, never the caller.
    `used_at` (default: the wall clock) is when the quote is used: the verdict's quote_live is True only for a
    quote from created_at's session at most 5 minutes old then (the scoreboard trusts the price only then).
    ``side=Side.SHORT`` checks an intraday ORB breakdown short: no long odds (the short_edge gate stands in) and the
    opening bar's relative volume measured from ``df``; ``ctx.fno`` must carry the F&O list (None BLOCKs)."""
    side = Side(side)
    plan = build_plan(df, symbol, mode, profile, entry=entry, stop=stop, target=target, side=side)
    ctx = replace(ctx, price_is_live=quote is not None, quote_as_of=quote.as_of if quote is not None else None)
    if side is Side.SHORT:
        ctx = replace(ctx, or_rel_volume=or_relative_volume(df))
    current_price = quote.price if quote is not None else plan.last_close
    halve = stoploss_streak(ctx.journal, profile.code)
    spread = spread_pct_for_df(df) if mode is Mode.SWING else DEFAULT_SPREAD
    sizing = size_position(plan, limits, profile, spread_per_side=spread, halve=halve)
    q = max(sizing.qty, 1)
    cps = side_cost(profile, mode, plan.entry, plan.target_1, q, spread, side=side) / q
    if side is Side.SHORT:
        odds = None  # the Time Machine replays longs only; the short_edge gate (H5b) is the short's odds
    else:
        try:
            odds = timemachine.odds(df, plan, cost_per_share=cps, replays=replays, robustness=robustness)
        except ValueError as e:  # no odds -> the monte_carlo check BLOCKs
            log.warning("odds could not be computed for %s: %s", symbol, e)
            odds = None
    checks = run_checks(plan, sizing, odds, ctx, current_price=current_price)
    regime = (ctx.regime.strip().upper() or None) if isinstance(ctx.regime, str) else None
    v = replace(decide(plan, sizing, odds, checks, profile, current_price, ctx.now), cost_per_share=cps,
                regime=regime, **_quote_fields(quote, ctx.now, used_at, profile))
    if audit:
        append_verdict(v, audit_path if audit_path is not None else data_dir() / "desk_audit.jsonl")
    return v
