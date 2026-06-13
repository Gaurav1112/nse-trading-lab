"""Exit primitives for swing/positional engines.

Phase 1 introduces:
  - update_trail_stop(): break-even-and-trail after T1, monotonic SL.
  - time_stop_triggered(): bagholder kill switch.

Pure functions — reusable from live (position_monitor) and replay (picker_replay).
"""
from __future__ import annotations

from typing import Final


class ExitReason:
    """Stable string constants — these end up in the trade journal."""
    TARGET_1_PARTIAL: Final[str] = "T1_PARTIAL_BE"
    TARGET_2: Final[str] = "TARGET_2"
    TRAIL_STOP: Final[str] = "TRAIL_STOP"
    STOP_LOSS: Final[str] = "STOP_LOSS"
    TIME_STOP: Final[str] = "TIME_STOP"
    SCORE_DECAY: Final[str] = "SCORE_DECAY"
    END_OF_REPLAY: Final[str] = "END_OF_REPLAY"
    # Phase F (Daniel Foss): honest-execution exit reasons.
    STOP_LOSS_GAP: Final[str] = "STOP_LOSS_GAP"
    TARGET_2_GAP: Final[str] = "TARGET_2_GAP"
    CIRCUIT_LOCK: Final[str] = "CIRCUIT_LOCK"


def update_trail_stop(
    *,
    entry: float,
    current_sl: float,
    t1: float,
    atr: float,
    bar_high: float,
    bar_low: float,
    last_swing_low: float,
    t1_hit_already: bool,
) -> tuple[float, bool]:
    """Compute the new trailing stop and whether a partial-exit should fire.

    Rules:
      - If T1 not yet hit and bar's high >= T1: SL jumps to entry (break-even),
        and the caller takes 50% off.
      - If T1 already hit: SL ratchets to max(bar_high - 1.5*ATR, swing_low - 0.3*ATR).
      - SL is monotonic post-T1: never decreases.

    Returns: (new_sl, take_partial_now)
    """
    if not t1_hit_already and bar_high >= t1:
        return entry, True

    if t1_hit_already:
        candidate = max(bar_high - 1.5 * atr, last_swing_low - 0.3 * atr)
        return max(current_sl, candidate), False

    return current_sl, False


def time_stop_triggered(
    *, bars_held: int, current_rescore: float, entry_price: float, current_price: float,
    max_bars: int = 12, decay_threshold: float = 50.0,
) -> bool:
    """True when the bagholder kill-switch should fire.

    All three must hold:
      - held more than max_bars bars
      - re-score has decayed below decay_threshold
      - price is still below entry (we're underwater)
    """
    return (
        bars_held > max_bars
        and current_rescore < decay_threshold
        and current_price < entry_price
    )
