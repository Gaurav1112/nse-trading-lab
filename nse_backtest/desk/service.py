"""check_trade: stock frame in, TAKE / WAIT / SKIP verdict with hand-typed ticket out. Nothing is placed."""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pandas as pd

from nse_backtest.picker_replay import spread_pct_for_df

from . import timemachine
from .audit import append_verdict, data_dir
from .checks import CheckContext, run_checks, stoploss_streak
from .costs import DEFAULT_SPREAD, round_trip_cost
from .markets import MarketProfile
from .models import DeskVerdict, LiveQuote, Limits, Mode
from .plan import build_plan
from .sizing import size_position
from .verdict import decide

log = logging.getLogger(__name__)


def check_trade(df: pd.DataFrame, symbol: str, profile: MarketProfile, mode: Mode, limits: Limits,
                ctx: CheckContext, *, entry: float | None = None, stop: float | None = None,
                target: float | None = None, robustness: bool = True, replays: int = 10_000,
                audit: bool = True, audit_path: Path | None = None,
                quote: LiveQuote | None = None) -> DeskVerdict:
    """`quote` is the live price; without one the verdict uses the plan's last close and the
    live_price check WARNs. The context's live-price fields always follow `quote`, never the caller."""
    plan = build_plan(df, symbol, mode, profile, entry=entry, stop=stop, target=target)
    ctx = replace(ctx, price_is_live=quote is not None, quote_as_of=quote.as_of if quote is not None else None)
    current_price = quote.price if quote is not None else plan.last_close
    halve = stoploss_streak(ctx.journal, profile.code)
    spread = spread_pct_for_df(df) if mode is Mode.SWING else DEFAULT_SPREAD
    sizing = size_position(plan, limits, profile, spread_per_side=spread, halve=halve)
    q = max(sizing.qty, 1)
    cps = round_trip_cost(profile, mode, plan.entry, plan.target_1, q, spread) / q
    try:
        odds = timemachine.odds(df, plan, cost_per_share=cps, replays=replays, robustness=robustness)
    except ValueError as e:  # no odds -> the monte_carlo check BLOCKs
        log.warning("odds could not be computed for %s: %s", symbol, e)
        odds = None
    checks = run_checks(plan, sizing, odds, ctx, current_price=current_price)
    v = decide(plan, sizing, odds, checks, profile, current_price, ctx.now)
    if audit:
        append_verdict(v, audit_path if audit_path is not None else data_dir() / "desk_audit.jsonl")
    return v
