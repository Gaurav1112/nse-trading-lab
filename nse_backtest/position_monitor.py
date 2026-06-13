"""Daily position re-score — the bagholder antidote.

For each held position, runs analyze_swing on today's data and emits one of:
  HOLD          — score still healthy (>=60)
  TIGHTEN_STOP  — score slipping (45-60); recommend pulling SL closer
  EXIT          — score decayed (<45) or time-stop fired

Surfaces on pages/12_Decay_Watch.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final
import pandas as pd

from .trading_modes import analyze_swing
from .exits import time_stop_triggered


class ReScoreAction:
    HOLD: Final[str] = "HOLD"
    TIGHTEN_STOP: Final[str] = "TIGHTEN_STOP"
    EXIT: Final[str] = "EXIT"


@dataclass
class ReScoreVerdict:
    symbol: str
    action: str
    current_rescore: float
    bars_held: int
    current_price: float
    entry_price: float
    pnl_pct: float
    suggested_sl: float | None
    reason: str


def _bars_held(entry_date_str: str | None, df: pd.DataFrame) -> int:
    if not entry_date_str:
        return 0
    try:
        entry = pd.to_datetime(entry_date_str).normalize()
    except (ValueError, TypeError):
        return 0
    idx = df.index[df.index.normalize() >= entry]
    return len(idx)


def daily_check(position: dict, df: pd.DataFrame) -> ReScoreVerdict:
    """Re-score a held position against today's market data.

    position: dict with keys symbol, buy_price, qty, stop_loss, target,
              entry_date (YYYY-MM-DD string, optional).
    df: OHLCV up to and including today for the position's symbol.
    """
    symbol = position["symbol"]
    entry = float(position["buy_price"])
    sl = float(position["stop_loss"])
    cur = float(df["Close"].iloc[-1])
    pnl_pct = (cur / entry - 1.0) * 100 if entry > 0 else 0.0
    bars = _bars_held(position.get("entry_date"), df)

    setup = analyze_swing(df, symbol, capital=100_000, risk_pct=2.0)
    rescore = setup.score

    if time_stop_triggered(
        bars_held=bars, current_rescore=rescore,
        entry_price=entry, current_price=cur,
    ):
        return ReScoreVerdict(
            symbol=symbol, action=ReScoreAction.EXIT, current_rescore=rescore,
            bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
            suggested_sl=None,
            reason=f"Time-stop fired: held {bars} bars, re-score {rescore:.0f}, underwater {pnl_pct:.1f}%",
        )

    if rescore < 45:
        return ReScoreVerdict(
            symbol=symbol, action=ReScoreAction.EXIT, current_rescore=rescore,
            bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
            suggested_sl=None,
            reason=f"Score decay: re-score {rescore:.0f} < 45 — engine no longer endorses this setup",
        )

    if rescore < 60:
        suggested = max(sl, setup.stop_loss)
        return ReScoreVerdict(
            symbol=symbol, action=ReScoreAction.TIGHTEN_STOP, current_rescore=rescore,
            bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
            suggested_sl=suggested,
            reason=f"Re-score slipping ({rescore:.0f}); tighten SL to ₹{suggested:.2f}",
        )

    return ReScoreVerdict(
        symbol=symbol, action=ReScoreAction.HOLD, current_rescore=rescore,
        bars_held=bars, current_price=cur, entry_price=entry, pnl_pct=pnl_pct,
        suggested_sl=sl,
        reason=f"Setup still valid (re-score {rescore:.0f})",
    )
