"""Daily plan: TAKE list, WATCH list (with triggers) and the regime flip conditions."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from nse_backtest import tape_monitor

from . import audit, strength
from .calendars import is_trading_day, session_close_for
from .checks import CheckContext
from .models import Action, DeskVerdict, LiveQuote, Limits, Mode
from .service import check_trade
from .verdict import sat_out_refusal


@dataclass(frozen=True)
class DailyPlan:
    market: str
    regime: str | None
    regime_text: str
    flip_conditions: tuple[str, ...]
    take: tuple[DeskVerdict, ...]
    watch: tuple[DeskVerdict, ...]
    skipped: tuple[tuple[str, str], ...]  # symbols with no verdict at all: (symbol, why)
    unavailable: tuple[str, ...]
    # Every other computed verdict (TAKEs beyond top_take, watch overflow, blocked and far symbols), numbers kept.
    # First-pass ones are quick (verdict.is_quick) and never audited.
    others: tuple[DeskVerdict, ...] = ()
    # "Holding up": (symbol, 60-day return minus the index's, in % points), best first — ready for when the regime
    # turns, never a trade. Empty with a reason in holding_up_note when the index can't anchor it.
    holding_up: tuple[tuple[str, float], ...] = ()
    holding_up_note: str | None = None
    # Why the "desk sat out" record could not be written for this plan (None: written, or nothing to write).
    sat_out_note: str | None = None
    # LAST field: the skips whose exception is not a ValueError (the desk's "unusable data" signal) — a code bug,
    # never a quiet day. Also in ``skipped``; shown in red by the page and the CLI (plan_errors_line).
    errors: tuple[tuple[str, str], ...] = ()


def plan_errors_line(plan) -> str | None:
    """The red line for a plan whose checks hit unexpected errors (DailyPlan.errors), else None."""
    errs = tuple(getattr(plan, "errors", ()) or ())
    if not errs:
        return None
    sym, why = errs[0]
    return f"The plan hit errors for {len(errs)} symbol(s): {sym}: {why}"


def turnover_20d(df) -> float | None:
    """Median Close*Volume over the last 20 bars; None when it cannot be computed. Never raises."""
    try:
        t = float((df["Close"] * df["Volume"]).tail(20).median())
        return t if math.isfinite(t) else None
    except Exception:  # noqa: BLE001
        return None


def today_gap_pct(daily_df, quote: LiveQuote | None, now, profile=None) -> float | None:
    """(today's open / previous completed daily close - 1) * 100, from a live quote.

    None without a quote, and None unless the quote's own time (in the market timezone of `now`)
    falls on market-today: a previous session's open is not today's gap. With a `profile`, also None
    when market-today is not a trading day on the exchange calendar (no session, so no gap).
    """
    if quote is None or daily_df is None or now is None or getattr(now, "tzinfo", None) is None:
        return None
    if profile is not None and not is_trading_day(profile, now.date()):
        return None
    try:
        day = quote.as_of.astimezone(now.tzinfo).date()
        if day != now.date():
            return None
        dates = pd.DatetimeIndex(daily_df.index).date
        prev = daily_df["Close"][dates < day]
        if len(prev) == 0:
            return None
        g = (float(quote.day_open) / float(prev.iloc[-1]) - 1) * 100
        return g if math.isfinite(g) else None
    except Exception:  # noqa: BLE001 - unknown gap WARNs
        return None


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


def breakout_entry(df) -> tuple[str, float | None]:
    """("at", None): at/through the 20-day high, plan at the close. ("trigger", price): within 5% below
    it, buy only above the trigger. ("far", None): not near a setup."""
    trigger = float(df["High"].tail(20).max())
    close = float(df["Close"].iloc[-1])
    # at/through the 20-day high, or closing at a 20-day closing high (intraday highs run ~1% above closes)
    if close >= trigger * 0.999 or close >= float(df["Close"].tail(20).max()):
        return "at", None
    if close < trigger / 1.05:
        return "far", None
    return "trigger", trigger * 1.001


def plan_entry(df) -> float | None:
    """The plan's own swing entry: None (buy at the close) at the 20-day high, else the breakout trigger
    (20-day high x 1.001), also for a symbol far below it."""
    kind, e = breakout_entry(df)
    if kind == "far":
        return float(df["High"].tail(20).max()) * 1.001
    return e


def _exp(v: DeskVerdict) -> float:
    return v.odds.mc_expectancy_r if v.odds is not None else float("-inf")


def _only_regime_block(v: DeskVerdict) -> bool:
    return {c.name for c in v.blocks()} == {"regime"}


def _watch_eligible(v: DeskVerdict) -> bool:
    return v.action is Action.WAIT or (v.action is Action.SKIP and _only_regime_block(v))


def _closeness(v: DeskVerdict) -> float:
    px = v.current_price
    if not (isinstance(px, (int, float)) and math.isfinite(px) and px > 0):
        return float("inf")
    return (v.trigger or v.plan.entry) / px


_UNSET = object()


def _tape_or_none(regime_df):
    try:
        return tape_monitor.assess_tape(regime_df)
    except Exception:  # noqa: BLE001 - unknown regime is handled fail-closed (swing BLOCK)
        return None


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def _overflow(vs: list, top: int, key) -> tuple[list, list]:
    """(the best `top` by `key`, the rest in rank order)."""
    ranked = sorted(vs, key=key)
    return ranked[:top], ranked[top:]


def _neg_exp(v: DeskVerdict) -> float:
    return -_exp(v)


@dataclass
class _Pass1:
    takes: list[DeskVerdict]
    watch: list[DeskVerdict]
    others: list[DeskVerdict]
    skipped: list[tuple[str, str]]
    errors: list[tuple[str, str]]
    unavailable: list[str]
    entries: dict[str, float | None]
    sym_ctx: dict[str, CheckContext]


def _first_pass(profile, frames: dict[str, pd.DataFrame], ctx: CheckContext, limits: Limits,
                replays: int) -> _Pass1:
    """Every symbol on its last close (no quote, no robustness, never audited). Near a setup -> TAKE/WATCH
    candidates; far symbols are priced at the plan's breakout trigger and kept in `others`; any exception is
    that symbol's `skipped` reason."""
    out = _Pass1([], [], [], [], [], [], {}, {})
    for sym, df in frames.items():
        if df is None or len(df) < 220:
            out.unavailable.append(sym)
            continue
        try:
            kind, _ = breakout_entry(df)
            e = plan_entry(df)
            c = replace(ctx, turnover_20d=turnover_20d(df), today_gap_pct=None)
            v = check_trade(df, sym, profile, Mode.SWING, limits, c, entry=e,
                            robustness=False, replays=replays, audit=False)
        except Exception as err:  # noqa: BLE001 - one symbol never stops the plan
            out.skipped.append((sym, _err(err)))
            if not isinstance(err, ValueError):
                out.errors.append((sym, _err(err)))
            continue
        v = replace(v, first_pass=True, ticket=())  # quick: no ticket until a full check (not hashed)
        if kind == "far":
            out.others.append(v)
            continue
        out.entries[sym], out.sym_ctx[sym] = e, c
        if v.action is Action.TAKE:
            out.takes.append(v)
        elif _watch_eligible(v):
            out.watch.append(v)
        else:
            out.others.append(v)
    return out


def _second_pass(profile, frames, p1: _Pass1, candidates: list[DeskVerdict], limits: Limits, replays: int,
                 audit_path: Path | None, earnings_lookup, quote_lookup
                 ) -> tuple[list[DeskVerdict], list[DeskVerdict], list[DeskVerdict], list[tuple[str, str]],
                            list[tuple[str, str]]]:
    """(takes, watch, others, skipped, errors): the candidates re-checked with the live quote, earnings date and
    robustness, audited. One network call of each kind per candidate."""
    takes: list[DeskVerdict] = []
    watch: list[DeskVerdict] = []
    others: list[DeskVerdict] = []
    skipped: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []
    for v in candidates:
        sym = v.plan.symbol
        c2 = p1.sym_ctx[sym]
        if earnings_lookup is not None:
            try:
                nxt, checked = earnings_lookup(sym)
            except Exception:  # noqa: BLE001 - unknown earnings WARNs, never passes
                nxt, checked = None, False
            c2 = replace(c2, next_earnings=nxt, earnings_checked=checked)
        quote = None
        if quote_lookup is not None:
            try:
                quote = quote_lookup(sym)
            except Exception:  # noqa: BLE001 - no quote: live_price and gap WARN
                quote = None
            c2 = replace(c2, today_gap_pct=today_gap_pct(frames[sym], quote, c2.now, profile))
        try:
            v2 = check_trade(frames[sym], sym, profile, Mode.SWING, limits, c2, entry=p1.entries[sym],
                             robustness=True, replays=replays, audit=True, audit_path=audit_path, quote=quote)
        except Exception as err:  # noqa: BLE001 - one symbol never stops the plan
            skipped.append((sym, _err(err)))
            if not isinstance(err, ValueError):
                errors.append((sym, _err(err)))
            continue
        if v2.action is Action.TAKE:
            takes.append(v2)
        elif _watch_eligible(v2):
            watch.append(v2)
        else:
            others.append(v2)
    return takes, watch, others, skipped, errors


def _sat_out_sessions(path: Path) -> set[tuple[str, str, date]] | None:
    """(market, symbol, fill session) of every SAT_OUT already in the audit log; None when the log can't be read
    (then nothing is logged: a duplicate is worse than a gap)."""
    from .scoreboard_io import AuditReadError, fill_session_of, read_audit  # scoreboard_io is heavier; load late
    try:
        rows, _ = read_audit(path)
    except AuditReadError:
        return None
    out: set[tuple[str, str, date]] = set()
    for _, r in rows:
        if r.get("action") != "SAT_OUT":
            continue
        try:
            out.add((str(r["market"]), str(r["symbol"]),
                     fill_session_of(str(r["market"]), datetime.fromisoformat(str(r["created_at"])))))
        except Exception:  # noqa: BLE001 - an unreadable row is the scoreboard's reject, not ours
            continue
    return out


SAT_OUT_UNREADABLE = "Sat-out record not written: the audit log could not be read (no duplicates risked)"


def _log_sat_outs(profile, verdicts: list[DeskVerdict], audit_path: Path) -> str | None:
    """One SAT_OUT row per symbol per session for every full-checked verdict the regime alone kept out (the rows the
    scoreboard's "Desk sat out (market regime)" record replays). Never stops the plan; returns why some rows could
    not be written (DailyPlan.sat_out_note), else None."""
    if not verdicts:
        return None
    from .scoreboard_io import fill_session_of
    seen = None
    failed: list[tuple[str, str]] = []
    for v in verdicts:
        try:
            if sat_out_refusal(v, profile) is not None:
                continue
            if seen is None:  # the log is read once, and only when there is something to log
                seen = _sat_out_sessions(audit_path)
                if seen is None:
                    return SAT_OUT_UNREADABLE
            key = (v.plan.market, v.plan.symbol, fill_session_of(v.plan.market, datetime.fromisoformat(v.created_at)))
            if key in seen:
                continue
            audit.append_sat_out(v, audit_path)
            seen.add(key)
        except Exception as e:  # noqa: BLE001 - the sat-out record is a measurement; the plan itself must come back
            failed.append((v.plan.symbol, _err(e)))
    if not failed:
        return None
    return f"Sat-out record not written for {len(failed)} symbol(s): {failed[0][0]}: {failed[0][1]}"


def _completed_bars(df, profile, now):
    """``df`` without today's still-open daily bar (as loaders drops it from the stock frames), so the index and the
    stocks end on the same completed session. Unreadable input is returned as it is (holding_up_report judges it)."""
    try:
        if now is None or df is None or len(df) == 0:
            return df
        local = now.astimezone(ZoneInfo(profile.tz)) if now.tzinfo is not None else now
        day = local.date()
        if (pd.Timestamp(df.index[-1]).date() == day and is_trading_day(profile, day)
                and local.time() < session_close_for(profile, day)):
            return df.iloc[:-1]
    except Exception:  # noqa: BLE001 - a frame this can't read is left for holding_up_report to reject
        return df
    return df


def _holding_up(frames, regime_df, profile=None, now=None) -> tuple[tuple[tuple[str, float], ...], str | None]:
    try:
        if profile is not None:
            regime_df = _completed_bars(regime_df, profile, now)
        held, why = strength.holding_up_report(frames, regime_df)
        return tuple(held), why
    except Exception as e:  # noqa: BLE001 - a watchlist failure never stops the plan
        return (), f"Holding-up list unavailable: {_err(e)}"


def daily_plan(profile, frames: dict[str, pd.DataFrame], regime_df, limits: Limits, ctx_base: CheckContext,
               *, top_take: int = 3, top_watch: int = 5, replays: int = 3000,
               audit_path: Path | None = None,
               earnings_lookup: Callable[[str], tuple[date | None, bool]] | None = None,
               quote_lookup: Callable[[str], LiveQuote | None] | None = None,
               regime=_UNSET, tape=None) -> DailyPlan:
    """Pass 1 checks every symbol without a live quote, at the plan's own entry (the close at the 20-day high,
    else the breakout trigger). Pass 2 re-checks only the top TAKE/WATCH candidates with the live quote,
    earnings date and robustness. Every other verdict is kept in `others`; a symbol with no verdict is in
    `skipped` with its reason.
    `regime`/`tape`, when given, are used as-is (so the page header and the plan agree) and the tape is
    not re-assessed."""
    if regime is _UNSET:
        tape = _tape_or_none(regime_df)
        regime = tape.regime if tape else None
    if tape is not None and getattr(tape, "regime", None) == regime:
        regime_text = tape.recommendation
    elif regime:
        regime_text = f"Regime {regime}"
    else:
        regime_text = "Regime unknown — treat as HOSTILE"
    ctx = replace(ctx_base, regime=regime)

    p1 = _first_pass(profile, frames, ctx, limits, replays)
    takes, takes_rest = _overflow(p1.takes, top_take, _neg_exp)
    watch, watch_rest = _overflow(p1.watch, top_watch, _closeness)
    t2, w2, o2, s2, e2 = _second_pass(profile, frames, p1, takes + watch, limits, replays, audit_path,
                                  earnings_lookup, quote_lookup)
    final_take, take_over = _overflow(t2, top_take, _neg_exp)
    final_watch, watch_over = _overflow(w2, top_watch, _closeness)
    sat_note = None
    if audit_path is not None:  # only a plan that writes its own audit log measures sitting out
        sat_note = _log_sat_outs(profile, w2 + o2, audit_path)
    others = [*take_over, *watch_over, *o2, *takes_rest, *watch_rest, *p1.others]
    held, held_note = _holding_up(frames, regime_df, profile, ctx.now)
    return DailyPlan(profile.code, regime, regime_text, flip_conditions(regime_df, profile),
                     tuple(final_take), tuple(final_watch), tuple(p1.skipped + s2),
                     tuple(p1.unavailable), tuple(others), held, held_note, sat_out_note=sat_note,
                     errors=tuple(p1.errors + e2))
