# NSE Trading Lab

A systematic stock analysis, screening & backtesting platform for Indian stock markets (NSE/BSE).

Built to help you **make data-driven trading decisions instead of gut-feel bets**.

## Features

- **Web Dashboard** — Full UI with stock analyzer, screener, backtester, position tracker
- **GO/WAIT/AVOID Verdicts** — 6-dimensional scoring engine rates every stock
- **Swing Screener** — 6 scan types: breakout, reversal, squeeze, volume surge, supertrend flip, trend continuation
- **11 Built-in Strategies** — All backtested with look-ahead bias protection (Zerodha cost model + STT/IPFT)
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

## Trade Desk

The Trade Desk turns a stock you are looking at into one sized decision: **TAKE**, **WAIT** or **SKIP**,
for **NSE** (₹, IST) and **US** (US$, ET) stocks. Each market keeps its own settings, journal rows and
money formatting; amounts are never mixed across markets.

Open it with `streamlit run ui.py` (or `./start.sh`) and pick the **Desk** page (tabs: Today's Plan, Check a Trade,
My Positions, Intraday, Scoreboard), or from the terminal:

```bash
python3.13 -m nse_backtest.desk check RELIANCE            # one stock, swing
python3.13 -m nse_backtest.desk check AAPL --market US --mode intraday
python3.13 -m nse_backtest.desk plan                      # today's TAKE / WATCH lists
python3.13 -m nse_backtest.desk positions                 # HOLD / TIGHTEN_STOP / EXIT
python3.13 -m nse_backtest.desk scoreboard                # paper record of logged TAKEs (all markets)
python3.13 -m nse_backtest.desk scoreboard --market US --json
```

