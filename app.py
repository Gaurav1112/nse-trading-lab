#!/usr/bin/env python3
"""
NSE Trading Lab — Main App
============================

One command to rule them all.

Usage:
    python app.py analyze RELIANCE           # Full analysis + GO/NO-GO
    python app.py analyze ARSSBL --full      # With backtests (slower but better)
    python app.py screen                     # Daily screener on Nifty 50
    python app.py screen --symbols TCS,INFY,RELIANCE  # Custom watchlist
    python app.py backtest RELIANCE          # Run all strategies
    python app.py batch RELIANCE TCS INFY    # Analyze multiple stocks
"""

import sys
import os
import argparse
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nse_backtest.data import fetch_nse, fetch_multiple, NIFTY50_SYMBOLS
from nse_backtest.strategies import STRATEGIES
from nse_backtest.engine import run_backtest, TradeConfig
from nse_backtest.analytics import compute_metrics, print_report, plot_results, compare_strategies
from nse_backtest.scorer import analyze_stock, print_analysis
from nse_backtest.screener import run_screener, print_screener_results


BANNER = """
================================================================================
   NSE TRADING LAB — Automated Stock Analysis & Swing Screener
================================================================================
"""


def cmd_analyze(args):
    """Analyze a single stock — full scoring + GO/NO-GO verdict."""
    symbol = args.symbol.upper()

    print(BANNER)
    print(f"  Analyzing {symbol}...")
    print(f"  Mode: {'Full (with backtests)' if args.full else 'Quick (technical only)'}")
    print(f"  Date range: {args.start} to today")
    print()

    try:
        df = fetch_nse(symbol, start=args.start)
    except Exception as e:
        print(f"  ERROR: Could not fetch data for {symbol}: {e}")
        print(f"  TIP: Try exchange='BO' for BSE, or check the symbol spelling")
        sys.exit(1)

    print(f"\n  Running analysis...")
    start_time = time.time()

    score = analyze_stock(df, symbol, run_backtests=args.full)
    elapsed = time.time() - start_time

    print_analysis(score)

    # Run backtests and show top strategies if --full
    if args.full:
        print(f"\n{'='*65}")
        print(f"  STRATEGY BACKTEST RESULTS")
        print(f"{'='*65}")

        config = TradeConfig(
            initial_capital=args.capital,
            stop_loss_pct=0.07,
        )
        results = []
        for name, strat_func in STRATEGIES.items():
            try:
                sd = strat_func(df)
                if len(sd) == 0:
                    continue
                res = run_backtest(sd, config)
                met = compute_metrics(res)
                label = sd["strategy_name"].iloc[-1]
                results.append((label, res, met))
            except Exception as e:
                print(f"  [warn] strategy '{name}' failed: {e}")

        if results:
            ranked = sorted(results, key=lambda x: x[2]["sharpe_ratio"], reverse=True)
            print("\n  Strategies ranked by Sharpe Ratio:\n")
            for i, (name, _, m) in enumerate(ranked, 1):
                marker = " << RECOMMENDED" if i == 1 and m["sharpe_ratio"] > 0.3 else ""
                print(
                    f"    {i}. {name:<30} Sharpe={m['sharpe_ratio']:>6.2f}  "
                    f"CAGR={m['cagr_pct']:>6.1f}%  MaxDD={m['max_drawdown_pct']:>6.1f}%  "
                    f"WinRate={m['win_rate_pct']:>4.0f}%{marker}"
                )

            # Save comparison chart
            os.makedirs("output", exist_ok=True)
            chart_path = compare_strategies(
                results, save_path=f"output/analysis_{symbol}.png"
            )
            print(f"\n  Chart saved: {chart_path}")

    print(f"\n  Analysis completed in {elapsed:.1f}s")

    # Final recommendation summary
    print(f"\n{'='*65}")
    print(f"  FINAL RECOMMENDATION FOR {symbol}")
    print(f"{'='*65}")
    if score.verdict == "GO":
        print(f"\n  >> {symbol} is a GO at Rs.{score.current_price:,.2f}")
        print(f"  >> Enter around {score.entry_zone}")
        print(f"  >> Set stop-loss at Rs.{score.stop_loss:,.2f}")
        print(f"  >> Target 1: Rs.{score.target_1:,.2f} | Target 2: Rs.{score.target_2:,.2f}")
        print(f"  >> Risk/Reward: {score.risk_reward:.1f}:1")
        if args.capital:
            max_risk = args.capital * 0.02  # 2% risk rule
            sl_per_share = score.current_price - score.stop_loss
            if sl_per_share > 0:
                shares = int(max_risk / sl_per_share)
                position_size = shares * score.current_price
                print(f"\n  Position sizing (2% risk of Rs.{args.capital:,.0f}):")
                print(f"    Buy {shares} shares = Rs.{position_size:,.0f}")
                print(f"    Max loss if SL hit: Rs.{shares * sl_per_share:,.0f}")
    elif score.verdict == "WAIT":
        print(f"\n  >> {symbol} is a WAIT — add to watchlist")
        print(f"  >> Monitor for: trend improvement, volume pickup, or pullback to support")
        print(f"  >> Re-check in 3-5 trading sessions")
    else:
        print(f"\n  >> {symbol} is an AVOID — do not enter")
        print(f"  >> Reasons: weak trend, poor risk/reward, or bad backtest history")
        if score.warnings:
            for w in score.warnings:
                print(f"     ! {w}")
    print()


