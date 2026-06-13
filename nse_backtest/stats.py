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
    """Non-parametric 95% CI on the mean of per-trade returns via bootstrap.

    Returns (low, mid, high) — mid is the point estimate (sample mean).
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


def bonferroni_alpha(n_trials: int, family_alpha: float = 0.05) -> float:
    """Per-test α after Bonferroni correction across n_trials."""
    if n_trials <= 0:
        return family_alpha
    return family_alpha / n_trials
