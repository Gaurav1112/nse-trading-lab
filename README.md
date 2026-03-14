# NSE Trading Lab

A systematic stock analysis, screening & backtesting platform for Indian stock markets (NSE/BSE).

Built to help you **make data-driven trading decisions instead of gut-feel bets**.

## Features

- **Web Dashboard** — Full UI with stock analyzer, screener, backtester, position tracker
- **GO/WAIT/AVOID Verdicts** — 6-dimensional scoring engine rates every stock
- **Swing Screener** — 5 scan types: breakout, reversal, squeeze, volume surge, supertrend flip
- **8 Built-in Strategies** — All backtested with look-ahead bias protection
- **Zerodha Cost Model** — Exact STT, stamp duty, GST, slippage
- **MTF Position Tracker** — Interest burn projection, breakeven drift, exit scenarios
- **Position Sizing** — 2% risk rule with exact share counts

## Quick Start

```bash
# 1. Setup
unzip nse-trading-lab.zip
cd nse-trading-lab
python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows
pip install -r requirements.txt

# 2. Launch the Web UI
streamlit run ui.py
# Opens http://localhost:8501 in your browser

# Or use the launch script
chmod +x start.sh && ./start.sh
```

## Alternative: Command Line

```bash
# Analyze any stock
python app.py analyze RELIANCE --full

# Run screener
python app.py screen

# Backtest strategies
python app.py backtest TCS --stop-loss 0.07

# Compare stocks
python app.py batch RELIANCE TCS INFY SBIN

# Demo mode (no internet)
python app.py demo
```

## Project Structure

```
nse-trading-lab/
├── app.py                     # MAIN ENTRY POINT — one command for everything
├── nse_backtest/              # Core framework
│   ├── __init__.py
│   ├── data.py                # NSE/BSE data fetching (yfinance)
│   ├── strategies.py          # 8 built-in strategies
│   ├── engine.py              # Backtesting engine with Zerodha costs
│   ├── analytics.py           # Metrics, reports, charts
│   ├── scorer.py              # GO/NO-GO scoring engine (6 dimensions)
│   ├── screener.py            # Daily swing screener (5 scan types)
│   └── sample_data.py         # Synthetic data for testing
├── custom_strategies/         # Your own strategies go here
│   └── example_strategy.py
├── notebooks/                 # Jupyter notebooks for exploration
│   └── 01_getting_started.ipynb
├── output/                    # Generated charts, reports, CSVs
├── run_backtest.py            # Standalone backtest runner
├── demo_backtest.py           # Demo with sample data
├── requirements.txt
├── .gitignore
└── README.md
```

## Commands

| Command | What it does |
|---------|-------------|
| `python app.py analyze SYMBOL` | Full technical analysis + GO/NO-GO verdict |
| `python app.py analyze SYMBOL --full` | Above + backtests all 8 strategies |
| `python app.py screen` | Scan Nifty 50 for swing setups |
| `python app.py screen --symbols X,Y,Z` | Scan custom watchlist |
| `python app.py backtest SYMBOL` | Backtest all strategies with charts |
| `python app.py batch SYM1 SYM2 SYM3` | Rank multiple stocks side by side |
| `python app.py demo` | Run everything on sample data (no internet) |

## How The Scoring Works

The analyzer scores each stock on 6 dimensions (0-100 each):

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| Trend (25%) | EMA stack, ADX, price slope, higher highs/lows |
| Momentum (20%) | RSI, MACD, StochRSI, Rate of Change |
| Volume (15%) | Volume vs average, OBV trend, volume spikes |
| Volatility (10%) | ATR range, BB squeeze, vol contraction |
| Backtest (15%) | How many strategies are profitable on this stock |
| Risk (15%) | Distance from highs, support proximity, risk/reward |

**Verdict:** Score >= 65 = GO, 45-65 = WAIT, < 45 = AVOID

## Swing Screener Scan Types

| Scan | What it detects |
|------|----------------|
| Breakout | Price crossing 20 EMA or 20-day high with volume |
| Reversal | RSI oversold bounce in uptrending stock |
| Squeeze | Bollinger Band squeeze about to expand |
| Volume Surge | Unusual volume spike with bullish candle |
| Supertrend Flip | Supertrend indicator turning bullish |

## Built-in Strategies

| Strategy | Type | Best For | Key Parameters |
|----------|------|----------|---------------|
| `sma_crossover` | Trend | Strong trending stocks | fast=20, slow=50 |
| `ema_filtered` | Trend + Filter | Uptrending large caps | fast=9, slow=21, trend=200 |
| `rsi_mean_reversion` | Mean Reversion | Range-bound quality stocks | period=14, oversold=30 |
| `bollinger_breakout` | Breakout | Post-consolidation moves | period=20, std=2.0 |
| `macd_rsi` | Combo | Confirmed momentum | MACD + RSI>50 filter |
| `supertrend` | Trend | Popular in Indian markets | period=10, multiplier=3.0 |
| `momentum` | Momentum | Sector leaders | lookback=90, hold=20 |
| `volume_breakout` | Breakout | Institutional buying | period=20, vol_mult=2.0 |

## Adding Your Own Strategy

Create a file in `custom_strategies/` following this pattern:

```python
import pandas as pd

def my_strategy(df: pd.DataFrame, **params) -> pd.DataFrame:
    """
    Your strategy logic here.
    Must add a 'signal' column: 1=Buy, -1=Sell, 0=Hold
    """
    data = df.copy()
    # ... your logic ...
    data["signal"] = 0
    # Set buy/sell conditions
    data.loc[buy_condition, "signal"] = 1
    data.loc[sell_condition, "signal"] = -1
    data["strategy_name"] = "My Strategy"
    return data
```

Then register it in `nse_backtest/strategies.py`:
```python
from custom_strategies.example_strategy import my_strategy
STRATEGIES["my_strategy"] = my_strategy
```

## Transaction Cost Model

The engine uses **Zerodha delivery trading costs** by default:
- Brokerage: ₹0 (equity delivery)
- STT: 0.1% on sell side
- GST: 18% on brokerage
- Slippage: 0.1% assumed
- Commission: 0.1% round-trip buffer

You can customize in `TradeConfig`:
```python
from nse_backtest.engine import TradeConfig

config = TradeConfig(
    initial_capital=200_000,
    stop_loss_pct=0.07,       # 7% stop loss
    take_profit_pct=0.15,     # 15% target
    position_pct=0.5,         # Use 50% of capital per trade
)
```

## Key Metrics Explained

- **Sharpe Ratio**: Risk-adjusted return. Above 1.0 = good, above 1.5 = excellent
- **Max Drawdown**: Worst peak-to-trough decline. Keep under 25% ideally
- **Profit Factor**: Gross wins / gross losses. Above 2.0 = strong edge
- **Win Rate**: % of profitable trades. Context-dependent (low WR + high avg win = fine)
- **Expectancy**: Average ₹ profit per trade. Must be positive for edge

## Roadmap

- [x] Core backtesting engine with Zerodha costs
- [x] 8 built-in strategies
- [x] Strategy comparison and ranking
- [x] Equity curves, drawdown charts, metrics
- [ ] Daily stock screener (momentum/mean-reversion)
- [ ] MTF position risk dashboard
- [ ] Kite Connect integration for alerts
- [ ] Walk-forward optimization
- [ ] Multi-stock portfolio backtesting
- [ ] Sector rotation strategy

## Important Disclaimer

This is a **research and education tool**. Past performance does not guarantee future results. Always paper-trade strategies before using real money. I am not a financial advisor — this is a systematic approach to testing ideas, not investment advice.

## License

MIT — use freely, modify as needed.
