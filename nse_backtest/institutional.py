"""
Institutional Strategies
=========================
Advanced strategies based on quantitative research.

References:
- Algorithmic Trading, Ernest Chan (VWAP, Mean Reversion)
- Advances in Financial ML, Lopez de Prado (Regime Detection)
- Trading Systems and Methods, Kaufman (Adaptive Moving Average)
- Technical Analysis of Financial Markets, Murphy (Multi-timeframe)
"""

import pandas as pd
import numpy as np
from ta import trend, momentum, volatility


def vwap_reversion(df: pd.DataFrame, lookback: int = 20,
                   entry_std: float = 2.0, exit_std: float = 0.5) -> pd.DataFrame:
    """
    VWAP Mean Reversion Strategy.
    
    Buy when price drops below VWAP - N*std (oversold vs institutional avg).
    Sell when price reverts to VWAP - exit_std (near fair value).
    
    VWAP is the volume-weighted average price — it represents the "fair" price
    where most volume traded. Deviations from VWAP tend to revert.
    
    Based on: Algorithmic Trading, Ernest Chan
    """
    data = df.copy()
    
    # Rolling VWAP (not intraday VWAP — we use daily rolling VWAP)
    typical_price = (data["High"] + data["Low"] + data["Close"]) / 3
    cum_vol_price = (typical_price * data["Volume"]).rolling(lookback).sum()
    cum_vol = data["Volume"].rolling(lookback).sum()
    data["vwap"] = cum_vol_price / cum_vol
    
    # VWAP standard deviation bands
    vwap_diff = data["Close"] - data["vwap"]
    data["vwap_std"] = vwap_diff.rolling(lookback).std()
    
    data["signal"] = 0
    in_position = False
    
    for i in range(lookback, len(data)):
        vwap = data["vwap"].iloc[i]
        std = data["vwap_std"].iloc[i]
        close = data["Close"].iloc[i]
        
        if pd.isna(vwap) or pd.isna(std) or std < 0.01:
            continue
            
        if not in_position and close < vwap - entry_std * std:
            in_position = True
        elif in_position and close > vwap - exit_std * std:
            in_position = False
            data.iloc[i, data.columns.get_loc("signal")] = -1
            continue
        
        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1
    
    data["strategy_name"] = f"VWAP Reversion({lookback}, {entry_std}σ)"
    return data


def market_regime(df: pd.DataFrame, lookback: int = 60,
                  vol_lookback: int = 20) -> pd.DataFrame:
    """
    Market Regime Detection Strategy.
    
    Classifies market into 4 regimes:
    1. Low vol + uptrend = BUY (best for momentum)
    2. Low vol + downtrend = SELL (momentum bearish)
    3. High vol + uptrend = CAUTIOUS BUY (volatile bull)
    4. High vol + downtrend = CASH (crash/panic — avoid)
    
    Only trades in regimes 1 and 3 (with reduced size in 3).
    
    Based on: Advances in Financial ML, Lopez de Prado
    """
    data = df.copy()
    
    # Trend: slope of linear regression over lookback
    close = data["Close"]
    slopes = pd.Series(index=data.index, dtype=float)
    for i in range(lookback, len(data)):
        y = close.iloc[i-lookback:i].values
        x = np.arange(lookback)
        slope = np.polyfit(x, y, 1)[0]
        slopes.iloc[i] = slope / close.iloc[i]  # Normalize by price
    
    # Volatility: realized vol (std of returns)
    daily_ret = close.pct_change()
    realized_vol = daily_ret.rolling(vol_lookback).std() * np.sqrt(252)
    median_vol = realized_vol.rolling(252).median()
    
    data["regime_slope"] = slopes
    data["regime_vol"] = realized_vol
    data["signal"] = 0
    
    for i in range(max(lookback, 252), len(data)):
        s = slopes.iloc[i]
        v = realized_vol.iloc[i]
        mv = median_vol.iloc[i]
        
        if pd.isna(s) or pd.isna(v) or pd.isna(mv):
            continue
        
        is_uptrend = s > 0
        is_low_vol = v < mv
        
        if is_uptrend and is_low_vol:
            # Regime 1: best — full position
            data.iloc[i, data.columns.get_loc("signal")] = 1
        elif is_uptrend and not is_low_vol:
            # Regime 3: cautious — still buy but system will use reduced size
            data.iloc[i, data.columns.get_loc("signal")] = 1
        else:
            # Regime 2 or 4: stay out
            data.iloc[i, data.columns.get_loc("signal")] = -1
    
    data["strategy_name"] = f"Market Regime({lookback}d)"
    return data


