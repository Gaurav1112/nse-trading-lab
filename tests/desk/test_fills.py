import numpy as np
import pytest
from nse_backtest.desk.fills import entry_limit, limit_or_default, stop_limit_fill

def test_entry_limit_caps_at_quarter_R_or_half_percent_rounded_up_to_tick():
    assert entry_limit(100.0, 98.0, 0.05) == 100.50          # min(0.5, 0.5) = 0.5
    assert entry_limit(100.0, 99.6, 0.05) == 100.10          # 0.25R = 0.1
    assert entry_limit(100.0, 90.0, 0.01) == 100.50          # 0.5% cap
    assert entry_limit(100.0, 99.61, 0.05) == 100.10         # 0.0975 rounds UP to 0.10

def _a(*xs): return np.array(xs, dtype=float)

def test_fill_on_trigger_bar_when_open_within_limit():
    # bar0 below trigger, bar1 trades through 100 opening at 99.8 -> fill at trigger 100
    assert stop_limit_fill(_a(99, 99.8), _a(99.5, 100.4), _a(98.8, 99.7), 0, 1, 100.0, 100.5) == (1, 100.0)

def test_open_between_trigger_and_limit_fills_at_open():
    assert stop_limit_fill(_a(100.3), _a(100.6), _a(100.2), 0, 0, 100.0, 100.5) == (0, 100.3)

def test_gap_above_limit_rests_then_fills_at_limit_when_price_returns():
    op, hi, lo = _a(101, 100.9), _a(101.5, 101.0), _a(100.8, 100.4)
    assert stop_limit_fill(op, hi, lo, 0, 1, 100.0, 100.5) == (1, 100.5)

def test_gap_above_limit_never_returns_is_not_filled():
    assert stop_limit_fill(_a(101, 101.2), _a(101.5, 102), _a(100.8, 100.9), 0, 1, 100.0, 100.5) is None

def test_never_triggered():
    assert stop_limit_fill(_a(99, 99.2), _a(99.5, 99.9), _a(98.5, 99), 0, 1, 100.0, 100.5) is None

def test_property_fill_always_between_trigger_and_limit():
    rng = np.random.default_rng(19)
    for _ in range(2000):
        n = 8
        op = 100 + rng.normal(0, 1, n); cl = op + rng.normal(0, 0.6, n)
        hi = np.maximum(op, cl) + rng.uniform(0, 0.8, n); lo = np.minimum(op, cl) - rng.uniform(0, 0.8, n)
        res = stop_limit_fill(op, hi, lo, 0, n - 1, 100.0, 100.5)
        if res is not None:
            k, px = res
            assert 100.0 <= px <= 100.5
            assert hi[: k + 1].max() >= 100.0


def test_limit_or_default_keeps_a_given_limit_else_computes_it():
    assert limit_or_default(100.3, 100.0, 98.0, 0.05) == 100.3
    assert limit_or_default(None, 100.0, 98.0, 0.05) == 100.5


# ---- H5a: the short-side mirror (sell stop-limit below the opening-range low) ----
from nse_backtest.desk.fills import entry_limit_short, short_limit_or_default, stop_limit_fill_short  # noqa: E402


def test_short_entry_limit_caps_at_quarter_R_or_half_percent_rounded_down_to_tick():
    assert entry_limit_short(100.0, 102.0, 0.05) == 99.50       # min(0.5, 0.5) = 0.5
    assert entry_limit_short(100.0, 100.4, 0.05) == 99.90       # 0.25R = 0.1
    assert entry_limit_short(100.0, 110.0, 0.01) == 99.50       # 0.5% cap
    assert entry_limit_short(100.0, 100.39, 0.05) == 99.90      # 99.9025 rounds DOWN to 99.90
    assert entry_limit_short(99.0, 100.5, 0.05) == 98.60        # 99 - 0.375 = 98.625 -> 98.60


@pytest.mark.parametrize("entry, stop, tick", [(100, 100, 0.05), (100, 99, 0.05), (100, 101, 0)])
def test_short_entry_limit_rejects_bad_geometry(entry, stop, tick):
    with pytest.raises(ValueError):
        entry_limit_short(entry, stop, tick)


def test_short_limit_or_default_keeps_a_given_limit_else_computes_it():
    assert short_limit_or_default(99.7, 100.0, 102.0, 0.05) == 99.7
    assert short_limit_or_default(None, 100.0, 102.0, 0.05) == 99.5


def test_short_fill_on_trigger_bar_when_open_at_or_above_limit():
    # bar0 above the trigger, bar1 trades through 100 opening at 100.2 -> fill at the trigger 100
    assert stop_limit_fill_short(_a(101, 100.2), _a(101.2, 100.3), _a(100.5, 99.6), 0, 1, 100.0, 99.5) == (1, 100.0)


def test_short_open_between_limit_and_trigger_fills_at_open():
    assert stop_limit_fill_short(_a(99.7), _a(99.9), _a(99.5), 0, 0, 100.0, 99.5) == (0, 99.7)


def test_short_gap_below_limit_rests_then_fills_at_limit_when_price_returns():
    op, hi, lo = _a(99.0, 99.1), _a(99.2, 99.6), _a(98.5, 99.0)
    assert stop_limit_fill_short(op, hi, lo, 0, 1, 100.0, 99.5) == (1, 99.5)


def test_short_gap_below_limit_never_returns_is_not_filled():
    assert stop_limit_fill_short(_a(99.0, 98.8), _a(99.2, 99.4), _a(98.5, 98.0), 0, 1, 100.0, 99.5) is None


def test_short_never_triggered():
    assert stop_limit_fill_short(_a(101, 100.8), _a(101.5, 101.0), _a(100.5, 100.1), 0, 1, 100.0, 99.5) is None


def test_short_property_fill_always_between_limit_and_trigger():
    rng = np.random.default_rng(19)
    filled = 0
    for _ in range(2000):
        n = 8
        op = 100 + rng.normal(0, 1, n); cl = op + rng.normal(0, 0.6, n)
        hi = np.maximum(op, cl) + rng.uniform(0, 0.8, n); lo = np.minimum(op, cl) - rng.uniform(0, 0.8, n)
        res = stop_limit_fill_short(op, hi, lo, 0, n - 1, 100.0, 99.5)
        if res is not None:
            filled += 1
            k, px = res
            assert 99.5 <= px <= 100.0
            assert lo[: k + 1].min() <= 100.0
    assert filled > 500


def test_short_mirror_of_long_on_reflected_paths():
    """Reflect every price about 100: a short over the reflected bars fills at the reflection of the long's fill."""
    rng = np.random.default_rng(19)
    for _ in range(2000):
        n = 8
        op = 100 + rng.normal(0, 1, n); cl = op + rng.normal(0, 0.6, n)
        hi = np.maximum(op, cl) + rng.uniform(0, 0.8, n); lo = np.minimum(op, cl) - rng.uniform(0, 0.8, n)
        long = stop_limit_fill(op, hi, lo, 0, n - 1, 100.5, 101.0)
        short = stop_limit_fill_short(200 - op, 200 - lo, 200 - hi, 0, n - 1, 99.5, 99.0)
        if long is None:
            assert short is None
        else:
            assert short is not None and short[0] == long[0] and short[1] == pytest.approx(200 - long[1])
