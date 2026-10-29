"""Intraday opening-range-breakout scan over 15-minute frames."""
from __future__ import annotations

from dataclasses import replace

import pandas as pd

from .checks import CheckContext
from .models import Action, DeskVerdict, Limits, Mode
from .service import check_trade

_RANK = {Action.TAKE: 0, Action.WAIT: 1, Action.SKIP: 2}


def orb_scan(profile, frames15: dict[str, pd.DataFrame], limits: Limits, ctx_base: CheckContext,
             *, replays: int = 3000,
             ctx_by_symbol: dict[str, CheckContext] | None = None) -> tuple[list[DeskVerdict], list[tuple[str, str]]]:
    verdicts: list[DeskVerdict] = []
    failures: list[tuple[str, str]] = []
    for sym, df in frames15.items():
        try:
            verdicts.append(check_trade(df, sym, profile, Mode.INTRADAY, limits,
                                        (ctx_by_symbol or {}).get(sym, ctx_base),
                                        robustness=False, replays=replays, audit=False))
        except ValueError as e:
            failures.append((sym, str(e)))

    def key(v: DeskVerdict):
        e = v.odds.mc_expectancy_r if v.odds is not None else None
        return (_RANK[v.action], e is None, -(e or 0.0))

    return sorted(verdicts, key=key), failures
