"""
Example Custom Strategy
=======================
Copy this file and modify to create your own strategy.

Rules:
1. Function takes a DataFrame with OHLCV columns
2. Must return DataFrame with a 'signal' column (1=Buy, -1=Sell, 0=Hold)
3. Must set 'strategy_name' column

Example: StochRSI + EMA trend filter
(Similar to what you'd use for ARSSBL-type analysis)
"""

import pandas as pd
import numpy as np
from ta import momentum, trend


def stochrsi_trend(
    df: pd.DataFrame,
    stoch_period: int = 14,
    stoch_smooth: int = 3,
    ema_period: int = 50,
    oversold: float = 20,
    overbought: float = 80,
) -> pd.DataFrame:
    """
    StochRSI mean reversion with EMA trend filter.

    - Only BUY when StochRSI is oversold AND price is above EMA (uptrend)
    - SELL when StochRSI is overbought
    - Stay flat when price is below EMA (avoid catching falling knives)

    This avoids the ARSSBL-type trap: buying dips in a downtrend.
    """
    data = df.copy()

    # Calculate StochRSI
    rsi = momentum.RSIIndicator(data["Close"], window=stoch_period).rsi()
    stoch_rsi = momentum.StochRSIIndicator(
        data["Close"], window=stoch_period, smooth1=stoch_smooth, smooth2=stoch_smooth
    )
    data["stochrsi_k"] = stoch_rsi.stochrsi_k() * 100
    data["stochrsi_d"] = stoch_rsi.stochrsi_d() * 100

    # Trend filter
    data["ema_trend"] = data["Close"].ewm(span=ema_period, adjust=False).mean()

    # Signal logic
    data["signal"] = 0

    # Buy: oversold + uptrend + K crossing above D
    buy_cond = (
        (data["stochrsi_k"] < oversold)
        & (data["Close"] > data["ema_trend"])
        & (data["stochrsi_k"] > data["stochrsi_d"])
    )

    # Sell: overbought OR price drops below EMA
    sell_cond = (data["stochrsi_k"] > overbought) | (
        data["Close"] < data["ema_trend"] * 0.97  # 3% below EMA = exit
    )

    # Apply signals (hold between buy and sell)
    in_position = False
    for i in range(len(data)):
        if buy_cond.iloc[i] and not in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1
            in_position = True
        elif sell_cond.iloc[i] and in_position:
            data.iloc[i, data.columns.get_loc("signal")] = -1
            in_position = False
        elif in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1

    data["strategy_name"] = f"StochRSI({stoch_period}) + EMA({ema_period})"
    return data


# ----- To register this strategy, add to nse_backtest/strategies.py: -----
# from custom_strategies.example_strategy import stochrsi_trend
# STRATEGIES["stochrsi_trend"] = stochrsi_trend
