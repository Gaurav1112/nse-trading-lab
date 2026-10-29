"""Shared, non-printing orchestration used by both the CLI and the dashboard page.

All I/O goes through the `loaders` module attribute so tests can monkeypatch it. Warnings are
handed to an optional `on_warning` callback instead of being printed. Nothing here places an order.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace

from nse_backtest import tape_monitor

from . import loaders
from .daily import DailyPlan, daily_plan, market_stats
from .intraday import orb_scan
from .models import DeskVerdict, Limits, Mode
from .service import check_trade

Warn = Callable[[str], None] | None
_RESOLVE = object()


def _warn(on_warning: Warn, msg: str) -> None:
    if on_warning is not None:
        on_warning(msg)


def limits_with_status(profile) -> tuple[Limits, bool]:
    """(limits, fell_back). fell_back is True when the settings file was unreadable or invalid."""
    return loaders.load_limits_with_status(profile)


def regime_of(regime_df, on_warning: Warn = None) -> str | None:
    """Tape regime name for a regime-index frame; None when it cannot be assessed (fail-closed downstream)."""
    try:
        tape = tape_monitor.assess_tape(regime_df)
        return tape.regime if tape else None
    except Exception as e:  # noqa: BLE001
        _warn(on_warning, f"regime unavailable: {e}")
        return None


def resolve_regime(profile, on_warning: Warn = None) -> str | None:
    """Load the regime index and classify it; None when either step fails."""
    try:
        df = loaders.load_regime(profile)
    except Exception as e:  # noqa: BLE001
        _warn(on_warning, f"regime unavailable: {e}")
        return None
    return regime_of(df, on_warning)


def context_for(profile, limits: Limits, symbol_df=None, regime: str | None = None):
    return loaders.build_context(profile, limits, symbol_df=symbol_df, regime=regime)


def load_frames(profile, symbols: Iterable[str], on_warning: Warn = None) -> dict:
    """Daily frames per symbol; a failed load maps to None (reported as unavailable, never dropped)."""
    out: dict = {}
    for sym in symbols:
        try:
            out[sym] = loaders.load_daily(profile, sym)
        except Exception as e:  # noqa: BLE001
            _warn(on_warning, f"no data for {sym}: {e}")
            out[sym] = None
    return out


def run_check(profile, symbol: str, mode: Mode, limits: Limits, *, entry=None, stop=None, target=None,
              regime=_RESOLVE, on_warning: Warn = None) -> DeskVerdict:
    """Full single-symbol check. Raises ValueError for bad input / unusable data."""
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("enter a symbol")
    daily = loaders.load_daily(profile, sym)
    df = loaders.load_intraday(profile, sym) if mode is Mode.INTRADAY else daily
    if regime is _RESOLVE:
        regime = resolve_regime(profile, on_warning)
    ctx = context_for(profile, limits, daily, regime)
    nxt, checked = loaders.next_earnings(profile, sym)
    ctx = replace(ctx, next_earnings=nxt, earnings_checked=checked)
    return check_trade(df, sym, profile, mode, limits, ctx, entry=entry, stop=stop, target=target)


def run_daily_plan(profile, limits: Limits, symbols: Iterable[str], on_warning: Warn = None) -> DailyPlan:
    frames = load_frames(profile, symbols, on_warning)
    regime_df = loaders.load_regime(profile)
    ctx = context_for(profile, limits, regime=regime_of(regime_df, on_warning))
    return daily_plan(profile, frames, regime_df, limits, ctx,
                      earnings_lookup=lambda s: loaders.next_earnings(profile, s))


def run_intraday(profile, limits: Limits, symbols: Iterable[str], regime=_RESOLVE,
                 on_warning: Warn = None) -> tuple[list[DeskVerdict], list[tuple[str, str]]]:
    """Opening-range scan over 15m frames; (verdicts, failures) with load failures included."""
    if regime is _RESOLVE:
        regime = resolve_regime(profile, on_warning)
    ctx = context_for(profile, limits, regime=regime)
    frames15, load_failures, per_symbol = {}, [], {}
    for s in symbols:
        try:
            frames15[s] = loaders.load_intraday(profile, s)
        except Exception as e:  # noqa: BLE001
            load_failures.append((s, str(e)))
            continue
        try:  # daily bars give the liquidity (turnover) and gap checks something to read
            turnover, gap = market_stats(loaders.load_daily(profile, s))
            per_symbol[s] = replace(ctx, turnover_20d=turnover, today_gap_pct=gap)
        except Exception as e:  # noqa: BLE001 - liquidity then stays unknown, which the desk WARNs on
            _warn(on_warning, f"no daily data for {s}: {e}")
    verdicts, failures = orb_scan(profile, frames15, limits, ctx, ctx_by_symbol=per_symbol)
    return verdicts, load_failures + list(failures)
