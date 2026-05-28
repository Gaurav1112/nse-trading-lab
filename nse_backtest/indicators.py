"""
Advanced Indicators Module
===========================
Professional-grade indicators beyond what ta-lib provides.
Each returns a dict of values for use in scoring.

Indicators:
  - Ichimoku Cloud (Goichi Hosoda, 1960s)
  - Keltner Channels (Chester Keltner, 1960)
  - Parabolic SAR (Welles Wilder, 1978)
  - CCI (Donald Lambert, 1980)
  - Donchian Channels (Richard Donchian, 1936)
  - Market Regime detector
  - Win-rate probability estimator
"""

import pandas as pd
import numpy as np
from ta import trend, momentum, volatility, volume as ta_vol


def ichimoku(df: pd.DataFrame, tenkan=9, kijun=26, senkou_b=52) -> dict:
    """
    Ichimoku Kinko Hyo — 5-line system for trend, momentum, support/resistance.
    Reference: Hosoda (1969), Murphy Ch.13
    """
    high, low, close = df["High"], df["Low"], df["Close"]
    n = len(df)

    tenkan_sen = (high.rolling(tenkan).max() + low.rolling(tenkan).min()) / 2
    kijun_sen = (high.rolling(kijun).max() + low.rolling(kijun).min()) / 2
    # Senkou A & B are projected `kijun` bars FORWARD on the chart.
    # To compare today's price to today's plotted cloud, use values calculated `kijun` bars ago.
    senkou_a_raw = (tenkan_sen + kijun_sen) / 2
    senkou_b_raw = (high.rolling(senkou_b).max() + low.rolling(senkou_b).min()) / 2
    senkou_a = senkou_a_raw.shift(kijun)
    senkou_b_line = senkou_b_raw.shift(kijun)
    # Chikou span: today's close plotted `kijun` bars in the past — for momentum confirmation.
    chikou_span = close.shift(-kijun)

    cur = close.iloc[-1]
    ts = tenkan_sen.iloc[-1]
    ks = kijun_sen.iloc[-1]
    sa = senkou_a.iloc[-1]
    sb = senkou_b_line.iloc[-1]
    # Chikou bull: today's close > close `kijun` bars ago (canonical confirmation).
    chikou = close.iloc[-1 - kijun] if len(close) > kijun else np.nan

    # Guard against NaNs in early-bar Ichimoku values.
    if any(pd.isna(v) for v in (ts, ks, sa, sb)):
        return {"score": 0, "reasons": ["Ichimoku unavailable (insufficient data)"],
                "above_cloud": False, "tk_cross": False,
                "tenkan": ts, "kijun": ks, "senkou_a": sa, "senkou_b": sb,
                "chikou": chikou}

    above_cloud = bool(cur > max(sa, sb))
    below_cloud = bool(cur < min(sa, sb))
    in_cloud = not above_cloud and not below_cloud
    tk_cross_bull = ts > ks
    cloud_green = sa > sb
    chikou_bull = pd.notna(chikou) and not np.isnan(chikou) and cur > chikou

    score = 0
    reasons = []
    if above_cloud:
        score += 30
        reasons.append("Price above Ichimoku cloud (strong bullish)")
    elif in_cloud:
        score += 10
        reasons.append("Price inside Ichimoku cloud (indecisive)")
    else:
        reasons.append("Price below Ichimoku cloud (bearish)")

    if tk_cross_bull:
        score += 15
        reasons.append("Tenkan > Kijun (bullish momentum)")
    if cloud_green:
        score += 10
        reasons.append("Cloud is green (bullish structure)")
    if chikou_bull:
        score += 5
        reasons.append("Chikou confirms (price > price 26 bars ago)")

    return {"score": min(score, 100), "reasons": reasons,
            "above_cloud": above_cloud, "below_cloud": below_cloud, "tk_cross": tk_cross_bull,
            "tenkan": ts, "kijun": ks, "senkou_a": sa, "senkou_b": sb,
            "chikou": chikou}


