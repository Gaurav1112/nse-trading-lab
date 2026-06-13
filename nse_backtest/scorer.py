"""
Stock Scorer & Predictor
=========================
Analyzes a stock across multiple dimensions and produces a GO/NO-GO verdict.

Scoring dimensions:
1. Trend Score      — Is the stock trending up? (EMA stack, ADX, price vs MAs)
2. Momentum Score   — Is momentum building? (RSI, MACD, StochRSI)
3. Volatility Score — Is volatility favorable? (ATR, BB width, recent range)
4. Volume Score     — Is smart money involved? (Volume trend, OBV, VWAP)
5. Backtest Score   — Do strategies historically work on this stock?
6. Risk Score       — How risky is entry right now? (Drawdown, distance from high)

Each dimension: 0-100 points
Final Score: Weighted average -> GO / WAIT / AVOID verdict
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from ta import trend, momentum, volatility, volume


@dataclass
class ScoreBreakdown:
    """Detailed score breakdown for a stock."""
    symbol: str
    trend_score: float = 0
    momentum_score: float = 0
    volatility_score: float = 0
    volume_score: float = 0
    backtest_score: float = 0
    risk_score: float = 0
    final_score: float = 0
    verdict: str = "AVOID"
    confidence: str = "LOW"
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    entry_zone: str = ""
    stop_loss: float = 0
    target_1: float = 0
    target_2: float = 0
    current_price: float = 0
    risk_reward: float = 0
    # Probability layer
    win_probability: float = 0
    expected_gain_pct: float = 0
    expected_loss_pct: float = 0
    expected_value_pct: float = 0
    regime: str = ""
    # Advanced indicator summaries
    ichimoku_signal: str = ""
    sar_signal: str = ""


def score_trend(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Score trend strength (0-100)."""
    score = 0
    reasons = []
    close = df["Close"]
    n = len(df)

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema100 = close.ewm(span=100, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    current = close.iloc[-1]

    if current > ema20.iloc[-1]:
        score += 10
        reasons.append("Price above 20 EMA")
    if current > ema50.iloc[-1]:
        score += 10
        reasons.append("Price above 50 EMA")
    if current > ema200.iloc[-1]:
        score += 15
        reasons.append("Price above 200 EMA (long-term uptrend)")

    if ema20.iloc[-1] > ema50.iloc[-1] > ema100.iloc[-1]:
        score += 15
        reasons.append("Bullish EMA alignment (20>50>100)")
    if ema50.iloc[-1] > ema200.iloc[-1]:
        score += 10
        reasons.append("Golden cross (50 EMA > 200 EMA)")

    adx_ind = trend.ADXIndicator(df["High"], df["Low"], close, window=14)
    adx_val = adx_ind.adx().iloc[-1]
    di_plus = adx_ind.adx_pos().iloc[-1]
    di_minus = adx_ind.adx_neg().iloc[-1]
    if pd.isna(adx_val) or pd.isna(di_plus) or pd.isna(di_minus):
        adx_val, di_plus, di_minus = 0.0, 0.0, 0.0
    bullish_direction = di_plus > di_minus

    if adx_val > 25 and bullish_direction:
        score += 15
        reasons.append(f"Strong BULLISH trend (ADX={adx_val:.0f}, DI+>DI-)")
    elif adx_val > 20 and bullish_direction:
        score += 8
        reasons.append(f"Moderate bullish trend (ADX={adx_val:.0f})")
    elif adx_val > 25 and not bullish_direction:
        score += 0
        reasons.append(f"Strong BEARISH trend (ADX={adx_val:.0f}, DI->DI+) — avoid")
    else:
        reasons.append(f"Weak/no trend (ADX={adx_val:.0f})")

    if n >= 20 and current > 0:
        x = np.arange(20)
        y = close.iloc[-20:].values
        slope = np.polyfit(x, y, 1)[0]
        slope_pct = (slope * 20 / current) * 100
        if slope_pct > 3:
            score += 15
            reasons.append(f"Strong upward slope ({slope_pct:.1f}% over 20d)")
        elif slope_pct > 0:
            score += 8
            reasons.append(f"Mild upward slope ({slope_pct:.1f}% over 20d)")
        else:
            reasons.append(f"Downward slope ({slope_pct:.1f}% over 20d)")

    if n >= 60:
        highs_20 = df["High"].iloc[-20:].max()
        highs_40_60 = df["High"].iloc[-60:-20].max()
        lows_20 = df["Low"].iloc[-20:].min()
        lows_40_60 = df["Low"].iloc[-60:-20].min()
        if highs_20 > highs_40_60 and lows_20 > lows_40_60:
            score += 10
            reasons.append("Higher highs & higher lows (classic uptrend)")

    return min(score, 100), reasons


def score_momentum(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Score momentum signals (0-100) — monotonic in RSI: higher RSI <= 70 scores higher."""
    score = 0
    reasons = []
    close = df["Close"]

    rsi = momentum.RSIIndicator(close, window=14).rsi()
    rsi_val = rsi.iloc[-1]
    if pd.isna(rsi_val):
        rsi_val = 50.0
    if 50 < rsi_val < 70:
        score += 20
        reasons.append(f"RSI in bullish zone ({rsi_val:.0f})")
    elif 40 < rsi_val <= 50:
        score += 10
        reasons.append(f"RSI neutral ({rsi_val:.0f})")
    elif 70 <= rsi_val < 80:
        score += 12
        reasons.append(f"RSI strong but extended ({rsi_val:.0f})")
    elif rsi_val >= 80:
        score += 5
        reasons.append(f"RSI overbought ({rsi_val:.0f}) — caution")
    elif 30 < rsi_val <= 40:
        score += 8
        reasons.append(f"RSI weak ({rsi_val:.0f})")
    elif rsi_val <= 30:
        score += 12
        reasons.append(f"RSI oversold ({rsi_val:.0f}) — potential bounce")
    else:
        reasons.append(f"RSI bearish ({rsi_val:.0f})")

    macd_ind = trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_hist = macd_ind.macd_diff().iloc[-1]
    macd_hist_prev = macd_ind.macd_diff().iloc[-2]
    if macd_hist > 0:
        score += 15
        reasons.append("MACD histogram positive")
    if macd_hist > macd_hist_prev:
        score += 10
        reasons.append("MACD histogram increasing")

    macd_line = macd_ind.macd()
    signal_line = macd_ind.macd_signal()
    if len(macd_line) >= 5:
        for i in range(-5, 0):
            if (macd_line.iloc[i] > signal_line.iloc[i] and
                macd_line.iloc[i-1] <= signal_line.iloc[i-1]):
                score += 15
                reasons.append("Recent MACD bullish crossover")
                break

    stoch = momentum.StochRSIIndicator(close, window=14, smooth1=3, smooth2=3)
    stoch_k = stoch.stochrsi_k().iloc[-1] * 100
    stoch_d = stoch.stochrsi_d().iloc[-1] * 100
    if 20 < stoch_k < 80:
        score += 10
        reasons.append(f"StochRSI in healthy range ({stoch_k:.0f})")
    if stoch_k > stoch_d and stoch_k < 50:
        score += 10
        reasons.append("StochRSI bullish crossover from oversold")

    roc = momentum.ROCIndicator(close, window=20).roc().iloc[-1]
    if roc > 5:
        score += 10
        reasons.append(f"Strong 20d price momentum ({roc:.1f}%)")
    elif roc > 0:
        score += 5
        reasons.append(f"Positive 20d momentum ({roc:.1f}%)")

    # --- Phase 1: Momentum-decay penalty (bagholder defense) ---
    # If both 5-bar ROC and 5-bar OBV slope have flattened to <30% of their
    # 20-bar counterparts, the trend is rolling over even though absolute
    # levels still look healthy. This is the bagholder fingerprint.
    if len(close) >= 25:
        roc5 = momentum.ROCIndicator(close, window=5).roc().iloc[-1]
        roc20 = momentum.ROCIndicator(close, window=20).roc().iloc[-1]
        if pd.notna(roc5) and pd.notna(roc20) and abs(roc20) > 0.1:
            roc_ratio = abs(roc5) / abs(roc20)
        else:
            roc_ratio = 1.0

        obv_series = volume.OnBalanceVolumeIndicator(close, df["Volume"]).on_balance_volume()
        if len(obv_series) >= 20:
            obv5 = obv_series.iloc[-5:].dropna()
            obv20 = obv_series.iloc[-20:].dropna()
            if len(obv5) >= 2 and len(obv20) >= 2:
                slope5 = np.polyfit(np.arange(len(obv5)), obv5.values, 1)[0]
                slope20 = np.polyfit(np.arange(len(obv20)), obv20.values, 1)[0]
                if abs(slope20) > 1:
                    obv_ratio = abs(slope5) / abs(slope20)
                else:
                    obv_ratio = 1.0
            else:
                obv_ratio = 1.0
        else:
            obv_ratio = 1.0

        if roc_ratio < 0.3 and obv_ratio < 0.25:
            score = max(score - 25, 0)
            reasons.append("⚠️ Momentum decaying — ROC and OBV both flatlining (bagholder risk)")

    return min(score, 100), reasons


def score_volatility(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Score volatility conditions (0-100)."""
    score = 0
    reasons = []
    close = df["Close"]

    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=14)
    atr_val = atr.average_true_range().iloc[-1]
    last_close = close.iloc[-1]
    if pd.isna(atr_val) or last_close <= 0:
        atr_pct = 0.0
    else:
        atr_pct = (atr_val / last_close) * 100

    if 1.5 < atr_pct < 4.0:
        score += 30
        reasons.append(f"ATR {atr_pct:.1f}% — ideal swing range")
    elif 1.0 < atr_pct <= 1.5:
        score += 15
        reasons.append(f"ATR {atr_pct:.1f}% — low volatility, smaller moves")
    elif atr_pct >= 4.0:
        score += 10
        reasons.append(f"ATR {atr_pct:.1f}% — HIGH volatility, risky")
    else:
        score += 5
        reasons.append(f"ATR {atr_pct:.1f}% — very low, no momentum")

    bb = volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    bb_mid = bb.bollinger_mavg()
    last_mid = bb_mid.iloc[-1]
    if pd.notna(last_mid) and last_mid > 0:
        bb_width = ((bb_upper.iloc[-1] - bb_lower.iloc[-1]) / last_mid) * 100
        # Element-wise width with NaN safety.
        bb_width_series = ((bb_upper - bb_lower) / bb_mid.where(bb_mid > 0, np.nan)) * 100
        bb_width_series = bb_width_series.dropna()
        if len(bb_width_series) >= 60:
            avg_width = bb_width_series.iloc[-60:].mean()
        elif len(bb_width_series) > 0:
            avg_width = bb_width_series.mean()
        else:
            avg_width = bb_width
    else:
        bb_width = 0.0
        avg_width = 0.0

    if avg_width > 0 and bb_width < avg_width * 0.7:
        score += 30
        reasons.append(f"BB Squeeze detected (width={bb_width:.1f}% vs avg={avg_width:.1f}%)")
    elif avg_width > 0 and bb_width < avg_width:
        score += 15
        reasons.append(f"BB narrowing ({bb_width:.1f}% vs avg={avg_width:.1f}%)")
    else:
        score += 10
        reasons.append(f"BB expanded ({bb_width:.1f}%)")

    if close.iloc[-1] > bb_mid.iloc[-1]:
        score += 15
        reasons.append("Price above BB middle (bullish)")

    if len(close) >= 40:
        recent_vol = close.iloc[-20:].pct_change().std()
        prior_vol = close.iloc[-40:-20].pct_change().std()
        if recent_vol < prior_vol * 0.8:
            score += 15
            reasons.append("Volatility contracting (energy building)")

    return min(score, 100), reasons


def score_volume(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Score volume signals (0-100). Weighted toward OBV trend (accumulation)."""
    score = 0
    reasons = []
    close = df["Close"]
    vol = df["Volume"]

    vol_avg_20 = vol.rolling(20).mean()
    last_avg = vol_avg_20.iloc[-1]
    if pd.notna(last_avg) and last_avg > 0:
        vol_ratio = vol.iloc[-1] / last_avg
    else:
        vol_ratio = 0.0
        reasons.append("Volume reference unavailable (insufficient data)")

    if vol_ratio > 2.0:
        score += 20
        reasons.append(f"Volume {vol_ratio:.1f}x above average — heavy activity")
    elif vol_ratio > 1.3:
        score += 15
        reasons.append(f"Volume {vol_ratio:.1f}x above average")
    elif vol_ratio > 0.7:
        score += 10
        reasons.append(f"Volume normal ({vol_ratio:.1f}x avg)")
    else:
        reasons.append(f"Volume below average ({vol_ratio:.1f}x) — low interest")

    # OBV trend — accumulation/distribution detector.
    obv = volume.OnBalanceVolumeIndicator(close, vol).on_balance_volume()
    if len(obv) >= 20:
        obv_window = obv.iloc[-20:].dropna()
        if len(obv_window) >= 2:
            obv_slope = np.polyfit(np.arange(len(obv_window)), obv_window.values, 1)[0]
            avg_v = vol_avg_20.iloc[-1] if pd.notna(vol_avg_20.iloc[-1]) and vol_avg_20.iloc[-1] > 0 else 0
            obv_norm = abs(obv_slope) / avg_v if avg_v > 0 else 0
            if obv_slope > 0 and obv_norm > 0.01:
                score += 25
                reasons.append("OBV strongly rising — institutional accumulation")
            elif obv_slope > 0:
                score += 18
                reasons.append("OBV trending up — accumulation detected")
            elif obv_slope < 0 and obv_norm > 0.01:
                score += 0
                reasons.append("OBV strongly falling — distribution (selling)")
            else:
                score += 5
                reasons.append("OBV flat — no clear accumulation")

    # Price rising + volume trend (not just single day spike)
    if len(close) >= 10:
        price_up = close.iloc[-1] > close.iloc[-10]
        vol_trend_up = vol.iloc[-5:].mean() > vol.iloc[-20:].mean() * 0.9  # Relaxed threshold
        if price_up and vol_trend_up:
            score += 20
            reasons.append("Price rising with volume support")
        elif price_up and not vol_trend_up:
            score += 8
            reasons.append("Price rising but volume weak — watch for reversal")

    # Recent volume spike (any day in last 5)
    for i in range(-5, 0):
        if vol_avg_20.iloc[i] > 0 and vol.iloc[i] > 2 * vol_avg_20.iloc[i]:
            score += 15
            reasons.append("Recent volume spike detected")
            break

    # Short-term volume acceleration
    if len(vol) >= 20:
        vol_5d = vol.iloc[-5:].mean()
        vol_20d = vol_avg_20.iloc[-1]
        if vol_20d > 0 and vol_5d / vol_20d > 1.2:
            score += 10
            reasons.append("Volume accelerating (5d > 20d avg)")

    return min(score, 100), reasons


def score_backtest(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Score based on backtesting key strategies on this stock."""
    from .strategies import STRATEGIES
    from .engine import run_backtest, TradeConfig
    from .analytics import compute_metrics

    score = 0
    reasons = []
    config = TradeConfig(initial_capital=100_000, stop_loss_pct=0.07)

    best_sharpe = -99
    best_name = ""
    profitable_count = 0
    positive_sharpe_count = 0
    valid_strategies = 0

    for name, strat_func in STRATEGIES.items():
        try:
            strat_data = strat_func(df)
            result = run_backtest(strat_data, config)
            metrics = compute_metrics(result)

            if metrics["total_trades"] < 30:
                continue

            valid_strategies += 1

            if metrics["sharpe_ratio"] > best_sharpe:
                best_sharpe = metrics["sharpe_ratio"]
                best_name = strat_data["strategy_name"].iloc[-1]
            if metrics["total_return_pct"] > 0:
                profitable_count += 1
            if metrics["sharpe_ratio"] > 0.3:
                positive_sharpe_count += 1
        except Exception as e:
            from ._logging import get_logger
            get_logger(__name__).debug("strategy %s failed during scoring: %s", name, e)
            continue

    total = max(valid_strategies, 1)
    if valid_strategies == 0:
        return 0.0, ["No valid backtest strategies executed (insufficient trades)."]
    ratio = profitable_count / total
    sharpe_ratio = positive_sharpe_count / total

    # Score based on profitability
    if ratio >= 0.6:
        score += 25
        reasons.append(f"{profitable_count}/{total} strategies profitable — strategy-friendly")
    elif ratio >= 0.3:
        score += 12
        reasons.append(f"{profitable_count}/{total} strategies profitable — mixed")
    else:
        score += 5
        reasons.append(f"Only {profitable_count}/{total} strategies profitable — tough stock")

    # Score based on best Sharpe
    if best_sharpe > 1.5:
        score += 30
        reasons.append(f"Best: {best_name} (Sharpe={best_sharpe:.2f}) — excellent")
    elif best_sharpe > 1.0:
        score += 25
        reasons.append(f"Best: {best_name} (Sharpe={best_sharpe:.2f}) — very good")
    elif best_sharpe > 0.5:
        score += 15
        reasons.append(f"Best: {best_name} (Sharpe={best_sharpe:.2f}) — decent")
    elif best_sharpe > 0:
        score += 8
        reasons.append(f"Best: {best_name} (Sharpe={best_sharpe:.2f}) — marginal")
    else:
        reasons.append(f"No strategy has positive Sharpe — avoid")

    # Consistency bonus: multiple strategies with good Sharpe
    if sharpe_ratio >= 0.5:
        score += 25
        reasons.append(f"{positive_sharpe_count}/{total} strategies with Sharpe>0.3 — highly consistent")
    elif sharpe_ratio >= 0.3:
        score += 15
        reasons.append(f"{positive_sharpe_count}/{total} strategies with Sharpe>0.3 — consistent")
    elif sharpe_ratio > 0:
        score += 5
        reasons.append(f"{positive_sharpe_count}/{total} strategies with Sharpe>0.3 — some edge")

    # Average return bonus
    if valid_strategies > 0 and profitable_count > 0:
        score += 10
    elif valid_strategies > 0:
        score += 5

    return min(score, 100), reasons


def score_risk(df: pd.DataFrame) -> tuple[float, list[str], dict]:
    """Score current risk (0-100, higher = LESS risky). Returns levels."""
    score = 0
    reasons = []
    close = df["Close"]
    current = close.iloc[-1]

    if len(close) >= 252:
        high_52w = df["High"].iloc[-252:].max()
        dist_from_high = ((high_52w - current) / high_52w) * 100
        if dist_from_high < 5:
            score += 10
            reasons.append(f"Near 52w high ({dist_from_high:.1f}% away)")
        elif dist_from_high < 15:
            score += 25
            reasons.append(f"{dist_from_high:.1f}% from 52w high — good entry zone")
        elif dist_from_high < 30:
            score += 15
            reasons.append(f"{dist_from_high:.1f}% from 52w high — check for bottom")
        else:
            score += 5
            reasons.append(f"{dist_from_high:.1f}% from 52w high — deep correction")

    recent_high = df["High"].iloc[-30:].max()
    recent_dd = ((recent_high - current) / recent_high) * 100
    if recent_dd < 3:
        score += 20
        reasons.append(f"Minimal recent pullback ({recent_dd:.1f}%)")
    elif recent_dd < 8:
        score += 15
        reasons.append(f"Healthy pullback ({recent_dd:.1f}%)")
    elif recent_dd < 15:
        score += 8
        reasons.append(f"Significant pullback ({recent_dd:.1f}%)")
    else:
        score += 3
        reasons.append(f"Deep recent drop ({recent_dd:.1f}%)")

    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1] if len(close) >= 200 else ema50
    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=14).average_true_range().iloc[-1]
    if pd.isna(atr) or atr <= 0:
        atr = max(current * 0.02, 0.01)

    if pd.notna(ema50) and ema50 > 0:
        dist_to_ema50 = ((current - ema50) / ema50) * 100
    else:
        dist_to_ema50 = 0.0
    if 0 < dist_to_ema50 < 3:
        score += 20
        reasons.append(f"Near 50 EMA support ({dist_to_ema50:.1f}% above)")
    elif dist_to_ema50 < 0:
        score += 5
        reasons.append("Below 50 EMA — bearish, higher risk")
    else:
        score += 12

    # --- STOP LOSS: must ALWAYS be below current price ---
    # Find nearest support level below price
    recent_low = df["Low"].iloc[-20:].min()
    swing_low = df["Low"].iloc[-60:].min() if len(df) >= 60 else recent_low
    atr_based_sl = current - 2 * atr

    # Pick the tightest SL that's still below price
    sl_candidates = [s for s in [recent_low, swing_low, atr_based_sl, ema50, ema200] if s < current]
    if sl_candidates:
        # Use the highest support below price (tightest stop)
        stop_loss = max(sl_candidates)
        # But ensure at least 1% below current (avoid too tight)
        stop_loss = min(stop_loss, current * 0.99)
    else:
        # Fallback: 5% below current
        stop_loss = current * 0.95

    # Ensure SL is at least ATR-distance away (not unreasonably tight)
    if current - stop_loss < 0.5 * atr:
        stop_loss = current - 1.5 * atr

    target_1 = current + max(2.5 * atr, 2 * (current - stop_loss))
    target_2 = current + max(4 * atr, 3 * (current - stop_loss))

    if current > 0:
        sl_pct = ((current - stop_loss) / current) * 100
        t1_pct = ((target_1 - current) / current) * 100
    else:
        sl_pct, t1_pct = 0.1, 0.0
    risk_reward = (t1_pct / sl_pct) if sl_pct > 0.1 else 0.1

    if risk_reward >= 3.0:
        score += 25
        reasons.append(f"Risk/Reward {risk_reward:.1f}:1 — excellent")
    elif risk_reward >= 2.0:
        score += 18
        reasons.append(f"Risk/Reward {risk_reward:.1f}:1 — good")
    elif risk_reward >= 1.5:
        score += 12
        reasons.append(f"Risk/Reward {risk_reward:.1f}:1 — acceptable")
    elif risk_reward >= 1.0:
        score += 6
        reasons.append(f"Risk/Reward {risk_reward:.1f}:1 — marginal")
    else:
        score += 3
        reasons.append(f"Risk/Reward {risk_reward:.1f}:1 — poor")

    # SL distance quality bonus
    if 3 < sl_pct < 8:
        score += 10
        reasons.append(f"SL distance {sl_pct:.1f}% — ideal for swing trading")

    levels = {
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "risk_reward": risk_reward,
        "entry_zone": f"₹{current - atr:.0f} - ₹{current + atr * 0.3:.0f}",
    }

    return min(score, 100), reasons, levels


