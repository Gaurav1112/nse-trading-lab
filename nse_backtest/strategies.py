"""
Trading Strategies for NSE Backtesting

Each strategy is a function that takes a DataFrame with OHLCV data
and returns a DataFrame with an added 'signal' column:
    1  = Buy/Long
   -1  = Sell/Short (or exit)
    0  = Hold/No action

Add your own strategies by following the same pattern.
"""

import pandas as pd
import numpy as np
from ta import trend, momentum, volatility


# ---------------------------------------------------------------------------
# STRATEGY 1: Dual Moving Average Crossover
# ---------------------------------------------------------------------------
def sma_crossover(df: pd.DataFrame, fast: int = 20, slow: int = 50) -> pd.DataFrame:
    """
    Classic golden cross / death cross.
    BUY when fast SMA crosses above slow SMA.
    SELL when fast SMA crosses below slow SMA.
    """
    data = df.copy()
    data["sma_fast"] = data["Close"].rolling(fast).mean()
    data["sma_slow"] = data["Close"].rolling(slow).mean()

    data["signal"] = 0
    fast_above = (data["sma_fast"] > data["sma_slow"]).fillna(False)
    fast_below_eq = (data["sma_fast"] <= data["sma_slow"]).fillna(False)
    data.loc[fast_above, "signal"] = 1
    data.loc[fast_below_eq & ~fast_above, "signal"] = -1

    data["strategy_name"] = f"SMA({fast}/{slow})"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 2: EMA Crossover with Trend Filter
# ---------------------------------------------------------------------------
def ema_crossover_filtered(
    df: pd.DataFrame, fast: int = 9, slow: int = 21, trend_period: int = 200
) -> pd.DataFrame:
    """
    EMA crossover but ONLY takes long trades when price is above 200 EMA (uptrend).
    Avoids buying in downtrends — key for Indian mid/small caps.
    """
    data = df.copy()
    data["ema_fast"] = data["Close"].ewm(span=fast, adjust=False).mean()
    data["ema_slow"] = data["Close"].ewm(span=slow, adjust=False).mean()
    data["ema_trend"] = data["Close"].ewm(span=trend_period, adjust=False).mean()

    data["signal"] = 0
    long_cond = (
        (data["ema_fast"] > data["ema_slow"]) & (data["Close"] > data["ema_trend"])
    ).fillna(False)
    data.loc[long_cond, "signal"] = 1
    data.loc[~long_cond & data["ema_trend"].notna(), "signal"] = -1

    data["strategy_name"] = f"EMA({fast}/{slow}) + Trend({trend_period})"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 3: RSI Mean Reversion
# ---------------------------------------------------------------------------
def rsi_mean_reversion(
    df: pd.DataFrame,
    period: int = 14,
    oversold: float = 30,
    overbought: float = 70,
) -> pd.DataFrame:
    """
    Buy when RSI drops below oversold, HOLD until RSI reaches overbought.
    Classic mean reversion — works well on large caps with strong fundamentals.
    """
    data = df.copy()
    data["rsi"] = momentum.RSIIndicator(data["Close"], window=period).rsi()

    data["signal"] = 0
    in_position = False
    for i in range(len(data)):
        rsi_val = data["rsi"].iloc[i]
        if pd.isna(rsi_val):
            continue
        if not in_position and rsi_val < oversold:
            in_position = True
        elif in_position and rsi_val > overbought:
            in_position = False
            data.iloc[i, data.columns.get_loc("signal")] = -1
            continue

        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1

    data["strategy_name"] = f"RSI({period}) MR [{oversold}/{overbought}]"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 4: Bollinger Band Squeeze Breakout
