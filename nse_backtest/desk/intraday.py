"""Intraday opening-range-breakout scan over 15-minute frames."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .audit import append_verdict
from .checks import CheckContext
from .models import Action, DeskVerdict, LiveQuote, Limits, Mode
from .service import check_trade

_RANK = {Action.TAKE: 0, Action.WAIT: 1, Action.SKIP: 2}


def orb_scan(profile, frames15: dict[str, pd.DataFrame], limits: Limits, ctx_base: CheckContext,
             *, replays: int = 3000,
             ctx_by_symbol: dict[str, CheckContext] | None = None,
             quotes: dict[str, LiveQuote | None] | None = None,
             audit_path: Path | None = None) -> tuple[list[DeskVerdict], list[tuple[str, str]]]:
    """(verdicts best first, (symbol, reason) failures). With audit_path every TAKE is appended to that audit
    log as it is decided (WAIT/SKIP are not logged), so a later symbol's failure never loses an earlier TAKE. An
    audit OSError (reading or logging) makes that one symbol a failure; a TAKE that could not be logged is not
    returned as a verdict."""
    verdicts: list[DeskVerdict] = []
    failures: list[tuple[str, str]] = []
    for sym, df in frames15.items():
        try:
            v = check_trade(df, sym, profile, Mode.INTRADAY, limits, (ctx_by_symbol or {}).get(sym, ctx_base),
                            robustness=False, replays=replays, audit=False, quote=(quotes or {}).get(sym))
        except ValueError as e:
            failures.append((sym, str(e)))
            continue
        except OSError as e:  # an audit read/write problem for one symbol: that symbol fails, the rest still return
            failures.append((sym, f"{type(e).__name__}: {e}"))
            continue
        if audit_path is not None and v.action is Action.TAKE:
            try:
                append_verdict(v, audit_path)
            except OSError as e:
                failures.append((sym, f"desk said TAKE but it could not be recorded ({type(e).__name__}: {e}), "
                                      "so it is withheld — fix the audit log and rescan"))
                continue
        verdicts.append(v)

    def key(v: DeskVerdict):
        e = v.odds.mc_expectancy_r if v.odds is not None else None
        return (_RANK[v.action], e is None, -(e or 0.0))

    return sorted(verdicts, key=key), failures
