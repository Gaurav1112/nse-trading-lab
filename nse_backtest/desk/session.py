"""Shared, non-printing orchestration used by both the CLI and the dashboard page.

All I/O goes through the `loaders` module attribute so tests can monkeypatch it. Warnings are
handed to an optional `on_warning` callback instead of being printed. Nothing here places an order.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from nse_backtest import tape_monitor

from . import calendars as cal
from . import loaders
from . import scoreboard_io
from . import shortcheck
from .audit import data_dir
from .daily import DailyPlan, daily_plan, plan_entry, today_gap_pct, turnover_20d
from .intraday import orb_scan
from .markets import universe
from .models import Action, DeskVerdict, Limits, Mode, ShortEdge, Side
from .scoreboard_io import ScoreboardResult  # noqa: F401 - re-exported for the CLI and page
from .service import check_trade
from .verdict import ADVICE_GROUPS, group_of

Warn = Callable[[str], None] | None
_RESOLVE = object()


def _warn(on_warning: Warn, msg: str) -> None:
    if on_warning is not None:
        on_warning(msg)


def limits_with_status(profile) -> tuple[Limits, bool]:
    """(limits, fell_back). fell_back is True when the settings file was unreadable or invalid."""
    return loaders.load_limits_with_status(profile)


_REGIME_MAX_AGE_DAYS = 3


_REGIME_MAX_MISSED_SESSIONS = 2  # the index may lag one session behind the stocks


def _regime_stale(regime_df, profile, now: datetime | None = None) -> str | None:
    """Warning text when the regime index's last bar is too old for market-today, else None.

    Covered years: stale once 2 or more finished trading sessions are missing after the last bar (sessions
    strictly between the last bar and today, plus today's once it has closed).
    Years without a holiday calendar keep the 3-calendar-day rule and say so in the message.
    A last bar after market-today is reported as future-dated, never passed silently.
    """
    try:
        last = pd.Timestamp(regime_df.index[-1]).date()
    except Exception:  # noqa: BLE001 - unreadable dates: treat as stale
        return "Regime index data is stale (last bar unknown)"
    tz = ZoneInfo(profile.tz)
    if now is not None and now.tzinfo is not None:
        now = now.astimezone(tz)
    today = (now or datetime.now(tz)).date()
    if last > today:
        return f"Regime index data has a future-dated last bar ({last})"
    if last == today:
        return None
    sfx = ""
    year = cal.first_uncovered_year(profile, last, today)
    if year is not None:
        stale = (today - last) > timedelta(days=_REGIME_MAX_AGE_DAYS)
        sfx = f" (holiday calendar not available for {year})"
    else:
        missed = cal.trading_days_between(profile, last, today)
        if now is None:
            now = datetime.now(tz)
        if cal.is_trading_day(profile, today) and now.time() >= cal.session_close_for(profile, today):
            missed += 1  # today's session has finished too (last < today here)
        stale = missed >= _REGIME_MAX_MISSED_SESSIONS
    return f"Regime index data is stale (last bar {last}){sfx}" if stale else None


def assess(regime_df, on_warning: Warn = None, profile=None):
    """TapeAssessment for a regime-index frame, or None when missing, stale or unclassifiable."""
    if regime_df is None:
        return None
    if profile is not None:
        stale = _regime_stale(regime_df, profile)
        if stale:
            _warn(on_warning, stale)
            return None
    try:
        return tape_monitor.assess_tape(regime_df)
    except Exception as e:  # noqa: BLE001
        _warn(on_warning, f"regime unavailable: {e}")
        return None


def regime_of(regime_df, on_warning: Warn = None, profile=None) -> str | None:
    """Tape regime name for a regime-index frame; None when it cannot be assessed (fail-closed downstream)."""
    tape = assess(regime_df, on_warning, profile)
    return tape.regime if tape else None


def resolve_tape(profile, on_warning: Warn = None):
    """(regime_df, tape). Load failures and stale data reach on_warning; tape is None then."""
    try:
        df = loaders.load_regime(profile)
    except Exception as e:  # noqa: BLE001
        _warn(on_warning, f"regime unavailable: {e}")
        return None, None
    return df, assess(df, on_warning, profile)


def resolve_regime(profile, on_warning: Warn = None) -> str | None:
    """Load the regime index and classify it; None when either step fails or the data is stale."""
    tape = resolve_tape(profile, on_warning)[1]
    return tape.regime if tape else None


def quote_for(profile, symbol: str, on_warning: Warn = None):
    q = loaders.live_quote(profile, symbol)
    if q is None:
        _warn(on_warning, f"live price unavailable for {symbol} — using the last close")
    return q


def context_for(profile, limits: Limits, symbol_df=None, regime: str | None = None):
    return loaders.build_context(profile, limits, symbol_df=symbol_df, regime=regime)


def load_frames(profile, symbols: Iterable[str], on_warning: Warn = None) -> dict:
    """Daily frames per symbol; a failed load maps to None (reported as unavailable, never dropped)."""
    symbols = list(symbols)
    try:
        frames, errors = loaders.load_daily_many(profile, symbols)
    except Exception as e:  # noqa: BLE001 - the batch never raises per symbol; this is a last guard
        frames, errors = {}, {s: str(e) for s in symbols}
    out: dict = {}
    for sym in symbols:
        if sym in frames:
            out[sym] = frames[sym]
        else:
            _warn(on_warning, f"no data for {sym}: {errors.get(sym, 'unavailable')}")
            out[sym] = None
    return out


def shorts_today(profile, regime) -> bool:
    """ORB breakdown shorts are offered only on HOSTILE NSE days."""
    return profile.code == "NSE" and isinstance(regime, str) and regime.strip().upper() == "HOSTILE"


_REGIME_DAYS_BACK = 100  # calendar days of past regimes the short odds need (15-minute bars reach back ~60 days)


def _market_today(profile, now: datetime | None = None) -> date:
    tz = ZoneInfo(profile.tz)
    if now is None:
        return datetime.now(tz).date()
    return now.astimezone(tz).date() if now.tzinfo is not None else now.date()


def short_edge_today(profile, now: datetime | None = None) -> tuple[ShortEdge | None, str]:
    """Today's pooled ORB-short edge from the daily cache: (edge, "") or (None, why). Never recomputed here: a
    missing or unreadable cache fails closed (short_edge WARNs "edge check unavailable" and caps at WAIT)."""
    if profile.code != "NSE":
        return None, "shorts are NSE only"
    try:
        return shortcheck.load_edge(data_dir() / shortcheck.CACHE_FILE, profile, _market_today(profile, now))
    except Exception as e:  # noqa: BLE001 - any read problem is "unavailable", never a pass
        return None, f"cache unreadable ({type(e).__name__}): run shortcheck again"


def regime_history(profile, days: Iterable[date], on_warning: Warn = None) -> dict[date, str] | None:
    """shortcheck.regime_by_day over the regime index for ``days``; None (with a warning) when the index cannot be
    loaded — the short history then has no HOSTILE day to replay."""
    try:
        df = loaders.load_regime(profile)
    except Exception as e:  # noqa: BLE001
        _warn(on_warning, f"regime history unavailable for the short odds: {e}")
        return None
    return shortcheck.regime_by_day(df, days)


def _recent_days(profile, now: datetime | None) -> list[date]:
    today = _market_today(profile, now)
    return [d.date() for d in pd.bdate_range(today - timedelta(days=_REGIME_DAYS_BACK), today)]


def _with_fno(ctx, profile, on_warning: Warn):
    """The short-side context: the F&O list, today's pooled edge (from the daily cache) and the regime of each recent
    day (for the short odds)."""
    fno = loaders.fno_underlyings(profile, ctx.now)
    if fno is None:
        _warn(on_warning, "NSE F&O list unavailable: could not verify F&O eligibility, so every short is blocked")
    edge, why = short_edge_today(profile, ctx.now)
    if edge is None:
        _warn(on_warning, f"short edge check unavailable ({why}): every short stays at most WAIT")
    return replace(ctx, fno=fno, short_edge=edge, short_edge_why=why,
                   regime_by_day=regime_history(profile, _recent_days(profile, ctx.now), on_warning))


@dataclass(frozen=True)
class ShortcheckResult:
    edge: ShortEdge
    day: date
    symbols: int | None  # eligible symbols whose 15-minute bars were replayed (None: a cache that does not say)
    cached: bool  # True: read from today's cache, not recomputed
    failures: tuple[tuple[str, str], ...] = ()


def run_shortcheck(profile, *, refresh: bool = False, on_warning: Warn = None, on_progress=None,
                   now: datetime | None = None) -> ShortcheckResult:
    """Today's pooled ORB-short edge over the eligible universe (the desk's universe in NSE's F&O list), HOSTILE
    days only, at 2x slippage, cached for the day in the data dir. Uses today's cache unless ``refresh``. Raises
    ValueError when it cannot be computed honestly (not NSE, no F&O list, no regime index, no bars at all); nothing
    is cached then, so the gate stays closed."""
    if profile.code != "NSE":
        raise ValueError("ORB shorts are NSE only")
    today = _market_today(profile, now)
    path = data_dir() / shortcheck.CACHE_FILE
    if not refresh:
        edge, _ = shortcheck.load_edge(path, profile, today)
        if edge is not None:
            return ShortcheckResult(edge, today, shortcheck.cached_symbols(path), True)
    fno = loaders.fno_underlyings(profile, now)
    if fno is None:
        raise ValueError("could not verify F&O eligibility: the pooled short history needs NSE's F&O list")
    syms = [s for s in universe(profile) if s in fno]
    if not syms:
        raise ValueError("no symbol of the desk's universe is in NSE's F&O list")
    try:
        index_df = loaders.load_regime(profile)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"regime index unavailable, so no day can be called HOSTILE: {e}") from e
    frames, failures = {}, []
    for n, sym in enumerate(syms, 1):
        if on_progress is not None:
            try:
                on_progress(n, len(syms), sym)
            except Exception:  # noqa: BLE001 - progress display only
                on_progress = None
        try:
            frames[sym] = loaders.load_intraday(profile, sym)
        except Exception as e:  # noqa: BLE001 - a missing symbol adds no trades (it can only make proving harder)
            failures.append((sym, str(e)))
            _warn(on_warning, f"no intraday data for {sym}: {e}")
    if not frames:
        raise ValueError("no intraday bars for any eligible symbol")
    days = sorted({d.date() for f in frames.values() for d in pd.unique(f.index.normalize())})
    edge = shortcheck.pooled_short_edge(profile, frames, shortcheck.regime_by_day(index_df, days))
    shortcheck.save_edge(path, profile, today, edge, symbols=len(frames), now=now)
    return ShortcheckResult(edge, today, len(frames), False, tuple(failures))


def run_check(profile, symbol: str, mode: Mode, limits: Limits, *, entry=None, stop=None, target=None,
              regime=_RESOLVE, on_warning: Warn = None, desk_entry: bool = False,
              side: Side = Side.LONG) -> DeskVerdict:
    """Full single-symbol check. Raises ValueError for bad input / unusable data. With ``desk_entry`` and no
    ``entry``, a swing check uses the daily plan's own entry (the breakout above the 20-day high; the close when
    already at it) instead of the last close."""
    sym = symbol.strip().upper()
    if not sym:
        raise ValueError("enter a symbol")
    daily = loaders.load_daily(profile, sym)
    if desk_entry and entry is None and mode is Mode.SWING:
        try:
            entry = plan_entry(daily)
        except Exception:  # noqa: BLE001 - unusable bars: build_plan below raises the real reason
            entry = None
    df = loaders.load_intraday(profile, sym) if mode is Mode.INTRADAY else daily
    if regime is _RESOLVE:
        regime = resolve_regime(profile, on_warning)
    ctx = context_for(profile, limits, daily, regime)
    nxt, checked = loaders.next_earnings(profile, sym)
    quote = quote_for(profile, sym, on_warning)
    ctx = replace(ctx, next_earnings=nxt, earnings_checked=checked, today_gap_pct=today_gap_pct(daily, quote, ctx.now, profile))
    if Side(side) is Side.SHORT:
        return check_trade(df, sym, profile, mode, limits, _with_fno(ctx, profile, on_warning), entry=entry,
                           stop=stop, target=target, quote=quote, side=Side.SHORT)
    return check_trade(df, sym, profile, mode, limits, ctx, entry=entry, stop=stop, target=target, quote=quote)


def run_daily_plan(profile, limits: Limits, symbols: Iterable[str], on_warning: Warn = None, *,
                   regime=_RESOLVE, tape=None) -> DailyPlan:
    """Daily plan. Pass the page header's `regime`/`tape` so header and plan agree; otherwise the
    regime is resolved here, once, and handed to daily_plan (which then does not re-assess it)."""
    frames = load_frames(profile, symbols, on_warning)
    if regime is _RESOLVE:
        regime_df, tape = resolve_tape(profile, on_warning)
        regime = tape.regime if tape else None
    else:
        regime_df = None
        try:
            regime_df = loaders.load_regime(profile)
        except Exception as e:  # noqa: BLE001 - only the flip conditions need it
            _warn(on_warning, f"regime index unavailable for flip conditions: {e}")
    ctx = context_for(profile, limits, regime=regime)
    return daily_plan(profile, frames, regime_df, limits, ctx,
                      audit_path=data_dir() / scoreboard_io.AUDIT_FILE,  # its regime sat-outs are scored too
                      earnings_lookup=lambda s: loaders.next_earnings(profile, s),
                      quote_lookup=lambda s: quote_for(profile, s, on_warning),
                      regime=regime, tape=tape)


def run_intraday(profile, limits: Limits, symbols: Iterable[str], regime=_RESOLVE,
                 on_warning: Warn = None, on_progress=None
                 ) -> tuple[list[DeskVerdict], list[tuple[str, str]]]:
    """Opening-range scan over 15m frames; (verdicts, failures) with load failures included. Every TAKE is
    logged to the audit log, so the scoreboard scores it. ``on_progress(done, total, symbol)`` is called after
    each symbol is loaded (a failing callback never stops the scan). On a HOSTILE NSE day every symbol is also
    checked as an ORB breakdown short (shorts_today)."""
    if regime is _RESOLVE:
        regime = resolve_regime(profile, on_warning)
    ctx = context_for(profile, limits, regime=regime)
    sides = (Side.LONG,)
    if shorts_today(profile, regime):
        ctx, sides = _with_fno(ctx, profile, on_warning), (Side.LONG, Side.SHORT)
    frames15, load_failures, per_symbol, quotes = {}, [], {}, {}
    symbols = list(symbols)
    for n, s in enumerate(symbols, 1):
        if on_progress is not None:
            try:
                on_progress(n, len(symbols), s)
            except Exception:  # noqa: BLE001 - progress display only
                on_progress = None
        try:
            frames15[s] = loaders.load_intraday(profile, s)
        except Exception as e:  # noqa: BLE001
            load_failures.append((s, str(e)))
            continue
        quotes[s] = quote_for(profile, s, on_warning)
        try:  # daily bars give the liquidity (turnover) and gap checks something to read
            daily = loaders.load_daily(profile, s)
            per_symbol[s] = replace(ctx, turnover_20d=turnover_20d(daily),
                                    today_gap_pct=today_gap_pct(daily, quotes[s], ctx.now, profile))
        except Exception as e:  # noqa: BLE001 - liquidity then stays unknown, which the desk WARNs on
            _warn(on_warning, f"no daily data for {s}: {e}")
    kw = {"sides": sides} if len(sides) > 1 else {}
    verdicts, failures = orb_scan(profile, frames15, limits, ctx, ctx_by_symbol=per_symbol, quotes=quotes,
                                  audit_path=data_dir() / scoreboard_io.AUDIT_FILE, **kw)  # scan TAKEs are scored
    return verdicts, load_failures + list(failures)


def expectancy(v: DeskVerdict) -> float | None:
    e = v.odds.mc_expectancy_r if v.odds is not None else None
    return e if isinstance(e, (int, float)) and e == e and abs(e) != float("inf") else None


def candidate_rank(v: DeskVerdict) -> tuple:
    """Sort key for "your call": TAKE, then WAIT, then SKIPs blocked only by the market or the desk's own odds
    (verdict.block_groups' "market" and "odds"), then every other SKIP (data, limits, exchange). Within
    a group, the highest Monte Carlo expectancy first; no odds last."""
    blocks = {c.name for c in v.blocks()}
    if v.action is Action.TAKE:
        group = 0
    elif v.action is Action.WAIT:
        group = 1
    elif blocks and all(group_of(b) in ADVICE_GROUPS for b in blocks):
        group = 2
    else:
        group = 3
    e = expectancy(v)
    return (group, e is None, -(e or 0.0))


def merge_candidates(plan: DailyPlan | None, scan_verdicts: Iterable[DeskVerdict] = (), *,
                     include_others: bool = True) -> list[DeskVerdict]:
    """Every verdict from the daily plan (TAKE, WATCH and, with ``include_others``, every other verdict it
    computed) and the intraday scan, one per (symbol, mode, side) (the plan's lists win, then the scan, then
    ``others``), ranked by candidate_rank. The plan's skipped symbols have no verdict and are left to the
    caller."""
    lists = (plan.take + plan.watch) if plan is not None else ()
    extra = plan.others if plan is not None and include_others else ()
    seen: set = set()
    out: list[DeskVerdict] = []
    for v in [*lists, *scan_verdicts, *extra]:
        k = (v.plan.symbol, v.plan.mode, v.plan.side)
        if k not in seen:
            seen.add(k)
            out.append(v)
    return sorted(out, key=candidate_rank)


def run_scoreboard(market: str | None = None, *, now: datetime | None = None, audit_path: Path | None = None,
                   cache_path: Path | None = None, on_warning: Warn = None,
                   load_daily=None, load_intraday=None) -> ScoreboardResult:
    """Paper record of every logged TAKE (market None or "ALL" = all markets; unknown market -> ValueError).
    Bars come from loaders.load_daily / loaders.load_intraday unless fakes are passed; resolved final
    outcomes are cached in desk_scoreboard.json so they are never refetched."""
    return scoreboard_io.build_scoreboard(
        market, now=now or datetime.now(timezone.utc),
        audit_path=Path(audit_path) if audit_path is not None else data_dir() / scoreboard_io.AUDIT_FILE,
        cache_path=Path(cache_path) if cache_path is not None else data_dir() / scoreboard_io.CACHE_FILE,
        on_warning=on_warning,
        load_daily=load_daily if load_daily is not None else loaders.load_daily,
        load_intraday=load_intraday if load_intraday is not None else loaders.load_intraday)