def cmd_screen(args):
    """Run the daily swing screener."""
    print(BANNER)
    print(f"  DAILY SWING SCREENER")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Scanning: {args.universe}")
    print()

    # Determine stock universe
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
    elif args.universe == "nifty50":
        symbols = NIFTY50_SYMBOLS
    elif args.universe == "nifty100":
        symbols = NIFTY50_SYMBOLS  # Extend with Nifty Next 50 later
    else:
        symbols = NIFTY50_SYMBOLS

    print(f"  Fetching data for {len(symbols)} stocks...")
    stock_data = fetch_multiple(symbols, start=args.start)
    print(f"  Loaded {len(stock_data)} stocks successfully\n")

    if not stock_data:
        print("  ERROR: No stock data loaded. Check your internet connection.")
        return

    # Run screener
    setups = run_screener(stock_data, top_n=args.top)
    print_screener_results(setups)

    # Auto-analyze top picks
    if setups and args.auto_analyze:
        print(f"\n{'='*65}")
        print(f"  AUTO-ANALYZING TOP {min(3, len(setups))} PICKS")
        print(f"{'='*65}")

        for setup in setups[:3]:
            if setup.symbol in stock_data:
                print(f"\n  --- {setup.symbol} ({setup.trigger}) ---")
                score = analyze_stock(
                    stock_data[setup.symbol], setup.symbol, run_backtests=False
                )
                print_analysis(score)

    # Save results
    if setups:
        os.makedirs("output", exist_ok=True)
        rows = []
        for s in setups:
            rows.append({
                "Symbol": s.symbol, "Setup": s.setup_type, "Score": s.score,
                "Price": s.current_price, "Entry": s.entry_price,
                "StopLoss": s.stop_loss, "Target": s.target,
                "RiskReward": s.risk_reward, "Trigger": s.trigger,
            })
        result_df = pd.DataFrame(rows)
        csv_path = f"output/screener_{datetime.now().strftime('%Y%m%d')}.csv"
        result_df.to_csv(csv_path, index=False)
        print(f"\n  Results saved to: {csv_path}")


