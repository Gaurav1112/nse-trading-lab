"""
Multi-Mode Trading Engine
==========================
Supports 5 trading styles with dedicated analysis for each.

1. SWING (2-15 days)    — Technical momentum, R:R based
2. POSITIONAL (15-90 days) — Trend following + fundamentals  
3. LONG TERM (90+ days)  — Sector rotation, macro, value
4. INTRADAY (same day)   — VWAP, ORB, momentum scalps
5. OPTIONS              — OI analysis, IV, strategies

Each mode has different:
  - Timeframes
  - Indicators
  - Entry/exit rules
  - Position sizing
  - Risk parameters
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from ta import trend, momentum, volatility, volume as ta_vol


# ════════════════════════════════════════════════════════════════
#  DATA CLASSES
# ════════════════════════════════════════════════════════════════

@dataclass
class TradeSetup:
    """Universal trade setup for any mode."""
    symbol: str
    mode: str              # SWING / POSITIONAL / LONGTERM / INTRADAY / OPTIONS
    signal: str            # BUY / SELL / HOLD
    score: float = 0
    win_probability: float = 0
    entry_price: float = 0
    stop_loss: float = 0
    target_1: float = 0
    target_2: float = 0
    risk_reward: float = 0
    timeframe: str = ""    # "2-15 days", "15-90 days", etc.
    strategy_name: str = ""
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    # Position sizing
    suggested_qty: int = 0
    position_value: float = 0
    max_loss: float = 0


@dataclass
class OptionsSetup:
    """Options-specific analysis."""
    symbol: str
    spot_price: float = 0
    # Open Interest
    total_ce_oi: float = 0
    total_pe_oi: float = 0
    pcr: float = 0         # Put-Call Ratio
    max_pain: float = 0
    # Implied Volatility
    iv_current: float = 0
    iv_rank: float = 0     # 0-100 (where current IV sits in 1yr range)
    iv_percentile: float = 0
    # Strategy suggestions
    strategies: list = field(default_factory=list)
    outlook: str = ""      # BULLISH / BEARISH / NEUTRAL / HIGH_VOL


@dataclass  
class FuturesSetup:
    """Futures-specific analysis."""
    symbol: str
    spot_price: float = 0
    futures_price: float = 0
    basis: float = 0        # Futures - Spot
    basis_pct: float = 0
    oi_change: float = 0    # OI change %
    rollover_pct: float = 0
    signal: str = ""        # LONG_BUILD / SHORT_BUILD / LONG_UNWIND / SHORT_COVER
    reasons: list = field(default_factory=list)


# ════════════════════════════════════════════════════════════════
#  SWING TRADING (2-15 days)
# ════════════════════════════════════════════════════════════════

def analyze_swing(df: pd.DataFrame, symbol: str, capital: float = 100000,
                  risk_pct: float = 2.0) -> TradeSetup:
    """
    Swing trading analysis — hold 2-15 days.
    Focus: Technical momentum, breakouts, mean reversion.
    Key indicators: RSI, MACD, BB, Volume, Supertrend.
    """
    from .scorer import analyze_stock
    
    score = analyze_stock(df, symbol, run_backtests=False)
    
    setup = TradeSetup(
        symbol=symbol, mode="SWING", timeframe="2-15 days",
        signal="BUY" if score.verdict == "GO" else "HOLD" if score.verdict == "WAIT" else "SELL",
        score=score.final_score,
        win_probability=score.win_probability,
        entry_price=score.current_price,
        stop_loss=score.stop_loss,
        target_1=score.target_1,
        target_2=score.target_2,
        risk_reward=score.risk_reward,
        strategy_name="Multi-indicator swing",
        reasons=score.reasons[:10],
        warnings=score.warnings,
    )
    
    # Position sizing
    risk_per_share = setup.entry_price - setup.stop_loss
    if risk_per_share > 0:
        setup.suggested_qty = int((capital * risk_pct / 100) / risk_per_share)
        setup.position_value = setup.suggested_qty * setup.entry_price
        setup.max_loss = setup.suggested_qty * risk_per_share
    
    return setup


# ════════════════════════════════════════════════════════════════
#  POSITIONAL TRADING (15-90 days)
# ════════════════════════════════════════════════════════════════

def analyze_positional(df: pd.DataFrame, symbol: str, capital: float = 100000,
                       risk_pct: float = 3.0) -> TradeSetup:
    """
    Positional trading — hold 15-90 days.
    Focus: Trend following, sector strength, wider stops.
    Key indicators: 50/200 EMA, ADX, weekly trend, Ichimoku.
    """
    close = df["Close"]
    cur = close.iloc[-1]
    n = len(close)
    
    score = 0
    reasons = []
    
    # 1. Long-term trend (50/200 EMA)
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean() if n >= 200 else ema50
    
    if cur > ema200.iloc[-1]:
        score += 20
        reasons.append("Price above 200 EMA (long-term bullish)")
    if ema50.iloc[-1] > ema200.iloc[-1]:
        score += 15
        reasons.append("Golden cross active (50 > 200 EMA)")
    
    # 2. Trend strength (ADX)
    adx_ind = trend.ADXIndicator(df["High"], df["Low"], close, window=14)
    adx_val = adx_ind.adx().iloc[-1]
    di_plus = adx_ind.adx_pos().iloc[-1]
    di_minus = adx_ind.adx_neg().iloc[-1]
    
    if adx_val > 25 and di_plus > di_minus:
        score += 15
        reasons.append(f"Strong bullish trend (ADX={adx_val:.0f})")
    elif adx_val > 20 and di_plus > di_minus:
        score += 8
        reasons.append(f"Moderate trend (ADX={adx_val:.0f})")
    elif di_minus > di_plus:
        reasons.append(f"Bearish direction (DI- > DI+)")
    
    # 3. Monthly momentum (3-month return)
    if n >= 63:
        ret_3m = (cur / close.iloc[-63] - 1) * 100
        if ret_3m > 15:
            score += 15
            reasons.append(f"Strong 3-month return: {ret_3m:.1f}%")
        elif ret_3m > 5:
            score += 10
            reasons.append(f"Positive 3-month return: {ret_3m:.1f}%")
        elif ret_3m < -10:
            reasons.append(f"Negative 3-month return: {ret_3m:.1f}%")
    
    # 4. 52-week relative position
    if n >= 252:
        high_52w = df["High"].iloc[-252:].max()
        low_52w = df["Low"].iloc[-252:].min()
        range_52w = high_52w - low_52w
        if range_52w > 0:
            position = (cur - low_52w) / range_52w * 100
            if position > 70:
                score += 10
                reasons.append(f"Near 52-week high ({position:.0f}th percentile)")
            elif position < 30:
                reasons.append(f"Near 52-week low ({position:.0f}th percentile)")
            else:
                score += 5
    
    # 5. Volume trend (20d vs 50d)
    vol = df["Volume"]
    if n >= 50:
        vol_20 = vol.iloc[-20:].mean()
        vol_50 = vol.iloc[-50:].mean()
        if vol_50 > 0 and vol_20 / vol_50 > 1.1:
            score += 10
            reasons.append("Volume trend rising (20d > 50d avg)")
    
    # 6. Sector momentum proxy (stock vs own 200 EMA slope)
    if n >= 200:
        ema200_slope = (ema200.iloc[-1] / ema200.iloc[-20] - 1) * 100
        if ema200_slope > 1:
            score += 10
            reasons.append(f"200 EMA rising ({ema200_slope:.1f}%/20d)")
    
    # 7. Weekly RSI
    if n >= 70:
        weekly_close = close.iloc[::5]  # Approximate weekly
        if len(weekly_close) >= 14:
            weekly_rsi = momentum.RSIIndicator(weekly_close, window=14).rsi().iloc[-1]
            if 40 < weekly_rsi < 70:
                score += 10
                reasons.append(f"Weekly RSI healthy ({weekly_rsi:.0f})")
            elif weekly_rsi > 70:
                score += 3
                reasons.append(f"Weekly RSI overbought ({weekly_rsi:.0f})")
    
    score = min(score, 100)
    verdict = "BUY" if score >= 60 else "HOLD" if score >= 40 else "SELL"
    
    # Wider stops for positional: 3× ATR
    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, 14).average_true_range().iloc[-1]
    sl = cur - 3 * atr
    sl = max(sl, ema50.iloc[-1] * 0.97) if cur > ema50.iloc[-1] else cur * 0.90
    t1 = cur + max(4 * atr, 2 * (cur - sl))
    t2 = cur + max(6 * atr, 3 * (cur - sl))
    risk = cur - sl
    rr = (t1 - cur) / risk if risk > 0 else 0
    
    setup = TradeSetup(
        symbol=symbol, mode="POSITIONAL", timeframe="15-90 days",
        signal=verdict, score=score, entry_price=cur,
        stop_loss=round(sl, 2), target_1=round(t1, 2), target_2=round(t2, 2),
        risk_reward=round(rr, 1), strategy_name="Trend following positional",
        reasons=reasons, warnings=[],
    )
    
    if risk > 0:
        setup.suggested_qty = int((capital * risk_pct / 100) / risk)
        setup.position_value = setup.suggested_qty * cur
        setup.max_loss = setup.suggested_qty * risk
    
    # Win probability (logistic)
    setup.win_probability = round(15 + 70 / (1 + np.exp(-0.06 * (score - 50))), 1)
    
    return setup


# ════════════════════════════════════════════════════════════════
#  LONG TERM INVESTING (90+ days)
# ════════════════════════════════════════════════════════════════

def analyze_longterm(df: pd.DataFrame, symbol: str, capital: float = 100000) -> TradeSetup:
    """
    Long-term analysis — hold 90+ days.
    Focus: Macro trend, value, sector rotation, accumulation.
    """
    close = df["Close"]
    cur = close.iloc[-1]
    n = len(close)
    
    score = 0
    reasons = []
    
    # 1. Long-term trend (200 EMA direction)
    if n >= 200:
        ema200 = close.ewm(span=200, adjust=False).mean()
        if cur > ema200.iloc[-1]:
            score += 15
            reasons.append("Above 200 EMA — long-term uptrend")
        ema200_slope = (ema200.iloc[-1] / ema200.iloc[-60] - 1) * 100
        if ema200_slope > 3:
            score += 10
            reasons.append(f"200 EMA rising strongly ({ema200_slope:.1f}%)")
    
    # 2. Annual return
    if n >= 252:
        ret_1y = (cur / close.iloc[-252] - 1) * 100
        if ret_1y > 20:
            score += 15
            reasons.append(f"Strong annual return: {ret_1y:.0f}%")
        elif ret_1y > 5:
            score += 8
            reasons.append(f"Positive annual return: {ret_1y:.0f}%")
        else:
            reasons.append(f"Weak annual return: {ret_1y:.0f}%")
    
    # 3. CAGR estimate (if enough data)
    if n >= 504:
        ret_2y = (cur / close.iloc[-504] - 1)
        cagr = ((1 + ret_2y) ** 0.5 - 1) * 100
        if cagr > 15:
            score += 15
            reasons.append(f"2-year CAGR: {cagr:.0f}% — excellent")
        elif cagr > 8:
            score += 8
            reasons.append(f"2-year CAGR: {cagr:.0f}% — good")
    
    # 4. Drawdown from ATH
    ath = df["High"].max()
    dd_from_ath = (ath - cur) / ath * 100
    if dd_from_ath < 10:
        score += 10
        reasons.append(f"Near all-time high ({dd_from_ath:.0f}% below)")
    elif dd_from_ath < 25:
        score += 12
        reasons.append(f"{dd_from_ath:.0f}% below ATH — potential accumulation zone")
    elif dd_from_ath > 40:
        score += 3
        reasons.append(f"{dd_from_ath:.0f}% below ATH — deep value or trouble?")
    
    # 5. Accumulation (rising OBV over 60 days)
    if n >= 60:
        obv = ta_vol.OnBalanceVolumeIndicator(close, df["Volume"]).on_balance_volume()
        obv_slope = np.polyfit(np.arange(60), obv.iloc[-60:].values, 1)[0]
        if obv_slope > 0:
            score += 10
            reasons.append("OBV rising over 60 days — institutional accumulation")
        else:
            reasons.append("OBV falling — institutional distribution")
    
    # 6. Low volatility (stable for long-term)
    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, 14).average_true_range().iloc[-1]
    atr_pct = atr / cur * 100
    if atr_pct < 2.5:
        score += 10
        reasons.append(f"Low daily volatility ({atr_pct:.1f}%) — stable")
    elif atr_pct > 4:
        reasons.append(f"High volatility ({atr_pct:.1f}%) — risky for long-term")
    
    # 7. Monthly higher lows (accumulation pattern)
    if n >= 120:
        month_lows = [df["Low"].iloc[i:i+20].min() for i in range(-120, 0, 20)]
        if all(month_lows[i] <= month_lows[i+1] for i in range(len(month_lows)-1)):
            score += 10
            reasons.append("Higher monthly lows — strong accumulation pattern")
    
    score = min(score, 100)
    verdict = "BUY" if score >= 55 else "HOLD" if score >= 35 else "SELL"
    
    # Wide stops for long-term
    sl = cur * 0.85  # 15% trailing stop
    t1 = cur * 1.25  # 25% target
    t2 = cur * 1.50  # 50% target
    rr = (t1 - cur) / (cur - sl) if cur > sl else 0
    
    setup = TradeSetup(
        symbol=symbol, mode="LONGTERM", timeframe="90+ days",
        signal=verdict, score=score, entry_price=cur,
        stop_loss=round(sl, 2), target_1=round(t1, 2), target_2=round(t2, 2),
        risk_reward=round(rr, 1), strategy_name="Long-term trend + accumulation",
        reasons=reasons, warnings=[],
    )
    setup.suggested_qty = max(1, int(capital * 0.10 / cur))  # 10% allocation
    setup.position_value = setup.suggested_qty * cur
    setup.max_loss = setup.suggested_qty * (cur - sl)
    setup.win_probability = round(15 + 70 / (1 + np.exp(-0.06 * (score - 45))), 1)
    
    return setup


# ════════════════════════════════════════════════════════════════
#  INTRADAY TRADING (same day)
# ════════════════════════════════════════════════════════════════

def analyze_intraday(df: pd.DataFrame, symbol: str, capital: float = 100000,
                     risk_pct: float = 1.0) -> TradeSetup:
    """
    Intraday analysis — exit same day.
    Focus: VWAP, ORB, volume spikes, momentum bursts.
    Uses daily data to estimate intraday setups.
    """
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    vol = df["Volume"]
    cur = close.iloc[-1]
    n = len(close)
    
    score = 0
    reasons = []
    
    # 1. Pre-market bias (gap analysis from previous days)
    if n >= 2:
        gap_pct = (df["Open"].iloc[-1] / close.iloc[-2] - 1) * 100
        if gap_pct > 0.5:
            score += 15
            reasons.append(f"Gap up {gap_pct:.1f}% — bullish opening")
        elif gap_pct < -0.5:
            reasons.append(f"Gap down {gap_pct:.1f}% — bearish opening")
        else:
            score += 5
            reasons.append(f"Flat open ({gap_pct:.1f}%)")
    
    # 2. Opening Range Breakout potential (estimate from ATR)
    atr = volatility.AverageTrueRange(high, low, close, 14).average_true_range().iloc[-1]
    orb_range = atr * 0.4  # ORB ≈ 40% of daily ATR in first 30 min
    orb_high = cur + orb_range / 2
    orb_low = cur - orb_range / 2
    
    if atr / cur * 100 > 1.5:
        score += 15
        reasons.append(f"Good intraday range (ATR {atr:.0f} = {atr/cur*100:.1f}%)")
    else:
        score += 5
        reasons.append(f"Narrow range (ATR {atr:.0f} = {atr/cur*100:.1f}%)")
    
    # 3. Volume profile (is today likely to be active?)
    if n >= 20:
        avg_vol = vol.iloc[-20:].mean()
        recent_vol = vol.iloc[-1]
        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1
        if vol_ratio > 1.5:
            score += 15
            reasons.append(f"High volume ({vol_ratio:.1f}x avg) — active day")
        elif vol_ratio > 0.8:
            score += 8
            reasons.append(f"Normal volume ({vol_ratio:.1f}x avg)")
        else:
            reasons.append(f"Low volume ({vol_ratio:.1f}x avg) — avoid intraday")
    
    # 4. VWAP analysis (estimated from daily data)
    if n >= 1:
        vwap_est = (high.iloc[-1] + low.iloc[-1] + close.iloc[-1]) / 3  # Typical price
        if cur > vwap_est:
            score += 10
            reasons.append(f"Above VWAP (₹{vwap_est:.0f}) — intraday bullish")
        else:
            reasons.append(f"Below VWAP (₹{vwap_est:.0f}) — intraday bearish")
    
    # 5. Intraday trend alignment with daily
    ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
    if cur > ema9 > ema21:
        score += 15
        reasons.append("Short EMAs bullish (9 > 21, price above)")
    elif cur < ema9 < ema21:
        reasons.append("Short EMAs bearish (price below)")
    
    # 6. Supertrend for direction
    try:
        st_ind = trend.STCIndicator(close, 10, 26, 0.5)
        # Fallback: use simple supertrend logic
    except:
        pass
    
    # 7. RSI for overbought/oversold timing
    rsi = momentum.RSIIndicator(close, 14).rsi().iloc[-1]
    if 40 < rsi < 65:
        score += 10
        reasons.append(f"RSI {rsi:.0f} — room to run intraday")
    elif rsi > 75:
        reasons.append(f"RSI {rsi:.0f} — overbought, watch for reversal")
        score += 3
    
    score = min(score, 100)
    verdict = "BUY" if score >= 60 else "HOLD" if score >= 40 else "SELL"
    
    # Tight stops for intraday: 0.5-1× ATR
    sl = cur - 0.8 * atr
    t1 = cur + 1.2 * atr  # 1.5:1 R:R minimum
    t2 = cur + 2.0 * atr
    risk = cur - sl
    rr = (t1 - cur) / risk if risk > 0 else 0
    
    setup = TradeSetup(
        symbol=symbol, mode="INTRADAY", timeframe="Same day",
        signal=verdict, score=score, entry_price=cur,
        stop_loss=round(sl, 2), target_1=round(t1, 2), target_2=round(t2, 2),
        risk_reward=round(rr, 1), strategy_name="VWAP + ORB intraday",
        reasons=reasons, warnings=["⚠️ Intraday analysis is based on daily data — use 5-min chart for actual entries"],
    )
    
    if risk > 0:
        setup.suggested_qty = int((capital * risk_pct / 100) / risk)
        setup.position_value = setup.suggested_qty * cur
        setup.max_loss = setup.suggested_qty * risk
    
    setup.win_probability = round(15 + 70 / (1 + np.exp(-0.06 * (score - 50))), 1)
    
    return setup


# ════════════════════════════════════════════════════════════════
#  OPTIONS ANALYSIS
# ════════════════════════════════════════════════════════════════

def analyze_options(df: pd.DataFrame, symbol: str) -> OptionsSetup:
    """
    Options analysis based on price action and volatility.
    Note: Real OI/IV data needs NSE API or broker API (not in yfinance).
    This provides estimated analysis from price data.
    """
    close = df["Close"]
    cur = close.iloc[-1]
    n = len(close)
    
    setup = OptionsSetup(symbol=symbol, spot_price=cur)
    
    # 1. Historical Volatility → IV estimate
    if n >= 30:
        daily_returns = close.pct_change().dropna()
        hv_30 = daily_returns.iloc[-30:].std() * np.sqrt(252) * 100  # Annualized
        hv_60 = daily_returns.iloc[-60:].std() * np.sqrt(252) * 100 if n >= 60 else hv_30
        
        setup.iv_current = round(hv_30, 1)  # HV as IV proxy
        
        # IV Rank (where current HV sits in 1-year range)
        if n >= 252:
            rolling_hv = daily_returns.rolling(30).std() * np.sqrt(252) * 100
            hv_min = rolling_hv.iloc[-252:].min()
            hv_max = rolling_hv.iloc[-252:].max()
            if hv_max > hv_min:
                setup.iv_rank = round((hv_30 - hv_min) / (hv_max - hv_min) * 100, 1)
            
            # IV Percentile (% of days where HV was lower than current)
            lower_count = (rolling_hv.iloc[-252:] < hv_30).sum()
            setup.iv_percentile = round(lower_count / 252 * 100, 1)
    
    # 2. Max Pain estimate (nearest round number with most activity)
    # Without real OI data, estimate from price levels
    round_levels = [int(cur / 50) * 50 + i * 50 for i in range(-5, 6)]
    setup.max_pain = min(round_levels, key=lambda x: abs(x - cur))
    
    # 3. PCR estimate (from price bias)
    # Bullish price action → lower PCR, bearish → higher PCR
    ema20 = close.ewm(span=20).mean().iloc[-1]
    if cur > ema20:
        setup.pcr = 0.75  # Bullish bias
    elif cur < ema20 * 0.98:
        setup.pcr = 1.25  # Bearish bias
    else:
        setup.pcr = 1.0   # Neutral
    
    # 4. Strategy suggestions based on IV and direction
    setup.strategies = []
    
    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, 14).average_true_range().iloc[-1]
    rsi = momentum.RSIIndicator(close, 14).rsi().iloc[-1]
    
    # Direction
    bullish = cur > ema20 and rsi > 50
    bearish = cur < ema20 and rsi < 50
    
    if setup.iv_rank > 60:
        # High IV → sell premium
        setup.outlook = "HIGH_VOL"
        if bullish:
            setup.strategies.append({
                "name": "Bull Put Spread (Credit)",
                "legs": f"Sell {int(cur-atr)}PE, Buy {int(cur-2*atr)}PE",
                "thesis": "High IV + bullish → collect premium below support",
                "max_profit": "Net credit received",
                "max_loss": "Spread width - credit",
                "breakeven": f"₹{cur-atr:.0f} - credit"
            })
            setup.strategies.append({
                "name": "Short Strangle",
                "legs": f"Sell {int(cur+2*atr)}CE, Sell {int(cur-2*atr)}PE",
                "thesis": "High IV will contract → both premiums decay",
                "max_profit": "Total credit",
                "max_loss": "Unlimited (use with hedge)",
                "breakeven": f"Below ₹{cur-2*atr:.0f} or above ₹{cur+2*atr:.0f}"
            })
        else:
            setup.strategies.append({
                "name": "Bear Call Spread (Credit)",
                "legs": f"Sell {int(cur+atr)}CE, Buy {int(cur+2*atr)}CE",
                "thesis": "High IV + bearish → collect premium above resistance",
                "max_profit": "Net credit received",
                "max_loss": "Spread width - credit",
                "breakeven": f"₹{cur+atr:.0f} + credit"
            })
            setup.strategies.append({
                "name": "Iron Condor",
                "legs": f"Sell {int(cur-atr)}PE/{int(cur+atr)}CE, Buy {int(cur-2*atr)}PE/{int(cur+2*atr)}CE",
                "thesis": "Range-bound + high IV → collect premium both sides",
                "max_profit": "Total credit",
                "max_loss": "Spread width - credit",
                "breakeven": f"₹{cur-atr:.0f} to ₹{cur+atr:.0f}"
            })
    else:
        # Low IV → buy options
        setup.outlook = "BULLISH" if bullish else "BEARISH" if bearish else "NEUTRAL"
        if bullish:
            setup.strategies.append({
                "name": "Long Call (ATM)",
                "legs": f"Buy {int(cur)}CE",
                "thesis": "Low IV + bullish → cheap premium, directional bet",
                "max_profit": "Unlimited",
                "max_loss": "Premium paid",
                "breakeven": f"₹{cur:.0f} + premium"
            })
            setup.strategies.append({
                "name": "Bull Call Spread (Debit)",
                "legs": f"Buy {int(cur)}CE, Sell {int(cur+2*atr)}CE",
                "thesis": "Defined risk bullish with reduced cost",
                "max_profit": "Spread width - debit",
                "max_loss": "Net debit paid",
                "breakeven": f"₹{cur:.0f} + debit"
            })
        elif bearish:
            setup.strategies.append({
                "name": "Long Put (ATM)",
                "legs": f"Buy {int(cur)}PE",
                "thesis": "Low IV + bearish → cheap hedge/directional",
                "max_profit": "Strike - premium (to zero)",
                "max_loss": "Premium paid",
                "breakeven": f"₹{cur:.0f} - premium"
            })
        else:
            setup.strategies.append({
                "name": "Long Straddle",
                "legs": f"Buy {int(cur)}CE + Buy {int(cur)}PE",
                "thesis": "Low IV → expect big move in either direction",
                "max_profit": "Unlimited",
                "max_loss": "Total premium paid",
                "breakeven": f"Below ₹{cur-2*atr:.0f} or above ₹{cur+2*atr:.0f}"
            })
    
    return setup


# ════════════════════════════════════════════════════════════════
#  FUTURES ANALYSIS
# ════════════════════════════════════════════════════════════════

def analyze_futures(df: pd.DataFrame, symbol: str, 
                    futures_premium_pct: float = 0.5) -> FuturesSetup:
    """
    Futures analysis — basis, rollover, OI signals.
    Note: Real futures data needs NSE API. This estimates from spot data.
    """
    close = df["Close"]
    cur = close.iloc[-1]
    
    setup = FuturesSetup(symbol=symbol, spot_price=cur)
    
    # Estimate futures price (spot + cost of carry)
    setup.futures_price = round(cur * (1 + futures_premium_pct / 100), 2)
    setup.basis = round(setup.futures_price - cur, 2)
    setup.basis_pct = round(futures_premium_pct, 2)
    
    # Analyze price action for OI signal interpretation
    n = len(close)
    ema20 = close.ewm(span=20).mean().iloc[-1]
    
    if n >= 5:
        price_up = cur > close.iloc[-5]
        vol_up = df["Volume"].iloc[-5:].mean() > df["Volume"].iloc[-20:].mean() if n >= 20 else False
        
        # OI + Price interpretation (Futures 101)
        if price_up and vol_up:
            setup.signal = "LONG_BUILD"
            setup.reasons.append("Price ↑ + Volume ↑ → Fresh long positions (bullish)")
        elif price_up and not vol_up:
            setup.signal = "SHORT_COVER"
            setup.reasons.append("Price ↑ + Volume ↓ → Short covering (weakly bullish)")
        elif not price_up and vol_up:
            setup.signal = "SHORT_BUILD"
            setup.reasons.append("Price ↓ + Volume ↑ → Fresh short positions (bearish)")
        else:
            setup.signal = "LONG_UNWIND"
            setup.reasons.append("Price ↓ + Volume ↓ → Long unwinding (weakly bearish)")
    
    # Basis analysis
    if futures_premium_pct > 0.3:
        setup.reasons.append(f"Positive basis ({futures_premium_pct:.1f}%) — market bullish on expiry")
    elif futures_premium_pct < -0.2:
        setup.reasons.append(f"Negative basis ({futures_premium_pct:.1f}%) — backwardation (bearish)")
    
    return setup


# ════════════════════════════════════════════════════════════════
#  UNIFIED ANALYZER — Run all modes at once
# ════════════════════════════════════════════════════════════════

def analyze_all_modes(df: pd.DataFrame, symbol: str, capital: float = 100000) -> dict:
    """Run all 5 analysis modes and return unified results."""
    results = {}
    
    try:
        results["swing"] = analyze_swing(df, symbol, capital)
    except Exception as e:
        results["swing"] = TradeSetup(symbol=symbol, mode="SWING", signal="ERROR", reasons=[str(e)])
    
    try:
        results["positional"] = analyze_positional(df, symbol, capital)
    except Exception as e:
        results["positional"] = TradeSetup(symbol=symbol, mode="POSITIONAL", signal="ERROR", reasons=[str(e)])
    
    try:
        results["longterm"] = analyze_longterm(df, symbol, capital)
    except Exception as e:
        results["longterm"] = TradeSetup(symbol=symbol, mode="LONGTERM", signal="ERROR", reasons=[str(e)])
    
    try:
        results["intraday"] = analyze_intraday(df, symbol, capital)
    except Exception as e:
        results["intraday"] = TradeSetup(symbol=symbol, mode="INTRADAY", signal="ERROR", reasons=[str(e)])
    
    try:
        results["options"] = analyze_options(df, symbol)
    except Exception as e:
        results["options"] = OptionsSetup(symbol=symbol)
    
    try:
        results["futures"] = analyze_futures(df, symbol)
    except Exception as e:
        results["futures"] = FuturesSetup(symbol=symbol)
    
    return results
