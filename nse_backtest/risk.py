"""
Risk Management Engine
========================
Professional risk controls for NSE Trading Lab.

Models:
- Kelly Criterion position sizing (fractional)
- Volatility targeting (Lopez de Prado)
- ATR-based stop loss
- Max drawdown circuit breaker
- Fixed fractional sizing
- Calmar ratio
- VaR / CVaR
- Monthly returns heatmap
- Walk-forward validation

References:
  Ernest Chan — Quantitative Trading
  Lopez de Prado — Advances in Financial ML
  Perry Kaufman — Trading Systems and Methods
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass


@dataclass
class RiskLimits:
    """Portfolio-level risk parameters."""
    max_position_pct: float = 0.25        # Max 25% capital in one trade
    max_portfolio_heat: float = 0.06      # Max 6% total capital at risk
    max_drawdown_pct: float = 0.15        # 15% DD = halt trading
    drawdown_reduce_pct: float = 0.10     # At 10% DD reduce size by 50%
    vol_target_annual: float = 0.15       # 15% annualized vol target
    max_kelly_fraction: float = 0.25      # Quarter-Kelly (conservative)
    min_risk_reward: float = 1.5          # Minimum R:R to take a trade
    consecutive_loss_limit: int = 5       # Pause after 5 consecutive losses


def kelly_criterion(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Kelly Criterion — optimal fraction of capital to risk.
    f* = (p*b - q) / b where p=win prob, q=1-p, b=payoff ratio
    Defensive: refuses to compute if signs are wrong (avg_win<=0 or avg_loss>=0).
    """
    if avg_loss == 0 or avg_win == 0 or win_rate <= 0 or win_rate >= 1:
        return 0.0
    if avg_win <= 0 or avg_loss >= 0:
        # Bad inputs (a "win" should be positive PnL, a "loss" should be negative)
        return 0.0
    b = abs(avg_win / avg_loss)
    p, q = win_rate, 1 - win_rate
    kelly = (p * b - q) / b
    return max(0.0, min(kelly, 0.5))


def fractional_kelly(win_rate: float, avg_win: float, avg_loss: float,
                      fraction: float = 0.25) -> float:
    """Fractional Kelly (25% default) for reduced variance."""
    return kelly_criterion(win_rate, avg_win, avg_loss) * fraction


def volatility_target_size(capital: float, price: float,
                            atr: float, target_vol: float = 0.15) -> int:
    """
    Volatility-targeted position sizing.
    Ensures each position contributes equally to portfolio risk.
    """
    if atr <= 0 or price <= 0:
        return 0
    daily_target = target_vol / np.sqrt(252)
    vol_shares = int((capital * daily_target) / atr)
    max_shares = int(capital * 0.25 / price)
    return max(min(vol_shares, max_shares), 0)


def atr_stop_loss(df: pd.DataFrame, multiplier: float = 2.0, period: int = 14) -> float:
    """ATR-based stop loss distance."""
    if len(df) < period:
        return df["Close"].iloc[-1] * 0.05
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"] - df["Close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return atr * multiplier


def check_drawdown_limit(equity_curve: pd.Series, max_dd: float = 0.15) -> dict:
    """
    Check if drawdown limit is breached.
    Returns dict with current state and recommendation.
    """
    if len(equity_curve) < 2:
        return {"current_dd": 0, "max_dd_seen": 0, "is_halted": False,
                "size_multiplier": 1.0, "peak": equity_curve.iloc[-1] if len(equity_curve) else 0}

    peak = equity_curve.expanding().max()
    dd = (equity_curve - peak) / peak
    current_dd = dd.iloc[-1]
    max_dd_seen = dd.min()

    is_halted = current_dd <= -max_dd
    # Deeper drawdown → smaller size. Thresholds expressed as fractions of max_dd.
    abs_dd = abs(current_dd)
    if abs_dd >= max_dd:
        multiplier = 0.25
    elif abs_dd >= max_dd * 0.67:
        multiplier = 0.5
    elif abs_dd >= max_dd * 0.33:
        multiplier = 0.75
    else:
        multiplier = 1.0

    return {
        "current_dd": current_dd,
        "max_dd_seen": max_dd_seen,
        "is_halted": is_halted,
        "size_multiplier": multiplier,
        "peak": peak.iloc[-1],
    }