# ---------------------------------------------------------------------------
def bollinger_breakout(
    df: pd.DataFrame, period: int = 20, std_dev: float = 2.0
) -> pd.DataFrame:
    """
    Buy on upper band breakout, HOLD until price drops below middle band.
    Captures trending moves after consolidation without premature exits.
    """
    data = df.copy()
    bb = volatility.BollingerBands(data["Close"], window=period, window_dev=std_dev)
    data["bb_upper"] = bb.bollinger_hband()
    data["bb_lower"] = bb.bollinger_lband()
    data["bb_mid"] = bb.bollinger_mavg()

    data["signal"] = 0
    in_position = False
    for i in range(len(data)):
        if pd.isna(data["bb_upper"].iloc[i]):
            continue
        if not in_position and data["Close"].iloc[i] > data["bb_upper"].iloc[i]:
            in_position = True
        elif in_position and data["Close"].iloc[i] < data["bb_mid"].iloc[i]:
            in_position = False
            data.iloc[i, data.columns.get_loc("signal")] = -1
            continue

        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1

    data["strategy_name"] = f"BB Breakout({period}, {std_dev}σ)"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 5: MACD + RSI Combo
# ---------------------------------------------------------------------------
def macd_rsi_combo(
    df: pd.DataFrame,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    rsi_period: int = 14,
    rsi_threshold: float = 50,
) -> pd.DataFrame:
    """
    Entry: MACD histogram turns positive AND RSI > 50 (both must agree).
    Exit: MACD histogram turns negative AND RSI < 45 (both must weaken).
    This prevents whipsawing from one indicator flickering.
    """
    data = df.copy()

    macd_ind = trend.MACD(data["Close"], macd_fast, macd_slow, macd_signal)
    data["macd_hist"] = macd_ind.macd_diff()
    data["rsi"] = momentum.RSIIndicator(data["Close"], window=rsi_period).rsi()

    data["signal"] = 0
    in_position = False
    for i in range(len(data)):
        mh = data["macd_hist"].iloc[i]
        rsi_val = data["rsi"].iloc[i]
        if pd.isna(mh) or pd.isna(rsi_val):
            continue

        if not in_position and mh > 0 and rsi_val > rsi_threshold:
            in_position = True
        elif in_position and mh < 0 and rsi_val < (rsi_threshold - 5):
            in_position = False
            data.iloc[i, data.columns.get_loc("signal")] = -1
            continue

        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1

    data["strategy_name"] = f"MACD({macd_fast}/{macd_slow}) + RSI({rsi_period})"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 6: Supertrend (very popular in Indian markets)
# ---------------------------------------------------------------------------
def supertrend(
    df: pd.DataFrame, period: int = 10, multiplier: float = 3.0
) -> pd.DataFrame:
    """
    Supertrend indicator — extremely popular among Indian traders.
    BUY when price is above supertrend line, SELL when below.
    """
    data = df.copy()
    hl2 = (data["High"] + data["Low"]) / 2
    atr = volatility.AverageTrueRange(
        data["High"], data["Low"], data["Close"], window=period
    ).average_true_range()

    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)

    supertrend_line = pd.Series(index=data.index, dtype=float)
    direction = pd.Series(index=data.index, dtype=int)

    # Seed the line based on the very first close vs. the first hl2 band.
    if len(data) > 0:
        first_close = data["Close"].iloc[0]
        if pd.notna(first_close) and first_close > hl2.iloc[0]:
            direction.iloc[0] = 1
            supertrend_line.iloc[0] = lower_band.iloc[0]
        else:
            direction.iloc[0] = -1
            supertrend_line.iloc[0] = upper_band.iloc[0]

    for i in range(1, len(data)):
        if data["Close"].iloc[i] > upper_band.iloc[i - 1]:
            direction.iloc[i] = 1
        elif data["Close"].iloc[i] < lower_band.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

        if direction.iloc[i] == 1:
            supertrend_line.iloc[i] = max(lower_band.iloc[i], supertrend_line.iloc[i - 1])
        else:
            supertrend_line.iloc[i] = min(upper_band.iloc[i], supertrend_line.iloc[i - 1])

    data["supertrend"] = supertrend_line
    data["signal"] = direction

    data["strategy_name"] = f"Supertrend({period}, {multiplier}x)"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 7: Momentum / Relative Strength (Dual Momentum)
