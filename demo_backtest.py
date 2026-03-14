#!/usr/bin/env python3
"""
Demo: Run all strategies on sample data to demonstrate the framework.
Use this when yfinance is unavailable (e.g., restricted network).
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nse_backtest.sample_data import trending_stock, volatile_midcap, sideways_stock
from nse_backtest.strategies import STRATEGIES
from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.analytics import (
    compute_metrics,
    print_report,
    plot_results,
    compare_strategies,
)

OUTPUT_DIR = "output"


def run_all_strategies(data, stock_label, config):
    """Run all strategies on given data, return results list."""
    results = []
    for name, strat_func in STRATEGIES.items():
        try:
            strat_data = strat_func(data)
            result = run_backtest(strat_data, config)
            metrics = compute_metrics(result)
            strategy_label = strat_data["strategy_name"].iloc[-1]
            print_report(metrics, f"{stock_label} — {strategy_label}")

            chart_path = plot_results(
                result, metrics, f"{stock_label} — {strategy_label}",
                save_path=os.path.join(OUTPUT_DIR, f"bt_{stock_label}_{name}.png"),
            )
            results.append((strategy_label, result, metrics))
        except Exception as e:
            print(f"  ERROR in {name}: {e}")
            import traceback
            traceback.print_exc()
    return results


def main():
    # --- Config: ₹1 Lakh, 7% stop-loss, Zerodha costs ---
    config = TradeConfig(
        initial_capital=100_000,
        stop_loss_pct=0.07,
        take_profit_pct=None,  # Let strategies decide exits
    )

    # --- Test 1: Trending Stock (like a strong Nifty 50 stock) ---
    print("\n" + "=" * 65)
    print("  SCENARIO 1: TRENDING STOCK (strong uptrend)")
    print("=" * 65)
    data1 = trending_stock()
    results1 = run_all_strategies(data1, "TRENDING", config)

    if len(results1) > 1:
        compare_strategies(
            results1,
            save_path=os.path.join(OUTPUT_DIR, "comparison_TRENDING.png"),
        )
        ranked = sorted(results1, key=lambda x: x[2]["sharpe_ratio"], reverse=True)
        print("\n  RANKING (by Sharpe):")
        for i, (name, _, m) in enumerate(ranked, 1):
            tag = " *** BEST ***" if i == 1 else ""
            print(f"    {i}. {name:<30} Sharpe={m['sharpe_ratio']:.2f}  CAGR={m['cagr_pct']:.1f}%{tag}")

    # --- Test 2: Volatile Mid-cap (like ARSSBL) ---
    print("\n" + "=" * 65)
    print("  SCENARIO 2: VOLATILE MID-CAP (high volatility)")
    print("=" * 65)
    data2 = volatile_midcap()
    results2 = run_all_strategies(data2, "VOLATILE", config)

    if len(results2) > 1:
        compare_strategies(
            results2,
            save_path=os.path.join(OUTPUT_DIR, "comparison_VOLATILE.png"),
        )
        ranked = sorted(results2, key=lambda x: x[2]["sharpe_ratio"], reverse=True)
        print("\n  RANKING (by Sharpe):")
        for i, (name, _, m) in enumerate(ranked, 1):
            tag = " *** BEST ***" if i == 1 else ""
            print(f"    {i}. {name:<30} Sharpe={m['sharpe_ratio']:.2f}  CAGR={m['cagr_pct']:.1f}%{tag}")

    # --- Test 3: Sideways Stock ---
    print("\n" + "=" * 65)
    print("  SCENARIO 3: SIDEWAYS STOCK (range-bound)")
    print("=" * 65)
    data3 = sideways_stock()
    results3 = run_all_strategies(data3, "SIDEWAYS", config)

    if len(results3) > 1:
        compare_strategies(
            results3,
            save_path=os.path.join(OUTPUT_DIR, "comparison_SIDEWAYS.png"),
        )
        ranked = sorted(results3, key=lambda x: x[2]["sharpe_ratio"], reverse=True)
        print("\n  RANKING (by Sharpe):")
        for i, (name, _, m) in enumerate(ranked, 1):
            tag = " *** BEST ***" if i == 1 else ""
            print(f"    {i}. {name:<30} Sharpe={m['sharpe_ratio']:.2f}  CAGR={m['cagr_pct']:.1f}%{tag}")

    print("\n" + "=" * 65)
    print(f"  All charts saved in: {OUTPUT_DIR}/")
    print("=" * 65)


if __name__ == "__main__":
    main()