def position_size_risk_based(capital: float, entry: float, stop_loss: float,
                              risk_pct: float = 0.02) -> int:
    """Fixed fractional: Shares = (Capital × Risk%) / (Entry - SL).

    Returns the smaller of risk-based and 25%-notional sizing.  Returns 0 only
    when SL is invalid or capital is insufficient even for a single share.
    """
    if entry <= 0 or capital <= 0:
        return 0
    sl_dist = entry - stop_loss
    if sl_dist <= 0:
        return 0
    risk_shares = int((capital * risk_pct) / sl_dist)
    notional_shares = int(capital * 0.25 / entry)
    shares = min(risk_shares, notional_shares)
    if shares < 1 and capital >= entry:
        # Capital can afford ≥1 share but sizing rounds down — return 1 with a
        # warning rather than a silent zero.
        from ._logging import get_logger
        get_logger(__name__).warning(
            "position_size_risk_based: rounding up to 1 share "
            "(capital=%.2f entry=%.2f sl_dist=%.2f)", capital, entry, sl_dist,
        )
        shares = 1
    return max(0, shares)


def annualized_volatility(returns: pd.Series, trading_days: int = 252) -> float:
    """Annualized volatility from daily returns."""
    if len(returns) < 2:
        return 0
    return returns.std() * np.sqrt(trading_days)


def calmar_ratio(cagr_pct: float, max_drawdown_pct: float) -> float:
    """Calmar ratio = CAGR / Max Drawdown."""
    if max_drawdown_pct == 0:
        return 0
    return abs(cagr_pct / max_drawdown_pct)


def compute_var_cvar(equity_curve: pd.Series) -> dict:
    """Value at Risk and Conditional VaR (Expected Shortfall)."""
    if len(equity_curve) < 10:
        return {"var_95": 0, "var_99": 0, "cvar_95": 0}
    returns = equity_curve.pct_change().dropna().replace([np.inf, -np.inf], 0)
    var_95 = np.percentile(returns, 5)
    var_99 = np.percentile(returns, 1)
    tail = returns[returns <= var_95]
    cvar_95 = tail.mean() if len(tail) > 0 else var_95
    return {"var_95": var_95, "var_99": var_99, "cvar_95": cvar_95}


def monthly_returns_table(equity_curve: pd.Series) -> pd.DataFrame:
    """Monthly returns heatmap data."""
    if len(equity_curve) < 20:
        return pd.DataFrame()
    monthly = equity_curve.resample("ME").last().pct_change().dropna() * 100
    if len(monthly) == 0:
        return pd.DataFrame()
    df = pd.DataFrame({"return": monthly})
    df["year"] = df.index.year
    df["month"] = df.index.month
    pivot = df.pivot_table(values="return", index="year", columns="month", aggfunc="first")
    pivot.columns = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][:len(pivot.columns)]
    return pivot


def walk_forward_validate(df: pd.DataFrame, strategy_func, config,
                           n_splits: int = 5, train_pct: float = 0.7) -> list:
    """
    Walk-forward validation to detect overfitting.
    Splits data into n windows, trains on train_pct, tests on remainder.
    """
    from .engine import run_backtest
    from .analytics import compute_metrics

    results = []
    n = len(df)
    window = n // n_splits

    for i in range(n_splits):
        start = i * window
        end = min(start + window, n)
        if end - start < 60:
            continue
        split = int((end - start) * train_pct)
        train_df = df.iloc[start:start + split]
        test_df = df.iloc[start + split:end]

        if len(test_df) < 20:
            continue

        try:
            sd = strategy_func(test_df)
            r = run_backtest(sd, config)
            m = compute_metrics(r)
            results.append({
                "split": i + 1,
                "test_start": test_df.index[0].strftime("%Y-%m-%d"),
                "test_end": test_df.index[-1].strftime("%Y-%m-%d"),
                "return_pct": m["total_return_pct"],
                "sharpe": m["sharpe_ratio"],
                "trades": m["total_trades"],
                "win_rate": m["win_rate_pct"],
                "max_dd": m["max_drawdown_pct"],
            })
        except Exception as e:
            from ._logging import get_logger
            get_logger(__name__).warning("walk_forward split %d failed: %s", i + 1, e)

    return results
