"""Daily plan: TAKE list, WATCH list (with triggers) and the regime flip conditions."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from nse_backtest import tape_monitor

from .checks import CheckContext
from .models import Action, DeskVerdict, Limits, Mode
from .service import check_trade


@dataclass(frozen=True)
class DailyPlan:
    market: str
    regime: str | None
    regime_text: str
    flip_conditions: tuple[str, ...]
    take: tuple[DeskVerdict, ...]
    watch: tuple[DeskVerdict, ...]
    skipped: tuple[tuple[str, str], ...]
    unavailable: tuple[str, ...]


def market_stats(df) -> tuple[float | None, float | None]:
    """(turnover_20d, today_gap_pct); None for anything that cannot be computed. Never raises."""
    turnover = gap = None
    try:
        t = float((df["Close"] * df["Volume"]).tail(20).median())
        turnover = t if math.isfinite(t) else None
    except Exception:  # noqa: BLE001
        pass
    try:
        if len(df) >= 2:
            g = (float(df["Open"].iloc[-1]) / float(df["Close"].iloc[-2]) - 1) * 100
            gap = g if math.isfinite(g) else None
    except Exception:  # noqa: BLE001
        pass
    return turnover, gap


def flip_conditions(regime_df: pd.DataFrame | None, profile) -> tuple[str, ...]:
    if regime_df is None or len(regime_df) < 200:
        return ("Regime index data unavailable",)
    close = regime_df["Close"]
    cur = float(close.iloc[-1])
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    e50, e200 = float(ema50.iloc[-1]), float(ema200.iloc[-1])
    ret = (cur / float(close.iloc[-61]) - 1.0) * 100 if len(close) >= 61 else 0.0
    slope = (e200 / float(ema200.iloc[-21]) - 1.0) * 100 if len(ema200) >= 21 else 0.0
    out: list[str] = []
    if not cur > e50:
        out.append(f"{profile.regime_index} close above 50-EMA (now {cur:,.0f} vs {e50:,.0f})")
    if not e50 > e200:
        out.append("50-EMA above 200-EMA (golden cross)")
    if not ret > 5.0:
        out.append(f"60-day return above +5% (now {ret:+.1f}%)")
    if not slope > 0.5:
        out.append(f"200-EMA rising > +0.5% per 20 days (now {slope:+.2f}%)")
    return tuple(out)


def breakout_entry(df) -> float | None:
    trigger = float(df["High"].tail(20).max())
    close = float(df["Close"].iloc[-1])
    # at/through the 20-day high, or closing at a 20-day closing high (intraday highs run ~1% above closes)
    if close >= trigger * 0.999 or close >= float(df["Close"].tail(20).max()):
        return None
    if close < trigger / 1.05:
        return float("nan")
    return trigger * 1.001


def _exp(v: DeskVerdict) -> float:
    return v.odds.mc_expectancy_r if v.odds is not None else float("-inf")


def _only_regime_block(v: DeskVerdict) -> bool:
    return {c.name for c in v.blocks()} == {"regime"}


def _watch_eligible(v: DeskVerdict) -> bool:
    return v.action is Action.WAIT or (v.action is Action.SKIP and _only_regime_block(v))


def _closeness(v: DeskVerdict) -> float:
    return (v.trigger or v.plan.entry) / v.current_price


def daily_plan(profile, frames: dict[str, pd.DataFrame], regime_df, limits: Limits, ctx_base: CheckContext,
               *, top_take: int = 3, top_watch: int = 5, replays: int = 3000,
               audit_path: Path | None = None,
               earnings_lookup: Callable[[str], tuple[date | None, bool]] | None = None) -> DailyPlan:
    tape = tape_monitor.assess_tape(regime_df)
    regime = tape.regime if tape else None
    regime_text = tape.recommendation if tape else "Regime unknown — treat as HOSTILE"
    ctx = replace(ctx_base, regime=regime)

    skipped: list[tuple[str, str]] = []
    unavailable: list[str] = []
    takes: list[DeskVerdict] = []
    watch: list[DeskVerdict] = []
    entries: dict[str, float | None] = {}
    sym_ctx: dict[str, CheckContext] = {}

    for sym, df in frames.items():
        if df is None or len(df) < 220:
            unavailable.append(sym)
            continue
        try:
            e = breakout_entry(df)
        except Exception as err:  # noqa: BLE001
            skipped.append((sym, str(err)))
            continue
        if e is not None and np.isnan(e):
            skipped.append((sym, "not near a setup"))
            continue
        turnover, gap = market_stats(df)
        c = replace(ctx, turnover_20d=turnover, today_gap_pct=gap)
        try:
            v = check_trade(df, sym, profile, Mode.SWING, limits, c, entry=e,
                            robustness=False, replays=replays, audit=False)
        except ValueError as err:
            skipped.append((sym, str(err)))
            continue
        entries[sym], sym_ctx[sym] = e, c
        if v.action is Action.TAKE:
            takes.append(v)
        elif _watch_eligible(v):
            watch.append(v)
        else:
            skipped.append((sym, v.headline))

    takes.sort(key=_exp, reverse=True)
    final_take: list[DeskVerdict] = []
    for v in takes[:top_take]:
        sym = v.plan.symbol
        c2 = sym_ctx[sym]
        if earnings_lookup is not None:  # only for final candidates: one network call each
            try:
                nxt, checked = earnings_lookup(sym)
            except Exception:  # noqa: BLE001 - unknown earnings WARNs, never passes
                nxt, checked = None, False
            c2 = replace(c2, next_earnings=nxt, earnings_checked=checked)
        try:
            v2 = check_trade(frames[sym], sym, profile, Mode.SWING, limits, c2, entry=entries[sym],
                             robustness=True, replays=replays, audit=True, audit_path=audit_path)
        except ValueError as err:
            skipped.append((sym, str(err)))
            continue
        if v2.action is Action.TAKE:
            final_take.append(v2)
        elif _watch_eligible(v2):
            watch.append(v2)
        else:
            skipped.append((sym, v2.headline))
    for v in takes[top_take:]:
        skipped.append((v.plan.symbol, f"{v.headline} (beyond top {top_take})"))

    final_take.sort(key=_exp, reverse=True)
    watch.sort(key=_closeness)
    for v in watch[top_watch:]:
        skipped.append((v.plan.symbol, f"{v.headline} (beyond top {top_watch} watch)"))
    return DailyPlan(profile.code, regime, regime_text, flip_conditions(regime_df, profile),
                     tuple(final_take), tuple(watch[:top_watch]), tuple(skipped), tuple(unavailable))