# ---------------------------------------------------------------------------
def momentum_strategy(
    df: pd.DataFrame,
    lookback: int = 90,
    hold_days: int = 20,
) -> pd.DataFrame:
    """
    Buy when stock has positive absolute momentum over lookback period.
    Rebalance every hold_days. Simple but effective for trending stocks.
    """
    data = df.copy()
    data["returns_lookback"] = data["Close"].pct_change(lookback)

    data["signal"] = 0
    # Generate signals every hold_days
    for i in range(lookback, len(data), hold_days):
        end_idx = min(i + hold_days, len(data))
        if data["returns_lookback"].iloc[i] > 0:
            data.iloc[i:end_idx, data.columns.get_loc("signal")] = 1
        else:
            data.iloc[i:end_idx, data.columns.get_loc("signal")] = -1

    data["strategy_name"] = f"Momentum({lookback}d, hold={hold_days}d)"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 8: Volume Breakout
# ---------------------------------------------------------------------------
def volume_breakout(
    df: pd.DataFrame,
    price_period: int = 20,
    vol_multiplier: float = 2.0,
) -> pd.DataFrame:
    """
    Buy when price breaks above N-day high on above-average volume.
    Great for catching institutional buying in Indian mid-caps.
    """
    data = df.copy()
    data["high_n"] = data["High"].rolling(price_period).max().shift(1)
    data["vol_avg"] = data["Volume"].rolling(price_period).mean()

    data["signal"] = 0
    breakout = (data["Close"] > data["high_n"]) & (
        data["Volume"] > vol_multiplier * data["vol_avg"]
    )
    data.loc[breakout, "signal"] = 1

    # Hold for price_period days after breakout, then exit
    in_trade = False
    hold_count = 0
    for i in range(len(data)):
        if data["signal"].iloc[i] == 1:
            in_trade = True
            hold_count = 0
        elif in_trade:
            hold_count += 1
            if hold_count < price_period:
                data.iloc[i, data.columns.get_loc("signal")] = 1
            else:
                in_trade = False
                data.iloc[i, data.columns.get_loc("signal")] = -1

    data["strategy_name"] = f"Vol Breakout({price_period}d, {vol_multiplier}x vol)"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 9: VWAP Reversion (Institutional Reference)