def analyze_stock(df: pd.DataFrame, symbol: str, run_backtests: bool = True, nifty_df=None) -> ScoreBreakdown:
    """
    Full stock analysis with GO/NO-GO verdict.
    
    Pipeline:
    1. Score 6 core dimensions (trend, momentum, volume, volatility, backtest, risk)
    2. Run advanced indicators (Ichimoku, SAR, CCI, Keltner, Donchian)
    3. Detect market regime
    4. Calculate weighted final score with indicator adjustments
    5. Estimate win probability and expected value
    """
    from .indicators import (ichimoku, keltner_channels, parabolic_sar, 
                              cci_indicator, donchian_channels,
                              detect_market_regime, estimate_probability)

    result = ScoreBreakdown(symbol=symbol)
    result.current_price = df["Close"].iloc[-1]

    # Phase 1: Core scoring (6 dimensions)
    result.trend_score, trend_r = score_trend(df)
    result.momentum_score, mom_r = score_momentum(df)
    result.volatility_score, vol_r = score_volatility(df)
    result.volume_score, volume_r = score_volume(df)
    result.risk_score, risk_r, levels = score_risk(df)

    if run_backtests:
        result.backtest_score, bt_r = score_backtest(df)
        backtest_dim_in_play = True
    else:
        result.backtest_score = 0
        bt_r = ["Backtest dimension skipped on fast path; re-added as nightly cache in roadmap Phase 2"]
        backtest_dim_in_play = False

    # Phase 2: Advanced indicators (adjust core scores ±5-10 pts)
    from ._logging import get_logger
    _log = get_logger(__name__)
    adv_reasons = []
    try:
        ichi = ichimoku(df)
        if ichi["above_cloud"]:
            result.trend_score = min(result.trend_score + 5, 100)
            result.ichimoku_signal = "BULLISH"
        elif not ichi["above_cloud"] and not ichi.get("below_cloud", True):
            result.ichimoku_signal = "NEUTRAL"
        else:
            result.trend_score = max(result.trend_score - 5, 0)
            result.ichimoku_signal = "BEARISH"
        adv_reasons.extend(ichi["reasons"])
    except Exception as e:
        _log.warning("ichimoku failed for %s: %s", symbol, e)
        result.warnings.append("Ichimoku unavailable")

    try:
        sar = parabolic_sar(df)
        if sar["bullish"]:
            result.trend_score = min(result.trend_score + 3, 100)
            result.sar_signal = f"BULLISH (SAR ₹{sar['sar']:.0f})"
        else:
            result.trend_score = max(result.trend_score - 3, 0)
            result.sar_signal = f"BEARISH (SAR ₹{sar['sar']:.0f})"
        adv_reasons.extend(sar["reasons"])
    except Exception as e:
        _log.warning("parabolic_sar failed for %s: %s", symbol, e)

    try:
        cci = cci_indicator(df)
        if cci["cci"] > 100:
            result.momentum_score = min(result.momentum_score + 5, 100)
        elif cci["cci"] < -100:
            result.momentum_score = max(result.momentum_score - 5, 0)
        adv_reasons.extend(cci["reasons"])
    except Exception as e:
        _log.warning("cci failed for %s: %s", symbol, e)

    try:
        kelt = keltner_channels(df)
        if kelt["above"]:
            result.momentum_score = min(result.momentum_score + 5, 100)
        adv_reasons.extend(kelt["reasons"])
    except Exception as e:
        _log.warning("keltner failed for %s: %s", symbol, e)

    try:
        donch = donchian_channels(df)
        adv_reasons.extend(donch["reasons"])
    except Exception as e:
        _log.warning("donchian failed for %s: %s", symbol, e)

    # Phase 3: Market regime
    try:
        regime = detect_market_regime(df)
        result.regime = regime["regime"]
        # Regime adjustment — applied ONCE here (estimate_probability does not double-penalize)
        if regime["regime"] == "TRENDING_DOWN":
            result.trend_score = max(result.trend_score - 10, 0)
            adv_reasons.append("⚠️ Market regime: TRENDING DOWN")
        elif regime["regime"] == "VOLATILE":
            result.volatility_score = max(result.volatility_score - 10, 0)
            adv_reasons.append("⚠️ Market regime: HIGH VOLATILITY")
        elif regime["regime"] == "TRENDING_UP":
            result.trend_score = min(result.trend_score + 5, 100)
            adv_reasons.append("✅ Market regime: TRENDING UP")
    except Exception as e:
        _log.warning("market regime detection failed for %s: %s", symbol, e)
        result.regime = "UNKNOWN"

    # Branch on the caller's intent, not on the realized backtest_score: a legitimate
    # 0 from score_backtest (no strategy produced ≥30 trades) is itself a signal worth
    # weighting at 15%, and silently collapsing it into the 5-dim path would mask weak
    # strategy-friendliness in the CLI Analyze report.
    if backtest_dim_in_play:
        weights = {
            "trend": 0.25, "momentum": 0.20, "volume": 0.15,
            "volatility": 0.10, "backtest": 0.15, "risk": 0.15,
        }
        result.final_score = (
            result.trend_score * weights["trend"]
            + result.momentum_score * weights["momentum"]
            + result.volume_score * weights["volume"]
            + result.volatility_score * weights["volatility"]
            + result.backtest_score * weights["backtest"]
            + result.risk_score * weights["risk"]
        )
    else:
        # Fast-path weights — each = original_weight / (1 - 0.15), rounded to 2dp.
        weights = {"trend": 0.30, "momentum": 0.23, "volume": 0.18, "volatility": 0.12, "risk": 0.17}
        assert abs(sum(weights.values()) - 1.0) < 1e-9
        result.final_score = (
            result.trend_score * weights["trend"]
            + result.momentum_score * weights["momentum"]
            + result.volume_score * weights["volume"]
            + result.volatility_score * weights["volatility"]
            + result.risk_score * weights["risk"]
        )

    import os

    if result.final_score >= 65:
        result.verdict = "GO"
        result.confidence = "HIGH" if result.final_score >= 75 else "MEDIUM"
    elif result.final_score >= 45:
        result.verdict = "WAIT"
        result.confidence = "MEDIUM" if result.final_score >= 55 else "LOW"
    else:
        result.verdict = "AVOID"
        result.confidence = "HIGH" if result.final_score < 30 else "MEDIUM"

    # --- Phase 2 features (additive boosters + defensive gate, behind NSE_SCORER_ENGINE=v2) ---
    # Runs AFTER verdict assignment so regime_gate can downgrade GO → WAIT.
    if os.getenv("NSE_SCORER_ENGINE", "v1") == "v2":
        from .features.relative_strength import rs_vs_nifty_boost
        if nifty_df is not None:
            rs_boost, rs_reason = rs_vs_nifty_boost(df, nifty_df)
            if rs_boost > 0:
                result.final_score = min(result.final_score + rs_boost, 100)
            adv_reasons.append(rs_reason)

        from .features.regime_gate import regime_block
        if nifty_df is not None:
            blocked, regime_reason = regime_block(nifty_df)
            if blocked:
                # Defensive downgrade — never let GO survive in hostile tape.
                if result.verdict == "GO":
                    result.verdict = "WAIT"
                    result.confidence = "LOW"
                adv_reasons.append(regime_reason)
            else:
                adv_reasons.append(regime_reason)

    result.reasons = trend_r + mom_r + vol_r + volume_r + bt_r + risk_r + adv_reasons
    result.stop_loss = levels["stop_loss"]
    result.target_1 = levels["target_1"]
    result.target_2 = levels["target_2"]
    result.risk_reward = levels["risk_reward"]
    result.entry_zone = levels["entry_zone"]

    # Phase 5: Probability estimation
    try:
        prob = estimate_probability({
            "final_score": result.final_score,
            "trend_score": result.trend_score,
            "volume_score": result.volume_score,
        }, df)
        result.win_probability = prob["win_probability"]
        result.expected_gain_pct = prob["expected_gain_pct"]
        result.expected_loss_pct = prob["expected_loss_pct"]
        result.expected_value_pct = prob["expected_value_pct"]
        # If expected value is negative, downgrade verdict — never trade negative-EV setups.
        # Cover both GO (downgrade to AVOID) and WAIT (downgrade to AVOID) — only an
        # explicit AVOID verdict already reflects the risk and is left unchanged.
        if (
            result.expected_value_pct is not None
            and result.expected_value_pct < 0
            and result.verdict in ("GO", "WAIT")
        ):
            result.verdict = "AVOID"
            result.confidence = "MEDIUM"
            adv_reasons.append("⛔ Negative expected value — verdict downgraded to AVOID")
    except Exception as e:
        _log.warning("probability estimation failed for %s: %s", symbol, e)
        result.win_probability = 50.0

    # Warnings
    if result.trend_score < 30:
        result.warnings.append("WEAK TREND — no clear direction")
    if result.volume_score < 25:
        result.warnings.append("LOW VOLUME — poor liquidity")
    if result.risk_score < 30:
        result.warnings.append("HIGH RISK — poor risk/reward right now")
    if result.backtest_score < 25:
        result.warnings.append("POOR BACKTEST — strategies historically fail here")
    if result.win_probability < 40:
        result.warnings.append(f"LOW WIN PROBABILITY — {result.win_probability:.0f}%")

    return result


