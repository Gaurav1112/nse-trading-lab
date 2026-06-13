"""Phase G: statistical honesty primitives."""
import numpy as np
import pytest

from nse_backtest.stats import (
    deflated_sharpe, expectancy_ci_bootstrap, bonferroni_alpha,
)


def test_deflated_sharpe_basic_shape():
    """Returns the expected keys and reasonable ranges."""
    rng = np.random.default_rng(0)
    rets = rng.normal(0.02, 0.05, 100)
    out = deflated_sharpe(rets, n_trials_tested=5)
    assert set(out.keys()) >= {"observed_sharpe", "deflated_sharpe",
                                "prob_sharpe", "n_trials", "expected_max_sharpe"}
    assert 0.0 <= out["deflated_sharpe"] <= 1.0
    assert 0.0 <= out["prob_sharpe"] <= 1.0


def test_deflated_sharpe_more_trials_means_lower_confidence():
    """Same returns, more trials → expected_max_sharpe rises → deflated falls."""
    rng = np.random.default_rng(1)
    rets = rng.normal(0.02, 0.05, 100)
    out_few = deflated_sharpe(rets, n_trials_tested=1)
    out_many = deflated_sharpe(rets, n_trials_tested=20)
    assert out_many["expected_max_sharpe"] > out_few["expected_max_sharpe"]
    assert out_many["deflated_sharpe"] <= out_few["deflated_sharpe"]


def test_deflated_sharpe_handles_tiny_input():
    """Empty or 1-element input should not crash."""
    out = deflated_sharpe([], n_trials_tested=5)
    assert out["observed_sharpe"] == 0.0
    out = deflated_sharpe([0.01], n_trials_tested=5)
    assert out["observed_sharpe"] == 0.0  # n<2 falls into safe path


def test_expectancy_ci_bootstrap_contains_mean():
    """The CI should contain the sample mean (it's the midpoint)."""
    rng = np.random.default_rng(0)
    rets = rng.normal(2.0, 5.0, 200)
    low, mid, high = expectancy_ci_bootstrap(rets, n_boot=500)
    assert low < mid < high
    assert abs(mid - rets.mean()) < 1e-9


def test_expectancy_ci_narrows_with_more_data():
    """A 1000-trade sample should have a tighter CI than a 50-trade sample."""
    rng = np.random.default_rng(1)
    small = rng.normal(2.0, 5.0, 50)
    large = rng.normal(2.0, 5.0, 1000)
    low_s, _, high_s = expectancy_ci_bootstrap(small, n_boot=300)
    low_l, _, high_l = expectancy_ci_bootstrap(large, n_boot=300)
    assert (high_l - low_l) < (high_s - low_s)


def test_expectancy_ci_empty_returns_zeros():
    low, mid, high = expectancy_ci_bootstrap([])
    assert low == mid == high == 0.0


def test_bonferroni_alpha():
    assert bonferroni_alpha(1) == 0.05
    assert bonferroni_alpha(5) == pytest.approx(0.01)
    assert bonferroni_alpha(0) == 0.05  # defensive


def test_deflated_sharpe_strong_signal_survives_many_trials():
    """A genuinely good strategy (high Sharpe, many trades) should still
    score deflated > 0.5 even after correcting for many trials."""
    rng = np.random.default_rng(2)
    # Mean 0.10, std 0.04 -> per-trade Sharpe ~2.4, comfortably above
    # the expected_max_sharpe ~1.9 for N=20 trials.
    rets = rng.normal(0.10, 0.04, 500)
    out = deflated_sharpe(rets, n_trials_tested=20)
    assert out["deflated_sharpe"] > 0.5
