"""Intraday opening-range-breakout scan over 15-minute frames."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .audit import append_verdict
from .checks import CheckContext
from .models import Action, DeskVerdict, LiveQuote, Limits, Mode, Side, expectancy
from .service import check_trade
from .textsafe import scrub

_RANK = {Action.TAKE: 0, Action.WAIT: 1, Action.SKIP: 2}


def orb_scan(profile, frames15: dict[str, pd.DataFrame], limits: Limits, ctx_base: CheckContext,
             *, replays: int = 3000,
             ctx_by_symbol: dict[str, CheckContext] | None = None,
             quotes: dict[str, LiveQuote | None] | None = None,
             audit_path: Path | None = None,
             sides: tuple[Side, ...] = (Side.LONG,)) -> tuple[list[DeskVerdict], list[tuple[str, str]]]:
    """(verdicts best first, (symbol, reason) failures). With audit_path every TAKE is appended to that audit
    log as it is decided (WAIT/SKIP are not logged), so a later symbol's failure never loses an earlier TAKE. An
    audit OSError (reading or logging) makes that one symbol a failure; a TAKE that could not be logged is not
    returned as a verdict. ``sides`` (default long only) are checked per symbol, in order; a short's failure
    reason starts with "short: "."""
    verdicts: list[DeskVerdict] = []
    failures: list[tuple[str, str]] = []
    for sym, df in frames15.items():
        for side in sides:
            v = _one(df, sym, profile, limits, (ctx_by_symbol or {}).get(sym, ctx_base), replays,
                     (quotes or {}).get(sym), Side(side), audit_path, failures)
            if v is not None:
                verdicts.append(v)

    def key(v: DeskVerdict):
        e = expectancy(v)  # NaN-safe: a non-finite expectancy ranks with no odds
        return (_RANK[v.action], e is None, -(e or 0.0))

    return sorted(verdicts, key=key), failures


def _one(df, sym: str, profile, limits: Limits, ctx: CheckContext, replays: int, quote, side: Side,
         audit_path: Path | None, failures: list) -> DeskVerdict | None:
    """One symbol and side: the verdict, or None after appending the failure."""
    tag = "short: " if side is Side.SHORT else ""
    kw = {"side": side} if side is Side.SHORT else {}  # long calls are exactly as before
    try:
        v = check_trade(df, sym, profile, Mode.INTRADAY, limits, ctx,
                        robustness=False, replays=replays, audit=False, quote=quote, **kw)
    except ValueError as e:
        failures.append((sym, f"{tag}{e}"))
        return None
    except OSError as e:  # an audit read/write problem for one symbol: that symbol fails, the rest still return
        failures.append((sym, f"{tag}{type(e).__name__}: {scrub(e)}"))
        return None
    if audit_path is not None and v.action is Action.TAKE:
        try:
            append_verdict(v, audit_path)
        except OSError as e:
            failures.append((sym, f"{tag}desk said TAKE but it could not be recorded ({type(e).__name__}: "
                                  f"{scrub(e)}), so it is withheld — fix the audit log and rescan"))
            return None
    return v
