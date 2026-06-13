import pandas as pd
import pytest
from nse_backtest.exits import (
    update_trail_stop, time_stop_triggered, ExitReason,
)


def test_trail_stop_moves_to_breakeven_when_t1_hit():
    entry, sl, t1, atr = 100.0, 95.0, 110.0, 2.0
    new_sl, partial = update_trail_stop(
        entry=entry, current_sl=sl, t1=t1, atr=atr,
        bar_high=111.0, bar_low=108.0, last_swing_low=104.0,
        t1_hit_already=False,
    )
    assert new_sl == pytest.approx(entry)
    assert partial is True


def test_trail_stop_trails_above_breakeven_on_new_highs():
    """Once T1 already hit, SL ratchets up using max(bar_high - 1.5*ATR, swing_low - 0.3*ATR)."""
    entry, sl, t1, atr = 100.0, 100.0, 110.0, 2.0
    new_sl, partial = update_trail_stop(
        entry=entry, current_sl=sl, t1=t1, atr=atr,
        bar_high=120.0, bar_low=117.0, last_swing_low=115.0,
        t1_hit_already=True,
    )
    # max(120 - 1.5*2, 115 - 0.3*2) = max(117.0, 114.4) = 117.0
    assert new_sl == pytest.approx(117.0)
    assert partial is False


def test_trail_stop_never_goes_backwards():
    """SL must monotonically increase once T1 hit."""
    new_sl, _ = update_trail_stop(
        entry=100.0, current_sl=115.0, t1=110.0, atr=2.0,
        bar_high=118.0, bar_low=117.5, last_swing_low=112.0,
        t1_hit_already=True,
    )
    assert new_sl >= 115.0


def test_time_stop_fires_when_held_long_with_decayed_score():
    """>12 bars held + current re-score <50 + price below entry → exit."""
    assert time_stop_triggered(bars_held=13, current_rescore=42, entry_price=100, current_price=98)


def test_time_stop_does_not_fire_when_above_entry():
    assert not time_stop_triggered(bars_held=20, current_rescore=42, entry_price=100, current_price=105)


def test_time_stop_does_not_fire_when_score_still_healthy():
    assert not time_stop_triggered(bars_held=20, current_rescore=58, entry_price=100, current_price=98)


def test_exit_reason_strings_are_stable():
    """These strings appear in the trade journal — changing them silently breaks history filters."""
    assert ExitReason.TARGET_1_PARTIAL == "T1_PARTIAL_BE"
    assert ExitReason.TRAIL_STOP == "TRAIL_STOP"
    assert ExitReason.TIME_STOP == "TIME_STOP"
    assert ExitReason.STOP_LOSS == "STOP_LOSS"
    assert ExitReason.TARGET_2 == "TARGET_2"
