"""Tomás Vega's statistical honesty toolkit (Phase G).

Three primitives:
  deflated_sharpe(returns, n_trials_tested)
    — Bailey & Lopez de Prado (2014) correction. Probability the observed
      Sharpe could have arisen from running n_trials independent strategies.
      Returns the *probabilistically deflated* Sharpe.

  expectancy_ci_bootstrap(per_trade_returns, n_boot=1000, ci=0.95)
    — Non-parametric 95% CI via bootstrap resampling. Returns (low, mid, high).

  bonferroni_alpha(n_trials, family_alpha=0.05)
    — Per-test α after Bonferroni correction across n_trials.
"""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np

try:
    from scipy import stats as _scs

    def _norm_ppf(x: float) -> float:
        return float(_scs.norm.ppf(x))

    def _norm_cdf(x: float) -> float:
        return float(_scs.norm.cdf(x))

    def _skew(r: np.ndarray) -> float:
        return float(_scs.skew(r))

    def _kurt(r: np.ndarray) -> float:
        return float(_scs.kurtosis(r, fisher=True))

except Exception:  # pragma: no cover - fallback path
    def _norm_ppf(x: float) -> float:
        # Inverse standard normal CDF via erfinv
        return math.sqrt(2.0) * math.erfinv(2.0 * x - 1.0)

    def _norm_cdf(x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def _skew(r: np.ndarray) -> float:
        mu = float(r.mean())
        sigma = float(r.std(ddof=0))
        if sigma == 0:
            return 0.0
        return float((((r - mu) / sigma) ** 3).mean())

    def _kurt(r: np.ndarray) -> float:
        mu = float(r.mean())
        sigma = float(r.std(ddof=0))
        if sigma == 0:
            return 0.0
        return float((((r - mu) / sigma) ** 4).mean() - 3.0)


def _to_array(returns: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(returns), dtype=float)
    return arr[np.isfinite(arr)]


def deflated_sharpe(
    returns: Iterable[float],
    n_trials_tested: int,
    benchmark_sharpe: float = 0.0,
    skewness: float | None = None,
    kurtosis: float | None = None,
) -> dict:
    """Compute the Probabilistic + Deflated Sharpe Ratio.

    Returns dict with: observed_sharpe, deflated_sharpe, prob_sharpe,
                       n_trials, expected_max_sharpe.

    Based on Bailey & López de Prado (2014). When you've tested N strategies
    and report the best, the observed Sharpe is biased upward. The deflated
    estimate is what you'd believably get out-of-sample.
    """
    r = _to_array(returns)
    n = len(r)
    if n < 2:
        return {
            "observed_sharpe": 0.0,
            "deflated_sharpe": 0.0,
            "prob_sharpe": 0.5,
            "n_trials": int(n_trials_tested),
            "expected_max_sharpe": 0.0,
        }

    mu, sigma = float(r.mean()), float(r.std(ddof=1))
    sharpe = mu / sigma if sigma > 0 else 0.0

    # Higher moments
    g3 = _skew(r) if skewness is None else float(skewness)
    g4 = _kurt(r) if kurtosis is None else float(kurtosis)

    # Expected maximum Sharpe across N i.i.d. trials (Bailey-LdP eq. 6)
    # E[max_N] ≈ (1-γ)·Φ⁻¹(1-1/N) + γ·Φ⁻¹(1-1/(N·e))   where γ = Euler-Mascheroni
    euler = 0.5772156649
    if n_trials_tested <= 1:
        emax = 0.0
    else:
        z1 = _norm_ppf(1.0 - 1.0 / n_trials_tested)
        z2 = _norm_ppf(1.0 - 1.0 / (n_trials_tested * math.e))
        emax = (1.0 - euler) * z1 + euler * z2

    # Variance of estimated Sharpe (Mertens 2002 / Lo 2002)
    var_sharpe = (1.0 - g3 * sharpe + (g4 / 4.0) * sharpe ** 2) / (n - 1)
    sd_sharpe = math.sqrt(max(var_sharpe, 1e-12))

    # Probabilistic Sharpe: P(true Sharpe > benchmark | observed sample)
    prob_sharpe = _norm_cdf((sharpe - benchmark_sharpe) / sd_sharpe)

    # Deflated Sharpe: P(true Sharpe > expected_max_under_null)
    deflated_z = (sharpe - emax) / sd_sharpe
    defl_sharpe = _norm_cdf(deflated_z)

    return {
        "observed_sharpe": round(sharpe, 4),
        "deflated_sharpe": round(defl_sharpe, 4),   # probability, 0-1
        "prob_sharpe": round(prob_sharpe, 4),       # probability, 0-1
        "n_trials": int(n_trials_tested),
        "expected_max_sharpe": round(emax, 4),
    }


def expectancy_ci_bootstrap(
    per_trade_returns: Iterable[float],
    n_boot: int = 1000,
    ci: float = 0.95,
    seed: int = 17,
) -> tuple[float, float, float]:
    """Naive bootstrap CI on the mean of per-trade returns.

    Kept for backward compatibility — for new time-series CIs prefer
    `stationary_block_bootstrap` below which respects autocorrelation.
    """
    r = _to_array(per_trade_returns)
    if len(r) == 0:
        return (0.0, 0.0, 0.0)
    rng = np.random.default_rng(seed)
    n = len(r)
    means = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        sample = rng.choice(r, size=n, replace=True)
        means[i] = sample.mean()
    alpha = (1.0 - ci) / 2.0
    low = float(np.quantile(means, alpha))
    mid = float(r.mean())
    high = float(np.quantile(means, 1.0 - alpha))
    return (low, mid, high)


def stationary_block_bootstrap(
    per_trade_returns: Iterable[float],
    n_boot: int = 1000,
    ci: float = 0.95,
    mean_block_size: int | None = None,
    seed: int = 17,
) -> tuple[float, float, float]:
    """Politis-Romano stationary block bootstrap (1994). Preserves the local
    autocorrelation structure of a time series by resampling random-length
    blocks with geometric block lengths.

    For our use case (consecutive per-trade returns where overlapping holding
    periods and regime clustering induce autocorrelation), this gives wider —
    more honest — CIs than the naive IID bootstrap above.

    mean_block_size defaults to ~sqrt(n) which is the standard automatic
    rule for moderate n. The geometric distribution has p = 1 / mean_block_size.
    """
    r = _to_array(per_trade_returns)
    n = len(r)
    if n == 0:
        return (0.0, 0.0, 0.0)
    if mean_block_size is None:
        mean_block_size = max(2, int(np.ceil(np.sqrt(n))))
    p = 1.0 / mean_block_size
    rng = np.random.default_rng(seed)
    means = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        out = []
        while len(out) < n:
            # Random starting index; circular wrap to handle ends.
            start = int(rng.integers(0, n))
            # Geometric block length; +1 ensures min 1.
            block_len = int(rng.geometric(p))
            for j in range(block_len):
                out.append(r[(start + j) % n])
                if len(out) >= n:
                    break
        means[i] = float(np.mean(out[:n]))
    alpha = (1.0 - ci) / 2.0
    low = float(np.quantile(means, alpha))
    mid = float(r.mean())
    high = float(np.quantile(means, 1.0 - alpha))
    return (low, mid, high)


def purged_kfold_indices(
    n: int,
    n_splits: int = 5,
    embargo: int = 15,
    seed: int = 17,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Generate train/eval index pairs using purged k-fold with embargo.

    Lopez de Prado (Advances in FML, ch.7): when sample observations overlap
    in time (e.g. our overlapping holding-period trades), random k-fold
    cross-validation leaks information from train to eval. Two corrections:

      1. PURGE: drop training observations whose holding period overlaps an
         eval observation's holding period.
      2. EMBARGO: drop additional training observations within `embargo`
         positions immediately after the eval block, to prevent leakage from
         look-back features.

    Returns a list of (train_idx, eval_idx) numpy arrays. Caller is
    responsible for ordering the input data chronologically.
    """
    if n_splits < 2 or n < n_splits:
        raise ValueError(f"Need n_splits >= 2 and n >= n_splits (got n={n}, k={n_splits})")
    rng = np.random.default_rng(seed)
    indices = np.arange(n)
    fold_size = n // n_splits
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    for i in range(n_splits):
        eval_start = i * fold_size
        eval_end = (i + 1) * fold_size if i < n_splits - 1 else n
        eval_idx = indices[eval_start:eval_end]
        # Purge + embargo: drop anything within `embargo` of the eval block.
        purge_start = max(0, eval_start - embargo)
        purge_end = min(n, eval_end + embargo)
        train_mask = np.ones(n, dtype=bool)
        train_mask[purge_start:purge_end] = False
        train_idx = indices[train_mask]
        # Shuffle only the training set; preserve eval ordering for
        # downstream walk-forward semantics.
        rng.shuffle(train_idx)
        folds.append((train_idx, eval_idx))
    return folds


def bonferroni_alpha(n_trials: int, family_alpha: float = 0.05) -> float:
    """Per-test α after Bonferroni correction across n_trials."""
    if n_trials <= 0:
        return family_alpha
    return family_alpha / n_trials