def print_analysis(score: ScoreBreakdown) -> str:
    """Print formatted analysis report."""
    if score.verdict == "GO":
        vd = ">>> GO — TAKE THE TRADE <<<"
        bc = "#"
    elif score.verdict == "WAIT":
        vd = "--- WAIT — NOT YET, WATCH ---"
        bc = "="
    else:
        vd = "!!! AVOID — STAY OUT !!!"
        bc = "-"

    def bar(val, w=25):
        filled = int(val / 100 * w)
        return "#" * filled + "." * (w - filled)

    lines = []
    lines.append("")
    lines.append("+" + "=" * 65 + "+")
    lines.append(f"|  STOCK ANALYSIS: {score.symbol}")
    lines.append(f"|  Price: Rs.{score.current_price:,.2f}")
    lines.append("+" + "=" * 65 + "+")
    lines.append(f"|")
    lines.append(f"|  VERDICT:  {vd}")
    lines.append(f"|  Score:    {score.final_score:.0f}/100  (Confidence: {score.confidence})")
    lines.append(f"|")
    lines.append("+" + "-" * 65 + "+")
    lines.append(f"|  SCORE BREAKDOWN")
    lines.append(f"|")
    lines.append(f"|   Trend      [{bar(score.trend_score)}] {score.trend_score:5.0f}/100")
    lines.append(f"|   Momentum   [{bar(score.momentum_score)}] {score.momentum_score:5.0f}/100")
    lines.append(f"|   Volatility [{bar(score.volatility_score)}] {score.volatility_score:5.0f}/100")
    lines.append(f"|   Volume     [{bar(score.volume_score)}] {score.volume_score:5.0f}/100")
    lines.append(f"|   Backtest   [{bar(score.backtest_score)}] {score.backtest_score:5.0f}/100")
    lines.append(f"|   Risk       [{bar(score.risk_score)}] {score.risk_score:5.0f}/100")
    lines.append(f"|")
    lines.append("+" + "-" * 65 + "+")
    lines.append(f"|  TRADE PLAN")
    lines.append(f"|")
    lines.append(f"|   Entry Zone:    {score.entry_zone}")
    lines.append(f"|   Stop Loss:     Rs.{score.stop_loss:,.2f}")
    lines.append(f"|   Target 1:      Rs.{score.target_1:,.2f}")
    lines.append(f"|   Target 2:      Rs.{score.target_2:,.2f}")
    lines.append(f"|   Risk/Reward:   {score.risk_reward:.1f}:1")

    # Position sizing (2% risk rule for Rs.1L capital)
    sl_per_share = score.current_price - score.stop_loss
    if sl_per_share > 0:
        capital = 100_000
        max_risk = capital * 0.02  # Risk 2% of capital
        shares = int(max_risk / sl_per_share)
        pos_size = shares * score.current_price
        max_loss = shares * sl_per_share
        lines.append(f"|")
        lines.append(f"|   --- Position Sizing (2% risk on Rs.1L) ---")
        lines.append(f"|   Buy:           {shares} shares = Rs.{pos_size:,.0f}")
        lines.append(f"|   Max Loss:      Rs.{max_loss:,.0f} ({max_loss/capital*100:.1f}% of capital)")
        lines.append(f"|   SL Distance:   Rs.{sl_per_share:,.2f} ({sl_per_share/score.current_price*100:.1f}%)")
    lines.append(f"|")

    if score.warnings:
        lines.append("+" + "-" * 65 + "+")
        lines.append(f"|  !! WARNINGS !!")
        for w in score.warnings:
            lines.append(f"|   * {w}")
        lines.append(f"|")

    lines.append("+" + "-" * 65 + "+")
    lines.append(f"|  SIGNALS")
    lines.append(f"|")
    for r in score.reasons:
        lines.append(f"|   > {r}")
    lines.append(f"|")
    lines.append("+" + "=" * 65 + "+")

    report = "\n".join(lines)
    print(report)
    return report
