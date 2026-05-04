#!/usr/bin/env python3
"""
NSE Backtest Runner
===================
Run multiple strategies on any NSE stock and compare results.

Usage:
    python run_backtest.py                          # Default: RELIANCE, all strategies
    python run_backtest.py TATAMOTORS               # Single stock
    python run_backtest.py ARSSBL --start 2020-01-01  # Custom date range
    python run_backtest.py SBIN --strategy supertrend --stop-loss 0.07

Full strategy list:
    sma_crossover, ema_filtered, rsi_mean_reversion, bollinger_breakout,
    macd_rsi, supertrend, momentum, volume_breakout
"""

import sys
import os
import argparse
import pandas as pd

# Add parent to path so imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nse_backtest.data import fetch_nse
from nse_backtest.strategies import STRATEGIES
from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.analytics import (
    compute_metrics,
    print_report,
    plot_results,
    compare_strategies,
)


def main():
    parser = argparse.ArgumentParser(description="NSE Backtesting Framework")
    parser.add_argument("symbol", nargs="?", default="RELIANCE", help="NSE stock symbol")
    parser.add_argument("--start", default="2018-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=100_000, help="Initial capital (₹)")
    parser.add_argument("--strategy", default="all", help="Strategy name or 'all'")
    parser.add_argument("--stop-loss", type=float, default=None, help="Stop loss % (e.g., 0.07)")
    parser.add_argument("--take-profit", type=float, default=None, help="Take profit % (e.g., 0.15)")
    parser.add_argument("--output-dir", default="output", help="Output directory for charts")

    args = parser.parse_args()

    # Validate --start <= --end early so users see a clear error rather than a
    # silent empty fetch (yfinance returns empty when start > end).
    if args.end is not None:
        try:
            if pd.Timestamp(args.start) > pd.Timestamp(args.end):
                print(f"Error: --start ({args.start}) is after --end ({args.end}).")
                sys.exit(2)
        except Exception as e:
            print(f"Error: invalid --start/--end ({e}).")
            sys.exit(2)

    # Validate --output-dir to defend against path traversal (e.g. "../../etc").
    # Allow the bare default, or any path resolved under the CWD.
    cwd_real = os.path.realpath(os.getcwd())
    out_real = os.path.realpath(args.output_dir)
    if not (out_real == cwd_real or out_real.startswith(cwd_real + os.sep)):
        print(f"Error: --output-dir {args.output_dir!r} resolves outside the working directory.")
        sys.exit(2)
    os.makedirs(out_real, exist_ok=True)
    args.output_dir = out_real

    # Fetch data
    print(f"\n{'='*65}")
    print(f"  NSE BACKTESTER — {args.symbol}")
    print(f"{'='*65}\n")

    try:
        data = fetch_nse(args.symbol, start=args.start, end=args.end)
    except Exception as e:
        print(f"Error fetching data: {e}")
        sys.exit(1)

    # Config
    config = TradeConfig(
        initial_capital=args.capital,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
    )

    # Determine which strategies to run
    if args.strategy == "all":
        strat_names = list(STRATEGIES.keys())
    else:
        if args.strategy not in STRATEGIES:
            print(f"Unknown strategy: {args.strategy}")
            print(f"Available: {', '.join(STRATEGIES.keys())}")
            sys.exit(1)
        strat_names = [args.strategy]

    # Run strategies
    all_results = []
    for name in strat_names:
        print(f"\n--- Running: {name} ---")
        try:
            strat_func = STRATEGIES[name]
            strat_data = strat_func(data)
            result = run_backtest(strat_data, config)
            metrics = compute_metrics(result)
            strategy_label = strat_data["strategy_name"].iloc[-1]
            report = print_report(metrics, strategy_label)
            chart_path = plot_results(
                result, metrics, f"{args.symbol} — {strategy_label}",
                save_path=os.path.join(args.output_dir, f"backtest_{args.symbol}_{name}.png"),
            )
            all_results.append((strategy_label, result, metrics))
        except Exception as e:
            print(f"  ERROR in {name}: {e}")
            import traceback
            traceback.print_exc()

    # Comparison chart (if multiple strategies)
    if len(all_results) > 1:
        print(f"\n{'='*65}")
        print("  STRATEGY COMPARISON")
        print(f"{'='*65}")

        # Rank by Sharpe ratio
        ranked = sorted(all_results, key=lambda x: x[2]["sharpe_ratio"], reverse=True)
        print("\n  Ranked by Sharpe Ratio:\n")
        for i, (name, _, m) in enumerate(ranked, 1):
            emoji = "🏆" if i == 1 else "  "
            print(
                f"  {emoji} {i}. {name:<30} "
                f"Sharpe={m['sharpe_ratio']:.2f}  "
                f"CAGR={m['cagr_pct']:.1f}%  "
                f"MaxDD={m['max_drawdown_pct']:.1f}%  "
                f"WinRate={m['win_rate_pct']:.0f}%"
            )

        compare_path = compare_strategies(
            all_results,
            save_path=os.path.join(args.output_dir, f"comparison_{args.symbol}.png"),
        )

    print(f"\nDone! Charts saved in: {args.output_dir}/")


if __name__ == "__main__":
    main()