def keltner_channels(df: pd.DataFrame, window=20, atr_mult=2.0) -> dict:
    """
    Keltner Channels — EMA ± ATR multiplier.
    Tighter than Bollinger = uses ATR not std dev.
    Reference: Keltner (1960), Kaufman Ch.9
    """
    close = df["Close"]
    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=window).average_true_range()
    ema = close.ewm(span=window, adjust=False).mean()

    upper = ema + atr_mult * atr
    lower = ema - atr_mult * atr
    cur = close.iloc[-1]

    upper_v = upper.iloc[-1]
    lower_v = lower.iloc[-1]
    mid = ema.iloc[-1]
    if any(pd.isna(v) for v in (upper_v, lower_v, mid)):
        return {"score": 0, "reasons": ["Keltner unavailable (insufficient data)"],
                "upper": upper_v, "lower": lower_v, "mid": mid,
                "width_pct": 0.0, "above": False}

    above = cur > upper_v
    below = cur < lower_v
    width = ((upper_v - lower_v) / mid * 100) if mid > 0 else 0.0

    score = 0
    reasons = []
    if above:
        score += 20
        reasons.append(f"Price above Keltner upper (strong momentum)")
    elif below:
        reasons.append("Price below Keltner lower (weak/oversold)")
    else:
        score += 10
        reasons.append("Price within Keltner channels")

    return {"score": min(score, 100), "reasons": reasons,
            "upper": upper_v, "lower": lower_v,
            "mid": mid, "width_pct": width, "above": above}


def parabolic_sar(df: pd.DataFrame) -> dict:
    """
    Parabolic SAR — trailing stop system.
    Reference: Wilder (1978), Murphy Ch.9
    """
    sar = trend.PSARIndicator(df["High"], df["Low"], df["Close"])
    sar_val = sar.psar().iloc[-1]
    cur = df["Close"].iloc[-1]

    if pd.isna(sar_val) or cur <= 0:
        return {"score": 0, "reasons": ["SAR unavailable"], "sar": sar_val,
                "bullish": False, "distance_pct": 0.0}
    bullish = cur > sar_val
    distance = abs(cur - sar_val) / cur * 100

    score = 0
    reasons = []
    if bullish:
        score += 20
        reasons.append(f"SAR bullish (SAR=₹{sar_val:.0f}, {distance:.1f}% below price)")
    else:
        reasons.append(f"SAR bearish (SAR=₹{sar_val:.0f}, {distance:.1f}% above price)")

    return {"score": score, "reasons": reasons, "sar": sar_val,
            "bullish": bullish, "distance_pct": distance}


def cci_indicator(df: pd.DataFrame, window=20) -> dict:
    """
    CCI — Commodity Channel Index.
    >100 = overbought/strong, <-100 = oversold/weak.
    Reference: Lambert (1980), Kaufman Ch.8
    """
    cci = trend.CCIIndicator(df["High"], df["Low"], df["Close"], window=window).cci()
    val = cci.iloc[-1]

    score = 0
    reasons = []
    if val > 100:
        score += 15
        reasons.append(f"CCI {val:.0f} — strong bullish momentum")
    elif val > 0:
        score += 10
        reasons.append(f"CCI {val:.0f} — mild bullish")
    elif val > -100:
        score += 5
        reasons.append(f"CCI {val:.0f} — mild bearish")
    else:
        reasons.append(f"CCI {val:.0f} — strong bearish")

    return {"score": score, "reasons": reasons, "cci": val}


def donchian_channels(df: pd.DataFrame, window=20) -> dict:
    """
    Donchian Channels — highest high / lowest low.
    Breakout system: new high = buy, new low = sell.
    Reference: Donchian (1936), Turtle Trading
    """
    high_ch = df["High"].rolling(window).max()
    low_ch = df["Low"].rolling(window).min()
    cur = df["Close"].iloc[-1]

    hi, lo = high_ch.iloc[-1], low_ch.iloc[-1]
    if pd.isna(hi) or pd.isna(lo) or cur <= 0:
        return {"score": 0, "reasons": ["Donchian unavailable"],
                "upper": hi, "lower": lo, "width_pct": 0.0}
    at_high = cur >= hi * 0.99
    at_low = cur <= lo * 1.01
    width = (hi - lo) / cur * 100

    score = 0
    reasons = []
    if at_high:
        score += 20
        reasons.append(f"At {window}d Donchian high (breakout zone)")
    elif at_low:
        reasons.append(f"At {window}d Donchian low (breakdown zone)")
    else:
        score += 10
        reasons.append(f"Mid-range in {window}d Donchian channel")

    return {"score": score, "reasons": reasons,
            "upper": high_ch.iloc[-1], "lower": low_ch.iloc[-1], "width_pct": width}