- **TAKE**: no check blocks and the current price (live quote, else the last close with a WARN) is
  within about 1% of the planned entry (intraday: at or below the stop-limit's limit price). You get a
  ticket to type into your broker yourself.
- **WAIT**: nothing blocks, but the price is below the entry (buy only above the trigger) or has run more
  than 1% past it (intraday: above the stop-limit's limit, so the order would not fill). The ticket is
  shown as "IF TRIGGERED".
- **SKIP**: at least one check BLOCKs (named in the headline), or there is no usable current price.

### The 17 checks

Missing or failed data is never a PASS: it becomes a WARN or a BLOCK.

| # | Check | BLOCK when | WARN when |
|---|---|---|---|
| 1 | `regime` | swing and the index regime is HOSTILE or unknown | swing MIXED; intraday HOSTILE or unknown |
| 2 | `reward_risk` | reward:risk to T1 is below the minimum (default 1:1.5) | — |
| 3 | `size` | sizing gives less than one share | — |
| 4 | `risk_governor` | the portfolio governor says no new trades | the governor could not be evaluated |
| 5 | `daily_loss` | today's realised loss reached the daily limit, or no limit is set | the journal (or today's P&L) is unreadable |
| 6 | `ban_list` | NSE: the stock is in the F&O ban list | the ban list could not be verified or is stale |
| 7 | `monte_carlo` | no odds could be computed, or simulated expectancy is below 0R | expectancy is under +0.10R |
| 8 | `freshness` | the last daily bar misses one or more completed trading sessions (3 calendar days in a year with no holiday calendar), or the last 15-min bar is older than 30 min | the last bar is in the future, today's bar is unfinished, or the year has no holiday calendar |
| 9 | `live_price` | the live price is at or below the stop | no live quote (last close used), or the quote is over 20 min old in session |
| 10 | `entry_window` | intraday outside the entry window, on a weekend or exchange holiday, or on an early-close day | the year has no holiday calendar |
| 11 | `liquidity` | 20-day median turnover below the market minimum | turnover unknown |
| 12 | `earnings` | — | earnings inside the swing hold window, today (intraday), or unknown |
| 13 | `setup_history` | intraday: under 20 past sessions, or under +0.2R, or no history | swing: under 20 samples, or negative expectancy |
| 14 | `stoploss_streak` | — | 3 losses in a row (size is halved), or the journal is unreadable |
| 15 | `gap` | — | today's gap is over 2%, or unknown (no live day-open) |
| 16 | `robustness` | — | over 25% of candle-noise replays lose, or the test was not run |
| 17 | `pdt` | US intraday with the PDT guard on: 3+ day trades in the last 5 weekdays | the day-trade count is unknown |

Besides the BLOCK conditions in the table, the desk also fails closed (BLOCK) when the last bar's time cannot
be read, when the clock has no timezone, when the Monte Carlo expectancy is not a finite number, or when the
live price is not a valid number.

### The Time Machine

The odds come from the stock's own bars, not from a model. A block-bootstrap Monte Carlo replays the exact
entry, stop, target and hold thousands of times (fixed seed, stop wins a bar that touches both), giving
P(target), P(stop) and expectancy in R after costs. A setup history replays every past time the same setup
fired on that stock. Robustness tests shuffle the trade order (drawdown, losing streaks) and jitter the
candles to see whether the edge survives noise.

### The Scoreboard

The Scoreboard checks whether the desk's own TAKEs made money. It reads every TAKE in
`desk_audit.jsonl` (written by Check a Trade, in the page or the CLI, and by Today's Plan) and replays
each one against the prices that followed, as if the ticket had been typed in exactly. It is the
desk's paper record, **not your real P&L**: it knows nothing about the orders you actually placed.
It is read-only and places nothing.
Covers TAKEs from Check a Trade and Today's Plan; Intraday-tab scan TAKEs are not logged yet.

Open the **Scoreboard** tab and press "Load scoreboard", or run
`python3.13 -m nse_backtest.desk scoreboard [--market NSE|US|ALL] [--json]` (default ALL).

- One TAKE per stock, mode and session is counted (the first one logged).
- Fills and exits are conservative. Swing: a one-session LIMIT at the entry, filled if the day's low
  reaches it; on the fill day only the stop can trigger; then up to the planned hold (15 bars by
  default) and a time exit at the last close. A swing TAKE logged while its fill session was open is
  filled from the 15-minute bars after the verdict (the day's earlier low happened before the order).
  Intraday: a BUY stop-limit, as the ticket prints it (trigger at the entry, limit = entry + the smaller
  of 0.25R and 0.5%, rounded up to the tick): 15-minute bars after the verdict; the first bar whose high
  reaches the trigger fills at its open or the trigger, whichever is higher, when it opens at or below the
  limit; a bar opening above the limit leaves a resting limit, filled at the limit when price trades back
  to it. Flat by the session's flat-by time. The Time Machine's intraday history uses the same rule. When
  a bar touches both stop and target, the stop wins.
- Swing trades are replayed on unadjusted daily bars. A missing trading session in the bars used, or
  prices re-scaled since the ticket (a split or bonus: the logged last close no longer matches), makes
  the trade `data_unavailable` instead of a guess.
- Realised R = (exit − fill − cost per share) ÷ (entry − stop). A win is realised R above 0.
- Each trade gets a status: `won`, `lost`, `time` (time exit), `not_filled`, `open` (still running;
  shown marked to the last close, not counted) or `data_unavailable` (bars could not be loaded, have
  gaps, or are missing). Nothing is dropped silently; malformed audit lines are listed by line number.
- Per market and mode it shows trades, win %, mean realised R against the mean predicted Monte Carlo
  and history R, a 95% bootstrap interval of the mean realised R, max drawdown in R and a cumulative
  R chart.

The verdict, per market and mode, is the first of these that applies:

| Verdict | When |
|---|---|
| Not enough trades yet (n/30) | fewer than 30 resolved trades (won, lost or time) |
| Losing — do not trade this with real money | the whole 95% interval is below 0R |
| Inconclusive (trades could not be replayed) | more than 10% of resolved + unavailable trades are `data_unavailable` |
| Inconclusive (odds missing) | under 80% of the resolved trades carry a Monte Carlo prediction |
| Worse than the desk's odds — do not size up | the whole interval is below the mean predicted R |
| Matching the odds — real money at small size (≤0.5% risk) | the interval is above 0R and the mean realised R is within 0.15R of the prediction |
| Inconclusive — keep paper-trading | anything else |

Example (fictional): `NSE swing: Not enough trades yet (12/30) — keep paper-trading`.

Final results (won, lost, time, not_filled) are cached in `desk_scoreboard.json` in the data directory
(`~/.nse-trading-lab/`, or `NSE_LAB_DATA_DIR`), so they are not fetched again; open and unavailable
trades are recomputed each time. yfinance only serves 15-minute bars for about the last 60 days, so an
intraday TAKE older than that can only be shown from the cache. **Deleting `desk_scoreboard.json` loses
those intraday results for good** (they come back as `data_unavailable`). A corrupt cache, or one from
another scoreboard version, is renamed to a backup file, not overwritten.

### Holiday calendars

`nse_backtest/desk/calendars.py` holds exchange holidays and early closes:

- NSE: 2026. NSE 2027 is not published yet.
- US (NYSE): 2026 and 2027, including the 1:00 pm ET early closes.

The desk uses them for daily-data freshness (counted in trading sessions, so a long weekend is not
stale), regime staleness, the intraday entry window (BLOCK on a holiday: "Market closed today: ...";
BLOCK on an early-close day: no new intraday entries), the live-quote "market closed today" note,
partial-bar handling, and the Scoreboard's fill sessions. In a year without a calendar the desk assumes
every weekday trades, falls back to a 3-calendar-day freshness rule, and WARNs "holiday calendar not
available for {year}". NSE Muhurat trading is not supported: that Sunday is treated as a weekend and a
Muhurat bar from yfinance is dropped.

**Yearly maintenance:** when NSE publishes the next year's trading holiday circular, add the dates to `HOLIDAYS["NSE"]`, add the year to `COVERED_YEARS["NSE"]`, and record the
circular's URL in the module docstring next to the existing sources. Do the same for NYSE from
nyse.com/markets/hours-calendars (holidays and `EARLY_CLOSES`). Use the trading-holiday table, not the
clearing-holiday table.

### What the desk never does

- **Nothing is ever placed.** There is no order API anywhere in this repo; you type every order yourself.
- `.mcp.json` points Claude sessions at the hosted Kite MCP for **read-only** use (holdings, quotes).
  The desk does not use it to trade.
- `.claude/skills/trader-news` and `trader-analyst` are vendored research skills (MIT, audited, see
  `.claude/skills/VENDORED.md`). They are never inputs to a desk verdict.

Example (fictional): a TAKE might read `TAKE ABC — BUY 40 @ ₹1,000.00, loss at stop ≈ ₹980.00 (a gap
can lose more)`: the loss at the stop includes costs, and a gap through the stop can lose more.

> **Disclaimer:** the Trade Desk is a paper-trading and education tool, not financial advice. Its odds
> are a reality check, not a forecast. Paper-trade until your record matches the odds.

### Tests

The full suite is the gate, pipeline tests included (the Fyers SDK is imported only when a Fyers
client is created, so it is not needed to run them):

```bash
python3.13 -m pytest -o addopts=""
```

## Project Structure

```
nse-trading-lab/
├── app.py                     # MAIN ENTRY POINT — one command for everything
├── nse_backtest/              # Core framework
│   ├── __init__.py
│   ├── data.py                # NSE/BSE data fetching (yfinance)
│   ├── strategies.py          # 11 built-in strategies
│   ├── engine.py              # Backtesting engine with Zerodha costs
│   ├── analytics.py           # Metrics, reports, charts
│   ├── scorer.py              # GO/NO-GO scoring engine (6 dimensions)
│   ├── screener.py            # Daily swing screener (6 scan types)
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
| `python app.py analyze SYMBOL --full` | Above + backtests all 11 strategies |
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
| Trend Continuation | Strong established uptrend with pullback entry |

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

The engine uses **Zerodha delivery trading costs** by default and switches to
intraday rates automatically when ``TradeConfig.trading_mode = "INTRADAY"``:

- Brokerage: ₹0 (delivery) / min(₹20, 0.03% turnover) per order (intraday)
- STT: 0.1% sell (delivery), 0.025% sell (intraday)
- Stamp duty: 0.015% buy (delivery), 0.003% buy (intraday)
- GST: 18% on brokerage + transaction charges
- Slippage: 0.1% assumed
- DP charge: ₹15.93 per delivery sell

**MTF mode** (Margin Trade Facility): set ``trading_mode="MTF"`` — adds an
interest accrual on the borrowed portion (default 18% p.a.) per holding day,
and applies a leverage factor configurable via ``mtf_margin_pct``. The MTF
position tracker page in the UI projects breakeven drift and exit scenarios.

You can customize in `TradeConfig`:
```python
from nse_backtest.engine import TradeConfig

config = TradeConfig(
    initial_capital=200_000,
    trading_mode="INTRADAY",  # or "DELIVERY" / "MTF"
    stop_loss_pct=0.07,       # 7% stop loss
    take_profit_pct=0.15,     # 15% target
    position_pct=0.5,         # Use 50% of capital per trade
)
```

### Environment Variables

- ``NSE_LOG_LEVEL`` — logging level for ``nse_backtest`` (DEBUG / INFO / WARNING / ERROR). Default: INFO.
- ``NSE_LOG_FILE`` — optional path; if set, logs are also written to this file.

## Key Metrics Explained

- **Sharpe Ratio**: Risk-adjusted return. Above 1.0 = good, above 1.5 = excellent
- **Max Drawdown**: Worst peak-to-trough decline. Keep under 25% ideally
- **Profit Factor**: Gross wins / gross losses. Above 2.0 = strong edge
- **Win Rate**: % of profitable trades. Context-dependent (low WR + high avg win = fine)
- **Expectancy**: Average ₹ profit per trade. Must be positive for edge

## Roadmap

- [x] Core backtesting engine with Zerodha costs (incl. STT, IPFT, GST, stamp duty)
- [x] 11 built-in strategies
- [x] Strategy comparison and ranking
- [x] Equity curves, drawdown charts, metrics
- [x] Daily stock screener (6 scan types: breakout, reversal, squeeze, volume surge, supertrend flip, trend continuation)
- [x] MTF position risk dashboard (with leverage + interest accrual)
- [x] Multi-stock portfolio backtesting
- [x] 3-round 16-expert adversarial security/correctness audit (61 regression tests)
- [ ] Kite Connect integration for live alerts
- [ ] Walk-forward optimization
- [ ] Sector rotation strategy

## Important Disclaimer

This is a **research and education tool**. Past performance does not guarantee future results. Always paper-trade strategies before using real money. I am not a financial advisor — this is a systematic approach to testing ideas, not investment advice.

## License

MIT — use freely, modify as needed.