def cmd_backtest(args):
    """Run full backtest on a stock."""
    symbol = args.symbol.upper()
    print(BANNER)
    print(f"  BACKTESTING: {symbol}")
    print()

    try:
        df = fetch_nse(symbol, start=args.start)
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    config = TradeConfig(
        initial_capital=args.capital,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
    )

    results = []
    strategies = args.strategies.split(",") if args.strategies != "all" else list(STRATEGIES.keys())

    for name in strategies:
        name = name.strip()
        if name not in STRATEGIES:
            print(f"  Unknown strategy: {name}")
            continue

        strat_func = STRATEGIES[name]
        try:
            sd = strat_func(df)
            res = run_backtest(sd, config)
            met = compute_metrics(res)
            label = sd["strategy_name"].iloc[-1]
            print_report(met, f"{symbol} — {label}")

            os.makedirs("output", exist_ok=True)
            plot_results(res, met, f"{symbol} — {label}",
                        save_path=f"output/bt_{symbol}_{name}.png")
            results.append((label, res, met))
        except Exception as e:
            print(f"  ERROR in {name}: {e}")

    if len(results) > 1:
        compare_strategies(results, save_path=f"output/comparison_{symbol}.png")

        ranked = sorted(results, key=lambda x: x[2]["sharpe_ratio"], reverse=True)
        print(f"\n{'='*65}")
        print(f"  RANKING BY SHARPE RATIO")
        print(f"{'='*65}\n")
        for i, (name, _, m) in enumerate(ranked, 1):
            tag = " << BEST" if i == 1 else ""
            print(f"    {i}. {name:<30} Sharpe={m['sharpe_ratio']:.2f}  CAGR={m['cagr_pct']:.1f}%{tag}")

    print(f"\n  Charts saved in: output/")


def cmd_batch(args):
    """Analyze multiple stocks and rank them."""
    symbols = [s.upper() for s in args.symbols]
    print(BANNER)
    print(f"  BATCH ANALYSIS: {', '.join(symbols)}")
    print()

    results = []
    for symbol in symbols:
        try:
            print(f"  Analyzing {symbol}...")
            df = fetch_nse(symbol, start=args.start)
            score = analyze_stock(df, symbol, run_backtests=args.full)
            results.append(score)
            print(f"    Score: {score.final_score:.0f}/100 — {score.verdict}")
        except Exception as e:
            print(f"    ERROR: {e}")

    if not results:
        print("  No stocks analyzed successfully.")
        return

    # Rank by score
    results.sort(key=lambda s: s.final_score, reverse=True)

    print(f"\n{'='*80}")
    print(f"  STOCK RANKINGS")
    print(f"{'='*80}\n")
    print(f"  {'#':>2}  {'Symbol':<12} {'Verdict':<8} {'Score':>5}  {'Trend':>5} {'Mom':>5} {'Vol':>5} {'Volume':>5} {'Risk':>5}  {'R:R':>5}")
    print(f"  {'-'*75}")

    for i, s in enumerate(results, 1):
        marker = " <<" if s.verdict == "GO" else ""
        print(
            f"  {i:>2}  {s.symbol:<12} {s.verdict:<8} {s.final_score:>5.0f}  "
            f"{s.trend_score:>5.0f} {s.momentum_score:>5.0f} {s.volatility_score:>5.0f} "
            f"{s.volume_score:>5.0f} {s.risk_score:>5.0f}  {s.risk_reward:>4.1f}x{marker}"
        )

    # Detailed output for GO stocks
    go_stocks = [s for s in results if s.verdict == "GO"]
    if go_stocks:
        print(f"\n{'='*80}")
        print(f"  ACTIONABLE PICKS ({len(go_stocks)} stocks)")
        print(f"{'='*80}")
        for s in go_stocks:
            print_analysis(s)
    else:
        wait_stocks = [s for s in results if s.verdict == "WAIT"]
        if wait_stocks:
            print(f"\n  No GO signals today. Top WAIT candidates:")
            for s in wait_stocks[:3]:
                print(f"    {s.symbol} — Score {s.final_score:.0f}, needs: ", end="")
                if s.trend_score < 50:
                    print("stronger trend", end=" ")
                if s.momentum_score < 50:
                    print("more momentum", end=" ")
                if s.volume_score < 40:
                    print("volume pickup", end=" ")
                print()