def volatility_expansion(df: pd.DataFrame, bb_period: int = 20,
                          squeeze_pct: float = 0.4,
                          atr_period: int = 14) -> pd.DataFrame:
    """
    Volatility Expansion Breakout.
    
    Wait for Bollinger Band squeeze (low volatility),
    then buy on expansion in the direction of the trend.
    
    Logic:
    1. Detect squeeze: BB width < 40th percentile of last 100 days
    2. Wait for expansion: price breaks above upper BB
    3. Confirm with volume: must be > 1.5× average
    4. Hold until price drops below middle BB or ATR-based trailing stop
    
    Based on: Trading Systems and Methods, Kaufman
    """
    data = df.copy()
    
    bb = volatility.BollingerBands(data["Close"], window=bb_period, window_dev=2)
    data["bb_upper"] = bb.bollinger_hband()
    data["bb_lower"] = bb.bollinger_lband()
    data["bb_mid"] = bb.bollinger_mavg()
    data["bb_width"] = (data["bb_upper"] - data["bb_lower"]) / data["bb_mid"]
    
    atr = volatility.AverageTrueRange(data["High"], data["Low"], data["Close"], 
                                       window=atr_period).average_true_range()
    data["atr"] = atr
    
    vol_avg = data["Volume"].rolling(20).mean()
    width_pctile = data["bb_width"].rolling(100).rank(pct=True)
    
    data["signal"] = 0
    in_position = False
    trail_stop = 0
    
    for i in range(100, len(data)):
        close = data["Close"].iloc[i]
        
        if pd.isna(width_pctile.iloc[i]):
            continue
            
        was_squeezed = width_pctile.iloc[i] < squeeze_pct
        broke_upper = close > data["bb_upper"].iloc[i]
        vol_confirm = data["Volume"].iloc[i] > 1.3 * vol_avg.iloc[i] if not pd.isna(vol_avg.iloc[i]) else False
        
        if not in_position and was_squeezed and broke_upper and vol_confirm:
            in_position = True
            trail_stop = close - 2 * data["atr"].iloc[i]
        elif in_position:
            # Update trailing stop
            new_stop = close - 2 * data["atr"].iloc[i]
            trail_stop = max(trail_stop, new_stop)
            
            if close < trail_stop or close < data["bb_mid"].iloc[i]:
                in_position = False
                data.iloc[i, data.columns.get_loc("signal")] = -1
                continue
        
        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1
    
    data["strategy_name"] = f"Vol Expansion(BB{bb_period}, squeeze<{squeeze_pct:.0%})"
    return data


def adaptive_momentum(df: pd.DataFrame, fast_period: int = 10,
                       slow_period: int = 30, er_period: int = 20) -> pd.DataFrame:
    """
    Kaufman Adaptive Moving Average (KAMA) Strategy.
    
    Uses Efficiency Ratio to adapt the moving average speed:
    - Trending market → fast MA (responsive)
    - Choppy market → slow MA (avoids whipsaws)
    
    This is superior to fixed-period MAs because it automatically
    adapts to market conditions.
    
    Based on: Trading Systems and Methods, Kaufman (Ch 17)
    """
    data = df.copy()
    close = data["Close"]
    
    # Efficiency Ratio: |net movement| / sum(|daily movements|)
    direction = abs(close - close.shift(er_period))
    volatility_sum = close.diff().abs().rolling(er_period).sum()
    er = direction / volatility_sum
    er = er.fillna(0)
    
    # Smoothing constants
    fast_sc = 2 / (fast_period + 1)
    slow_sc = 2 / (slow_period + 1)
    
    # KAMA: starts from SMA, then adapts
    kama = pd.Series(index=data.index, dtype=float)
    kama.iloc[er_period] = close.iloc[:er_period+1].mean()
    
    for i in range(er_period + 1, len(data)):
        sc = (er.iloc[i] * (fast_sc - slow_sc) + slow_sc) ** 2
        kama.iloc[i] = kama.iloc[i-1] + sc * (close.iloc[i] - kama.iloc[i-1])
    
    data["kama"] = kama
    
    # Signal: price above KAMA = buy, below = sell
    # With a small filter band to avoid whipsaws
    atr = volatility.AverageTrueRange(data["High"], data["Low"], close, 
                                       window=14).average_true_range()
    
    data["signal"] = 0
    in_position = False
    
    for i in range(er_period + 14, len(data)):
        if pd.isna(kama.iloc[i]) or pd.isna(atr.iloc[i]):
            continue
            
        band = 0.5 * atr.iloc[i]  # Filter band
        
        if not in_position and close.iloc[i] > kama.iloc[i] + band:
            in_position = True
        elif in_position and close.iloc[i] < kama.iloc[i] - band:
            in_position = False
            data.iloc[i, data.columns.get_loc("signal")] = -1
            continue
        
        if in_position:
            data.iloc[i, data.columns.get_loc("signal")] = 1
    
    data["strategy_name"] = f"KAMA({fast_period}/{slow_period}, ER={er_period})"
    return data


# Registry for institutional strategies
INSTITUTIONAL_STRATEGIES = {
    "vwap_reversion": vwap_reversion,
    "market_regime": market_regime,
    "volatility_expansion": volatility_expansion,
    "adaptive_momentum": adaptive_momentum,
}
