from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import pandas as pd
from pipeline.fetch import LTPQuote
from nse_backtest.tape_monitor import assess_tape


@dataclass(frozen=True)
class Signal:
    signal_id: str
    mode: str            # "SWING" | "INTRADAY"
    action: str          # "BUY" | "SELL" | "EXIT"
    symbol: str
    entry: float
    stop_loss: float
    target: float
    tape_regime: str
    thesis: str
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class SignalBatch:
    generated_at: datetime
    regime: str
    regime_conditions: dict
    swing_signals: list[Signal]


def _analyze_symbol(symbol: str, ltp: float, tape) -> Optional[Signal]:
    """Wrapper around existing analyze_swing — Loop 1 emits a stub signal based
    on tape regime only. Real scorer integration lands in Loop 2 (T2.x)."""
    if tape.regime == "HOSTILE":
        return None
    sl = round(ltp * 0.98, 2)
    tgt = round(ltp * 1.03, 2)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    return Signal(
        signal_id=f"swing-{ts}-{symbol}",
        mode="SWING", action="BUY", symbol=symbol,
        entry=ltp, stop_loss=sl, target=tgt,
        tape_regime=tape.regime,
        thesis=f"L1 stub — tape regime {tape.regime}",
    )


def compute_signal_batch(
    ltps: dict[str, LTPQuote],
    nifty_df: pd.DataFrame,
) -> SignalBatch:
    tape = assess_tape(nifty_df)
    signals: list[Signal] = []
    for sym, quote in ltps.items():
        s = _analyze_symbol(sym, quote.ltp, tape)
        if s is not None:
            signals.append(s)
    conditions = {
        "nifty_close": tape.nifty_close,
        "return_60d_pct": tape.return_60d_pct,
        "ema_200_slope_pct_20d": tape.ema_200_slope_pct_20d,
    }
    return SignalBatch(
        generated_at=datetime.now(timezone.utc),
        regime=tape.regime,
        regime_conditions=conditions,
        swing_signals=signals,
    )