def detect_market_regime(df: pd.DataFrame) -> dict:
    """
    Market regime detection using multiple methods.
    Reference: Lopez de Prado (2018) Ch.3, Chan (2009)
    
    Returns: TRENDING_UP, TRENDING_DOWN, RANGING, VOLATILE
    """
    close = df["Close"]
    n = len(close)

    # Method 1: ADX for trend strength
    adx_ind = trend.ADXIndicator(df["High"], df["Low"], close, window=14)
    adx = adx_ind.adx().iloc[-1]
    di_plus = adx_ind.adx_pos().iloc[-1]
    di_minus = adx_ind.adx_neg().iloc[-1]

    # Method 2: Slope of 50-period regression
    if n >= 50:
        slope = np.polyfit(np.arange(50), close.iloc[-50:].values, 1)[0]
        slope_pct = (slope * 50 / close.iloc[-1]) * 100
    else:
        slope_pct = 0

    # Method 3: Volatility regime (ATR vs historical)
    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, 14).average_true_range()
    last_close = close.iloc[-1]
    atr_pct = (atr.iloc[-1] / last_close * 100) if (pd.notna(atr.iloc[-1]) and last_close > 0) else 0.0
    if n >= 60:
        recent_close_mean = close.iloc[-60:].mean()
        atr_avg = (atr.iloc[-60:].mean() / recent_close_mean * 100) if recent_close_mean > 0 else atr_pct
    else:
        atr_avg = atr_pct

    # Method 4: EMA alignment
    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]
    ema200 = close.ewm(span=200).mean().iloc[-1] if n >= 200 else ema50

    # Determine regime
    high_vol = atr_pct > atr_avg * 1.5
    trending = adx > 25
    bullish = di_plus > di_minus and slope_pct > 0
    bearish = di_minus > di_plus and slope_pct < 0

    if high_vol and not trending:
        regime = "VOLATILE"
        regime_score = 20  # Unfavorable for swing trading
    elif trending and bullish:
        regime = "TRENDING_UP"
        regime_score = 90
    elif trending and bearish:
        regime = "TRENDING_DOWN"
        regime_score = 15
    elif not trending:
        regime = "RANGING"
        regime_score = 40
    else:
        regime = "MIXED"
        regime_score = 50

    return {
        "regime": regime, "regime_score": regime_score,
        "adx": adx, "slope_pct": slope_pct,
        "atr_pct": atr_pct, "atr_avg": atr_avg,
        "ema_aligned": ema20 > ema50 > ema200,
    }


def estimate_probability(score_breakdown: dict, df: pd.DataFrame) -> dict:
    """
    Convert scoring into probability estimates.
    Uses logistic mapping: score → probability.
    
    Reference: Aronson (2006) — scores should map to expected outcomes.
    
    Methodology:
    1. Base probability from score (logistic function)
    2. Adjust for market regime
    3. Adjust for volume confirmation
    4. Add expected return estimate from ATR
    """
    final_score = score_breakdown.get("final_score", 50)
    trend_s = score_breakdown.get("trend_score", 50)
    vol_s = score_breakdown.get("volume_score", 50)
    regime = detect_market_regime(df)
    
    # Logistic mapping: score 0-100 → probability 15%-85%
    # Centered at 50 (50% probability), steepness = 0.06
    raw_prob = 1 / (1 + np.exp(-0.06 * (final_score - 50)))
    
    # Scale to 15-85% range (never 0% or 100% — epistemic humility)
    base_prob = 15 + raw_prob * 70
    
    # Regime is already reflected in trend_score / volatility_score upstream (scorer.py),
    # which feeds final_score → base_prob via the logistic. To avoid double-counting, we
    # do NOT subtract a second regime penalty here. Keep a small confirmation bump only.
    regime_adj = 0
    if regime["regime"] == "TRENDING_UP":
        regime_adj = +3
    elif regime["regime"] == "VOLATILE":
        regime_adj = -3
    
    # Volume confirmation (±5%)
    vol_adj = 0
    if vol_s >= 60:
        vol_adj = +5
    elif vol_s <= 25:
        vol_adj = -5
    
    adjusted_prob = np.clip(base_prob + regime_adj + vol_adj, 10, 90)
    
    # Expected return from ATR (what's realistically achievable)
    close = df["Close"]
    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, 14).average_true_range().iloc[-1]
    last_close = close.iloc[-1]
    if pd.notna(last_close) and last_close > 0 and pd.notna(atr):
        atr_pct = atr / last_close * 100
    else:
        atr_pct = 0.0
    
    # Expected 2-week return: ~2-3 ATR if signal is correct
    expected_gain = atr_pct * 2.5  # ~2.5 ATR gain target
    expected_loss = atr_pct * 1.5  # ~1.5 ATR stop loss
    
    # Expected value = P(win) × gain - P(loss) × loss
    p_win = adjusted_prob / 100
    expected_value = p_win * expected_gain - (1 - p_win) * expected_loss
    
    return {
        "win_probability": round(adjusted_prob, 1),
        "expected_gain_pct": round(expected_gain, 1),
        "expected_loss_pct": round(expected_loss, 1),
        "expected_value_pct": round(expected_value, 2),
        "regime": regime["regime"],
        "regime_score": regime["regime_score"],
        "base_prob": round(base_prob, 1),
        "regime_adj": regime_adj,
        "vol_adj": vol_adj,
    }