def cmd_demo(args):
    """Run demo with sample data (no internet needed)."""
    from nse_backtest.sample_data import trending_stock, volatile_midcap, sideways_stock

    print(BANNER)
    print("  DEMO MODE — Using synthetic data (no internet needed)")
    print()

    scenarios = {
        "TRENDING_STOCK": trending_stock(),
        "VOLATILE_MIDCAP": volatile_midcap(),
        "SIDEWAYS_STOCK": sideways_stock(),
    }

    for name, df in scenarios.items():
        print(f"\n{'='*65}")
        print(f"  ANALYZING: {name}")
        print(f"{'='*65}")

        score = analyze_stock(df, name, run_backtests=True)
        print_analysis(score)

    # Run screener on all
    print(f"\n{'='*65}")
    print(f"  SCREENER ON DEMO DATA")
    print(f"{'='*65}")
    setups = run_screener(scenarios, top_n=10)
    print_screener_results(setups)


def main():
    parser = argparse.ArgumentParser(
        description="NSE Trading Lab — Automated Stock Analysis & Screening",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app.py analyze RELIANCE              Quick analysis
  python app.py analyze RELIANCE --full       Full analysis with backtests
  python app.py screen                        Scan Nifty 50 for setups
  python app.py screen --symbols TCS,INFY     Scan custom watchlist
  python app.py backtest TCS                  Backtest all strategies
  python app.py batch RELIANCE TCS INFY       Compare multiple stocks
  python app.py demo                          Run demo (no internet)
        """,
    )
    parser.add_argument("--start", default="2020-01-01", help="Start date for data")
    parser.add_argument("--capital", type=float, default=100_000, help="Capital in Rs.")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # analyze
    p_analyze = subparsers.add_parser("analyze", help="Analyze a stock")
    p_analyze.add_argument("symbol", help="NSE stock symbol (e.g., RELIANCE)")
    p_analyze.add_argument("--full", action="store_true", help="Run backtests too (slower)")
    p_analyze.set_defaults(func=cmd_analyze)

    # screen — --symbols and --universe are mutually exclusive
    p_screen = subparsers.add_parser("screen", help="Daily swing screener")
    screen_src = p_screen.add_mutually_exclusive_group()
    screen_src.add_argument("--symbols", help="Comma-separated symbols (overrides universe)")
    screen_src.add_argument("--universe", default="nifty50", choices=["nifty50", "nifty100"])
    p_screen.add_argument("--top", type=int, default=15, help="Max results")
    p_screen.add_argument("--auto-analyze", action="store_true", help="Auto-analyze top picks")
    p_screen.set_defaults(func=cmd_screen)

    # backtest
    p_bt = subparsers.add_parser("backtest", help="Backtest strategies on a stock")
    p_bt.add_argument("symbol", help="NSE stock symbol")
    p_bt.add_argument("--strategies", default="all", help="Comma-separated or 'all'")
    p_bt.add_argument("--stop-loss", type=float, default=0.07, help="Stop loss pct")
    p_bt.add_argument("--take-profit", type=float, default=None, help="Take profit pct")
    p_bt.set_defaults(func=cmd_backtest)

    # batch
    p_batch = subparsers.add_parser("batch", help="Analyze multiple stocks")
    p_batch.add_argument("symbols", nargs="+", help="Stock symbols")
    p_batch.add_argument("--full", action="store_true", help="Run backtests")
    p_batch.set_defaults(func=cmd_batch)

    # demo
    p_demo = subparsers.add_parser("demo", help="Demo mode (no internet)")
    p_demo.set_defaults(func=cmd_demo)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        print("\n  Quick start: python app.py demo")
        # Exit with status 2 (POSIX "command line usage error") so wrappers
        # and CI scripts can detect "no subcommand provided" without parsing
        # output. Previously returned 0 which falsely signalled success.
        sys.exit(2)

    args.func(args)


# Allow: python app.py analyze RELIANCE
# Also allow: python app.py demo
import pandas as pd  # needed for batch cmd

if __name__ == "__main__":
    main()
