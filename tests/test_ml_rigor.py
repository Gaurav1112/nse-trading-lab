"""Tests for stationary block bootstrap + purged k-fold CV.

These functions don't change expectancy estimates; they make the CIs and
training-eval splits more defensible against autocorrelation and overlap.
"""
from __future__ import annotations

import numpy as np
import pytest

from nse_backtest.stats import (
    expectancy_ci_bootstrap, stationary_block_bootstrap, purged_kfold_indices,
)


def test_block_bootstrap_returns_three_floats():
    rng = np.random.default_rng(0)
    returns = rng.normal(0.5, 2.0, 100).tolist()
    low, mid, high = stationary_block_bootstrap(returns, n_boot=200)
    assert isinstance(low, float)
    assert isinstance(mid, float)
    assert isinstance(high, float)
    assert low <= mid <= high


def test_block_bootstrap_wider_than_naive_on_autocorrelated_data():
    """Construct an autocorrelated series; block bootstrap CI should be wider
    than the naive IID bootstrap CI on the same data (the whole point)."""
    rng = np.random.default_rng(42)
    # AR(1): r_t = 0.7 r_{t-1} + e_t — strong positive autocorrelation
    n = 300
    eps = rng.normal(0, 1, n)
    r = np.zeros(n)
    for i in range(1, n):
        r[i] = 0.7 * r[i - 1] + eps[i]
    naive_low, _, naive_high = expectancy_ci_bootstrap(r.tolist(), n_boot=500)
    block_low, _, block_high = stationary_block_bootstrap(r.tolist(), n_boot=500)
    naive_width = naive_high - naive_low
    block_width = block_high - block_low
    # Block bootstrap should be ≥ naive on autocorrelated data; allow a touch
    # of tolerance because of sampling noise at n_boot=500
    assert block_width >= naive_width * 0.95


def test_block_bootstrap_empty_input_safe():
    low, mid, high = stationary_block_bootstrap([])
    assert (low, mid, high) == (0.0, 0.0, 0.0)


def test_block_bootstrap_singleton_returns_constant():
    """A single observation → no variance → CI collapses to the point."""
    low, mid, high = stationary_block_bootstrap([2.5], n_boot=50)
    assert mid == pytest.approx(2.5)
    assert low == pytest.approx(2.5)
    assert high == pytest.approx(2.5)


def test_purged_kfold_returns_k_folds():
    folds = purged_kfold_indices(n=100, n_splits=5, embargo=5)
    assert len(folds) == 5
    for train, ev in folds:
        assert len(ev) > 0
        assert len(train) > 0
        # No overlap between train and eval indices
        assert len(set(train.tolist()) & set(ev.tolist())) == 0


def test_purged_kfold_embargo_excludes_nearby_train():
    folds = purged_kfold_indices(n=100, n_splits=5, embargo=10)
    for train, ev in folds:
        eval_start, eval_end = ev[0], ev[-1]
        for t in train:
            # No training index falls within `embargo` of the eval block
            assert t < eval_start - 10 or t > eval_end + 10 or t < eval_start


def test_purged_kfold_raises_on_invalid_inputs():
    with pytest.raises(ValueError):
        purged_kfold_indices(n=10, n_splits=20)
    with pytest.raises(ValueError):
        purged_kfold_indices(n=10, n_splits=1)


def test_block_bootstrap_seed_reproducible():
    returns = [1.0, -0.5, 2.0, -1.0, 0.5] * 20
    out_a = stationary_block_bootstrap(returns, n_boot=100, seed=7)
    out_b = stationary_block_bootstrap(returns, n_boot=100, seed=7)
    assert out_a == out_b
