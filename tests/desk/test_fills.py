import numpy as np
import pytest
from nse_backtest.desk.fills import entry_limit, stop_limit_fill

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
