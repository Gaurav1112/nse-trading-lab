"""NSE Trading Lab — Backtesting, Scoring & Screening Framework."""
from ._logging import get_logger
from .data import fetch_nse, fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS, NIFTY100_SYMBOLS
from .strategies import STRATEGIES
from .engine import run_backtest, TradeConfig
from .analytics import compute_metrics, print_report, plot_results, compare_strategies
from .scorer import analyze_stock, print_analysis
from .screener import run_screener, print_screener_results
from .risk import (
    RiskLimits, kelly_criterion, fractional_kelly,
    volatility_target_size, atr_stop_loss, check_drawdown_limit,
    position_size_risk_based, calmar_ratio, compute_var_cvar,
    monthly_returns_table, walk_forward_validate,
)

__all__ = [
    "get_logger",
    "fetch_nse", "fetch_multiple", "fetch_nifty50", "NIFTY50_SYMBOLS", "NIFTY100_SYMBOLS",
    "STRATEGIES",
    "run_backtest", "TradeConfig",
    "compute_metrics", "print_report", "plot_results", "compare_strategies",
    "analyze_stock", "print_analysis",
    "run_screener", "print_screener_results",
    "RiskLimits", "kelly_criterion", "fractional_kelly",
    "volatility_target_size", "atr_stop_loss", "check_drawdown_limit",
    "position_size_risk_based", "calmar_ratio", "compute_var_cvar",
    "monthly_returns_table", "walk_forward_validate",
]
__version__ = "1.0.0"
