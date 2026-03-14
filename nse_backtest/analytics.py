"""
Performance Analytics & Visualization

Computes all the metrics that matter:
- Returns, CAGR, Sharpe, Sortino, Calmar
- Max drawdown, drawdown duration
- Win rate, profit factor, expectancy
- Trade analysis
- Equity curve charts
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from typing import Optional
import os


def compute_metrics(result: dict, risk_free_rate: float = 0.065) -> dict:
    """
    Compute comprehensive performance metrics.

    Args:
        result: Output from run_backtest()
        risk_free_rate: Annual risk-free rate (default 6.5% for India, ~RBI repo rate)

    Returns:
        Dictionary of performance metrics
    """
    equity = result["equity_curve"]
    trades = result["trades"]
    config = result["config"]
    bh = result["buy_hold_curve"]

    # Basic returns
    total_return = (equity.iloc[-1] / config.initial_capital) - 1
    bh_return = (bh.iloc[-1] / config.initial_capital) - 1

    # Trading days
    n_days = len(equity)
    n_years = n_days / 252

    # CAGR (guard against negative/zero equity)
    final_eq = max(equity.iloc[-1], 0.01)
    cagr = (final_eq / config.initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0
    bh_final = max(bh.iloc[-1], 0.01)
    bh_cagr = (bh_final / config.initial_capital) ** (1 / n_years) - 1 if n_years > 0 else 0

    # Daily returns (guard against division by zero)
    daily_returns = equity.pct_change().dropna()
    daily_returns = daily_returns.replace([np.inf, -np.inf], 0).fillna(0)

    # Sharpe Ratio (annualized)
    excess_daily = daily_returns - risk_free_rate / 252
    std = daily_returns.std()
    sharpe = np.sqrt(252) * excess_daily.mean() / std if std > 1e-10 else 0.0

    # Sortino Ratio
    downside = daily_returns[daily_returns < 0]
    down_std = downside.std() if len(downside) > 1 else 0
    sortino = np.sqrt(252) * excess_daily.mean() / down_std if down_std > 1e-10 else 0.0

    # Max Drawdown
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_dd = drawdown.min()

    # Max Drawdown Duration
    dd_duration = _max_drawdown_duration(equity)

    # Calmar Ratio
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    # Trade metrics
    winning = [t for t in trades if t.pnl > 0]
    losing = [t for t in trades if t.pnl <= 0]
    n_trades = len(trades)
    win_rate = len(winning) / n_trades if n_trades > 0 else 0

    avg_win = np.mean([t.pnl_pct for t in winning]) if winning else 0
    avg_loss = np.mean([t.pnl_pct for t in losing]) if losing else 0

    # Profit Factor
    gross_profit = sum(t.pnl for t in winning)
    gross_loss = abs(sum(t.pnl for t in losing))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (99.99 if gross_profit > 0 else 0.0)

    # Expectancy per trade
    expectancy = sum(t.pnl for t in trades) / n_trades if n_trades > 0 else 0

    # Total costs
    total_costs = sum(t.costs for t in trades)

    # Average holding period
    hold_periods = []
    for t in trades:
        if t.exit_date and t.entry_date:
            hold_periods.append((t.exit_date - t.entry_date).days)
    avg_hold = np.mean(hold_periods) if hold_periods else 0

    return {
        "total_return_pct": total_return * 100,
        "buy_hold_return_pct": bh_return * 100,
        "cagr_pct": cagr * 100,
        "buy_hold_cagr_pct": bh_cagr * 100,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "max_drawdown_pct": max_dd * 100,
        "max_dd_duration_days": dd_duration,
        "total_trades": n_trades,
        "winning_trades": len(winning),
        "losing_trades": n_trades - len(winning),
        "win_rate_pct": win_rate * 100,
        "avg_win_pct": avg_win * 100,
        "avg_loss_pct": avg_loss * 100,
        "profit_factor": profit_factor,
        "expectancy_inr": expectancy,
        "total_costs_inr": total_costs,
        "avg_holding_days": avg_hold,
        "final_equity": equity.iloc[-1],
        "n_years": n_years,
    }


def _max_drawdown_duration(equity: pd.Series) -> int:
    """Find the longest drawdown period in trading days."""
    rolling_max = equity.cummax()
    in_dd = equity < rolling_max
    max_duration = 0
    current = 0
    for v in in_dd:
        if v:
            current += 1
            max_duration = max(max_duration, current)
        else:
            current = 0
    return max_duration


def print_report(metrics: dict, strategy_name: str = "") -> str:
    """Print a clean performance report. Returns the report string."""
    lines = []
    lines.append("=" * 65)
    lines.append(f"  BACKTEST REPORT: {strategy_name}")
    lines.append("=" * 65)
    lines.append("")
    lines.append("  RETURNS")
    lines.append(f"    Total Return:      {metrics['total_return_pct']:>10.2f}%")
    lines.append(f"    Buy & Hold Return: {metrics['buy_hold_return_pct']:>10.2f}%")
    lines.append(f"    CAGR:              {metrics['cagr_pct']:>10.2f}%")
    lines.append(f"    B&H CAGR:          {metrics['buy_hold_cagr_pct']:>10.2f}%")
    lines.append(f"    Final Equity:      ₹{metrics['final_equity']:>12,.0f}")
    lines.append("")
    lines.append("  RISK")
    lines.append(f"    Sharpe Ratio:      {metrics['sharpe_ratio']:>10.2f}")
    lines.append(f"    Sortino Ratio:     {metrics['sortino_ratio']:>10.2f}")
    lines.append(f"    Calmar Ratio:      {metrics['calmar_ratio']:>10.2f}")
    lines.append(f"    Max Drawdown:      {metrics['max_drawdown_pct']:>10.2f}%")
    lines.append(f"    Max DD Duration:   {metrics['max_dd_duration_days']:>10d} days")
    lines.append("")
    lines.append("  TRADES")
    lines.append(f"    Total Trades:      {metrics['total_trades']:>10d}")
    lines.append(f"    Win Rate:          {metrics['win_rate_pct']:>10.1f}%")
    lines.append(f"    Avg Win:           {metrics['avg_win_pct']:>10.2f}%")
    lines.append(f"    Avg Loss:          {metrics['avg_loss_pct']:>10.2f}%")
    lines.append(f"    Profit Factor:     {metrics['profit_factor']:>10.2f}")
    lines.append(f"    Expectancy/Trade:  ₹{metrics['expectancy_inr']:>12,.0f}")
    lines.append(f"    Avg Holding Period: {metrics['avg_holding_days']:>9.0f} days")
    lines.append(f"    Total Costs:       ₹{metrics['total_costs_inr']:>12,.0f}")
    lines.append("")
    lines.append(f"    Period: {metrics['n_years']:.1f} years")
    lines.append("=" * 65)

    report = "\n".join(lines)
    print(report)
    return report


def plot_results(
    result: dict,
    metrics: dict,
    strategy_name: str = "",
    save_path: Optional[str] = None,
) -> str:
    """
    Generate a comprehensive chart with:
    - Equity curve vs buy & hold
    - Drawdown chart
    - Trade markers
    - Key metrics annotation

    Returns path to saved image.
    """
    fig, axes = plt.subplots(3, 1, figsize=(14, 10), height_ratios=[3, 1, 1])
    fig.suptitle(f"Backtest: {strategy_name}", fontsize=14, fontweight="bold")

    equity = result["equity_curve"]
    bh = result["buy_hold_curve"]
    data = result["data"]
    trades = result["trades"]

    # --- Equity Curve ---
    ax1 = axes[0]
    ax1.plot(equity.index, equity.values, label="Strategy", color="#2196F3", linewidth=1.5)
    ax1.plot(bh.index, bh.values, label="Buy & Hold", color="#9E9E9E", linewidth=1, alpha=0.7)

    # Mark trades
    for t in trades:
        color = "#4CAF50" if t.pnl > 0 else "#F44336"
        ax1.axvline(t.entry_date, color=color, alpha=0.15, linewidth=0.5)

    ax1.set_ylabel("Portfolio Value (₹)")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)

    # Metrics box
    metrics_text = (
        f"Return: {metrics['total_return_pct']:.1f}%  |  "
        f"CAGR: {metrics['cagr_pct']:.1f}%  |  "
        f"Sharpe: {metrics['sharpe_ratio']:.2f}  |  "
        f"MaxDD: {metrics['max_drawdown_pct']:.1f}%  |  "
        f"WinRate: {metrics['win_rate_pct']:.0f}%  |  "
        f"Trades: {metrics['total_trades']}"
    )
    ax1.text(
        0.5, 0.02, metrics_text,
        transform=ax1.transAxes, fontsize=8,
        ha="center", va="bottom",
        bbox=dict(boxstyle="round,pad=0.3", facecolor="lightyellow", alpha=0.8),
    )

    # --- Drawdown ---
    ax2 = axes[1]
    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max * 100
    ax2.fill_between(equity.index, drawdown.values, 0, color="#F44336", alpha=0.3)
    ax2.plot(equity.index, drawdown.values, color="#F44336", linewidth=0.8)
    ax2.set_ylabel("Drawdown (%)")
    ax2.grid(True, alpha=0.3)

    # --- Price with signals ---
    ax3 = axes[2]
    ax3.plot(data.index, data["Close"], color="#333333", linewidth=0.8, label="Price")
    ax3.set_ylabel("Stock Price (₹)")
    ax3.set_xlabel("Date")
    ax3.grid(True, alpha=0.3)

    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.YearLocator())

    plt.tight_layout()

    if save_path is None:
        save_path = f"backtest_{strategy_name.replace(' ', '_').replace('/', '_')}.png"
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Chart saved: {save_path}")
    return save_path


def compare_strategies(results_list: list[tuple[str, dict, dict]], save_path: Optional[str] = None) -> str:
    """
    Compare multiple strategies side by side.

    Args:
        results_list: List of (strategy_name, backtest_result, metrics) tuples
    """
    fig, axes = plt.subplots(2, 1, figsize=(14, 8))

    # Equity curves
    ax1 = axes[0]
    colors = ["#2196F3", "#4CAF50", "#FF9800", "#9C27B0", "#F44336", "#00BCD4", "#795548", "#607D8B"]

    for i, (name, result, _) in enumerate(results_list):
        equity = result["equity_curve"]
        # Normalize to percentage returns
        norm = (equity / result["config"].initial_capital - 1) * 100
        ax1.plot(equity.index, norm.values, label=name, color=colors[i % len(colors)], linewidth=1.2)

    # Buy & hold
    bh = results_list[0][1]["buy_hold_curve"]
    bh_norm = (bh / results_list[0][1]["config"].initial_capital - 1) * 100
    ax1.plot(bh.index, bh_norm.values, label="Buy & Hold", color="#9E9E9E", linewidth=1, linestyle="--")

    ax1.set_ylabel("Return (%)")
    ax1.set_title("Strategy Comparison — Cumulative Returns", fontsize=12)
    ax1.legend(loc="upper left", fontsize=8)
    ax1.grid(True, alpha=0.3)

    # Metrics comparison table
    ax2 = axes[1]
    ax2.axis("off")

    headers = ["Strategy", "Return%", "CAGR%", "Sharpe", "MaxDD%", "WinRate%", "PF", "Trades"]
    table_data = []
    for name, _, m in results_list:
        table_data.append([
            name[:25],
            f"{m['total_return_pct']:.1f}",
            f"{m['cagr_pct']:.1f}",
            f"{m['sharpe_ratio']:.2f}",
            f"{m['max_drawdown_pct']:.1f}",
            f"{m['win_rate_pct']:.0f}",
            f"{m['profit_factor']:.2f}",
            str(m['total_trades']),
        ])

    table = ax2.table(
        cellText=table_data,
        colLabels=headers,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.5)

    # Color code: green for best, red for worst in each column
    for col_idx in range(1, len(headers)):
        vals = []
        for row in table_data:
            try:
                v = float(row[col_idx])
                vals.append(v if not np.isinf(v) else 0)
            except:
                vals.append(0)

        if len(set(vals)) <= 1:
            continue  # All same value, skip coloring

        # MaxDD (col 4): least negative = best. Trades (col 7): ambiguous, skip.
        if col_idx == 7:
            continue
        best_idx = vals.index(max(vals))
        worst_idx = vals.index(min(vals))

        if best_idx != worst_idx:
            table[best_idx + 1, col_idx].set_facecolor("#C8E6C9")
            table[worst_idx + 1, col_idx].set_facecolor("#FFCDD2")

    plt.tight_layout()
    if save_path is None:
        save_path = "strategy_comparison.png"
    plt.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Comparison chart saved: {save_path}")
    return save_path