# ---------------------------------------------------------------------------
def vwap_reversion(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """
    VWAP as institutional reference price.
    Buy when price dips below VWAP in uptrend, sell when extended above.
    Ref: Trading and Exchanges (Larry Harris) — VWAP is the benchmark.
    """
    data = df.copy()
    # Approximate VWAP using rolling price-volume weighted average
    typical = (data["High"] + data["Low"] + data["Close"]) / 3
    data["vwap"] = (typical * data["Volume"]).rolling(lookback).sum() / data["Volume"].rolling(lookback).sum()
    data["ema50"] = data["Close"].ewm(span=50, adjust=False).mean()

    data["signal"] = 0
    in_position = False
    for i in range(lookback, len(data)):
        price = data["Close"].iloc[i]
        vwap = data["vwap"].iloc[i]
        uptrend = price > data["ema50"].iloc[i]

        if pd.isna(vwap):
            continue

        if not in_position and uptrend and price < vwap * 0.995:
            in_position = True
        elif in_position and price > vwap * 1.01:
            in_position = False
            data.iloc[i, data.columns.get_loc("signal")] = -1
            continue

        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1

    data["strategy_name"] = f"VWAP Reversion({lookback}d)"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 10: Market Regime Adaptive
# ---------------------------------------------------------------------------
def regime_adaptive(df: pd.DataFrame) -> pd.DataFrame:
    """
    Detects market regime (trending vs mean-reverting) and adapts.
    
    Trending: Use momentum (EMA crossover)
    Choppy: Use mean reversion (RSI)
    
    Regime detection via ADX + volatility ratio.
    Ref: Kaufman — Trading Systems and Methods
    """
    data = df.copy()
    close = data["Close"]

    adx_ind = trend.ADXIndicator(data["High"], data["Low"], close, window=14)
    data["adx"] = adx_ind.adx()
    data["ema9"] = close.ewm(span=9, adjust=False).mean()
    data["ema21"] = close.ewm(span=21, adjust=False).mean()
    data["rsi"] = momentum.RSIIndicator(close, window=14).rsi()

    data["signal"] = 0
    in_position = False

    for i in range(30, len(data)):
        adx_val = data["adx"].iloc[i]
        if pd.isna(adx_val):
            continue

        trending = adx_val > 25

        if trending:
            # Momentum mode: follow EMA crossover
            buy = data["ema9"].iloc[i] > data["ema21"].iloc[i]
            if not in_position and buy:
                in_position = True
            elif in_position and not buy:
                in_position = False
                data.iloc[i, data.columns.get_loc("signal")] = -1
                continue
        else:
            # Mean reversion mode: buy oversold RSI
            rsi_val = data["rsi"].iloc[i]
            if not in_position and rsi_val < 35:
                in_position = True
            elif in_position and rsi_val > 65:
                in_position = False
                data.iloc[i, data.columns.get_loc("signal")] = -1
                continue

        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1

    data["strategy_name"] = "Regime Adaptive (ADX>25:Momentum, else:MR)"
    return data


# ---------------------------------------------------------------------------
# STRATEGY 11: Volatility Expansion (Kaufman)
# ---------------------------------------------------------------------------
def volatility_expansion(df: pd.DataFrame, atr_period: int = 14,
                          expansion_mult: float = 1.5) -> pd.DataFrame:
    """
    Enter when volatility expands (ATR spikes above average).
    Combined with directional filter — only buy expanding vol in uptrend.
    Ref: Perry Kaufman — Trading Systems and Methods
    """
    data = df.copy()
    atr = volatility.AverageTrueRange(data["High"], data["Low"], data["Close"],
                                       window=atr_period).average_true_range()
    data["atr"] = atr
    data["atr_avg"] = atr.rolling(50).mean()
    data["ema20"] = data["Close"].ewm(span=20, adjust=False).mean()

    data["signal"] = 0
    in_position = False

    for i in range(50, len(data)):
        if pd.isna(data["atr_avg"].iloc[i]):
            continue

        vol_expanding = data["atr"].iloc[i] > expansion_mult * data["atr_avg"].iloc[i]
        uptrend = data["Close"].iloc[i] > data["ema20"].iloc[i]

        if not in_position and vol_expanding and uptrend:
            in_position = True
        elif in_position:
            # Exit when vol contracts back to normal
            if data["atr"].iloc[i] < data["atr_avg"].iloc[i]:
                in_position = False
                data.iloc[i, data.columns.get_loc("signal")] = -1
                continue

        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1

    data["strategy_name"] = f"Vol Expansion(ATR>{expansion_mult}x avg)"
    return data


# ---------------------------------------------------------------------------
# Registry of all strategies (for easy iteration)
# ---------------------------------------------------------------------------
STRATEGIES = {
    "sma_crossover": sma_crossover,
    "ema_filtered": ema_crossover_filtered,
    "rsi_mean_reversion": rsi_mean_reversion,
    "bollinger_breakout": bollinger_breakout,
    "macd_rsi": macd_rsi_combo,
    "supertrend": supertrend,
    "momentum": momentum_strategy,
    "volume_breakout": volume_breakout,
    "vwap_reversion": vwap_reversion,
    "regime_adaptive": regime_adaptive,
    "volatility_expansion": volatility_expansion,
}

# Import institutional strategies and merge
try:
    from .institutional import INSTITUTIONAL_STRATEGIES
    ALL_STRATEGIES = {**STRATEGIES, **INSTITUTIONAL_STRATEGIES}
except ImportError:
    ALL_STRATEGIES = STRATEGIES
