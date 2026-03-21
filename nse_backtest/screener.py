"""
Daily Swing Trading Screener
==============================
Scans NSE stocks and ranks them by swing trading potential.

FIXED: Entry points now use proper technical levels:
- Breakout: Entry at pullback to breakout level (old resistance = new support)
- Reversal: Entry near identified support level
- Squeeze: Entry at upper BB on confirmation
- Volume: Entry at VWAP or day's average price
- Supertrend: Entry at supertrend line (trailing support)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from ta import trend, momentum, volatility, volume as ta_vol


@dataclass
class SwingSetup:
    """A detected swing trading setup."""
    symbol: str
    setup_type: str
    score: float               # Scan-specific setup quality score
    current_price: float
    entry_price: float         # Proper technical entry level
    entry_note: str = ""       # Why this entry level
    stop_loss: float = 0
    target: float = 0
    target_2: float = 0
    risk_reward: float = 0
    trigger: str = ""
    notes: list = field(default_factory=list)
    # Analyzer integration — filled in by run_screener
    analyzer_score: float = 0  # 6-dimension analyzer score (0-100)
    verdict: str = ""          # GO / WAIT / AVOID
    confidence: str = ""       # HIGH / MEDIUM / LOW


def _calc_levels(current, entry, atr, support=None):
    """Calculate SL, targets from proper entry point."""
    if support and support < entry:
        sl = support - 0.5 * atr  # Below support with buffer
    else:
        sl = entry - 1.5 * atr

    # Ensure SL is always below entry
    sl = min(sl, entry * 0.96)  # At least 4% below entry

    risk = entry - sl
    t1 = entry + 2.0 * risk   # 2:1 R:R
    t2 = entry + 3.0 * risk   # 3:1 R:R
    rr = (t1 - entry) / (entry - sl) if (entry - sl) > 0 else 0
    return sl, t1, t2, rr


def scan_breakout(df: pd.DataFrame, symbol: str) -> Optional[SwingSetup]:
    """
    Detect momentum breakout.
    ENTRY: Pullback to breakout level (previous resistance = new support).
    """
    close = df["Close"]
    vol = df["Volume"]
    current = close.iloc[-1]
    n = len(df)
    if n < 50:
        return None

    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    vol_avg = vol.rolling(20).mean()
    high_20 = df["High"].rolling(20).max()

    score = 0
    notes = []

    crossed_20ema = False
    for i in range(-3, 0):
        if close.iloc[i] > ema20.iloc[i] and close.iloc[i - 1] <= ema20.iloc[i - 1]:
            crossed_20ema = True
            break

    broke_high = current >= high_20.iloc[-2]
    vol_confirm = vol.iloc[-1] > 1.3 * vol_avg.iloc[-1]
    uptrend = ema20.iloc[-1] > ema50.iloc[-1]

    if not (crossed_20ema or broke_high):
        return None

    if crossed_20ema:
        score += 30
        notes.append("Price crossed above 20 EMA")
    if broke_high:
        score += 25
        notes.append("New 20-day high breakout")
    if vol_confirm:
        score += 20
        notes.append(f"Volume {vol.iloc[-1] / vol_avg.iloc[-1]:.1f}x avg (confirmed)")
    if uptrend:
        score += 15
        notes.append("Uptrend (20 EMA > 50 EMA)")

    adx_ind = trend.ADXIndicator(df["High"], df["Low"], close, window=14)
    adx_val = adx_ind.adx().iloc[-1]
    if adx_val > 20:
        score += 10
        notes.append(f"ADX trend strength: {adx_val:.0f}")

    if score < 40:
        return None

    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=14).average_true_range().iloc[-1]

    # ENTRY: Pullback to previous resistance (now support)
    # For breakout: entry at the 20-day high level (breakout level)
    # or at 20 EMA (pullback entry)
    breakout_level = high_20.iloc[-2]
    ema20_val = ema20.iloc[-1]

    if current > breakout_level * 1.02:
        # Already ran past breakout — entry on pullback to breakout level
        entry = breakout_level
        entry_note = f"Buy on pullback to Rs.{entry:.0f} (breakout retest)"
    else:
        # Near breakout — entry at 20 EMA or current
        entry = max(ema20_val, current - 0.5 * atr)
        entry_note = f"Buy near Rs.{entry:.0f} (20 EMA support)"

    # Support = 20 EMA or recent swing low
    support = min(ema20_val, df["Low"].iloc[-10:].min())
    sl, t1, t2, rr = _calc_levels(current, entry, atr, support)

    notes.append(entry_note)

    return SwingSetup(
        symbol=symbol, setup_type="BREAKOUT", score=min(score, 100),
        current_price=current, entry_price=round(entry, 2),
        entry_note=entry_note, stop_loss=round(sl, 2),
        target=round(t1, 2), target_2=round(t2, 2), risk_reward=round(rr, 1),
        trigger="Momentum Breakout", notes=notes,
    )


def scan_reversal(df: pd.DataFrame, symbol: str) -> Optional[SwingSetup]:
    """
    Detect oversold bounce in uptrend.
    ENTRY: Near identified support level (EMA / swing low).
    """
    close = df["Close"]
    n = len(df)
    if n < 200:
        return None

    current = close.iloc[-1]
    ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    rsi = momentum.RSIIndicator(close, window=14).rsi()

    score = 0
    notes = []

    if current < ema200:
        return None

    rsi_now = rsi.iloc[-1]
    rsi_prev = rsi.iloc[-3]

    if rsi_now < 35:
        score += 30
        notes.append(f"RSI oversold ({rsi_now:.0f}) in uptrending stock")
    elif rsi_prev < 35 and rsi_now > rsi_prev:
        score += 25
        notes.append(f"RSI bouncing ({rsi_prev:.0f} -> {rsi_now:.0f})")
    else:
        return None

    dist_50 = abs(current - ema50) / ema50 * 100
    if dist_50 < 3:
        score += 20
        notes.append(f"Near 50 EMA support ({dist_50:.1f}% away)")

    day_range = df["High"].iloc[-1] - df["Low"].iloc[-1]
    if day_range > 0:
        candle_pos = (current - df["Low"].iloc[-1]) / day_range
        if candle_pos > 0.7:
            score += 15
            notes.append("Bullish candle (close near high)")

    stoch = momentum.StochRSIIndicator(close, window=14, smooth1=3, smooth2=3)
    k = stoch.stochrsi_k().iloc[-1] * 100
    d = stoch.stochrsi_d().iloc[-1] * 100
    if k > d and k < 30:
        score += 15
        notes.append("StochRSI bullish crossover in oversold zone")

    if score < 40:
        return None

    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=14).average_true_range().iloc[-1]

    # ENTRY: Near support level — 50 EMA or recent swing low
    swing_low = df["Low"].iloc[-10:].min()
    entry = max(swing_low, ema50, current - 0.5 * atr)
    entry = min(entry, current)  # Don't set entry above current price
    entry_note = f"Buy near Rs.{entry:.0f} (support zone)"
    notes.append(entry_note)

    sl, t1, t2, rr = _calc_levels(current, entry, atr, swing_low)

    return SwingSetup(
        symbol=symbol, setup_type="REVERSAL", score=min(score, 100),
        current_price=current, entry_price=round(entry, 2),
        entry_note=entry_note, stop_loss=round(sl, 2),
        target=round(t1, 2), target_2=round(t2, 2), risk_reward=round(rr, 1),
        trigger="Oversold Bounce in Uptrend", notes=notes,
    )


def scan_squeeze(df: pd.DataFrame, symbol: str) -> Optional[SwingSetup]:
    """
    Detect Bollinger Band squeeze.
    ENTRY: On upper band breakout confirmation (not before).
    """
    close = df["Close"]
    n = len(df)
    if n < 60:
        return None

    current = close.iloc[-1]
    bb = volatility.BollingerBands(close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband()
    bb_lower = bb.bollinger_lband()
    bb_mid = bb.bollinger_mavg()
    bb_width = ((bb_upper - bb_lower) / bb_mid) * 100

    avg_width = bb_width.iloc[-60:].mean()
    current_width = bb_width.iloc[-1]

    score = 0
    notes = []

    if current_width < avg_width * 0.6:
        score += 40
        notes.append(f"Tight squeeze ({current_width:.1f}% vs avg {avg_width:.1f}%)")
    elif current_width < avg_width * 0.75:
        score += 25
        notes.append(f"BB narrowing ({current_width:.1f}% vs avg {avg_width:.1f}%)")
    else:
        return None

    if all(bb_width.iloc[i] <= bb_width.iloc[i - 1] for i in range(-4, 0)):
        score += 15
        notes.append("Width contracting consistently (5 days)")

    if current > bb_mid.iloc[-1]:
        score += 15
        notes.append("Price above BB middle — bullish bias")

    adx_ind = trend.ADXIndicator(df["High"], df["Low"], close, window=14)
    adx = adx_ind.adx()
    if len(adx) >= 5 and adx.iloc[-1] > adx.iloc[-5]:
        score += 15
        notes.append(f"ADX rising ({adx.iloc[-5]:.0f} -> {adx.iloc[-1]:.0f})")

    if score < 40:
        return None

    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=14).average_true_range().iloc[-1]

    # ENTRY: At upper Bollinger Band (buy the breakout)
    entry = bb_upper.iloc[-1]
    entry_note = f"Buy on breakout above Rs.{entry:.0f} (upper BB)"
    notes.append(entry_note)

    support = bb_mid.iloc[-1]
    sl, t1, t2, rr = _calc_levels(current, entry, atr, support)

    return SwingSetup(
        symbol=symbol, setup_type="SQUEEZE", score=min(score, 100),
        current_price=current, entry_price=round(entry, 2),
        entry_note=entry_note, stop_loss=round(sl, 2),
        target=round(t1, 2), target_2=round(t2, 2), risk_reward=round(rr, 1),
        trigger="BB Squeeze — Breakout Setup", notes=notes,
    )


def scan_volume_surge(df: pd.DataFrame, symbol: str) -> Optional[SwingSetup]:
    """
    Detect unusual volume with bullish price action.
    ENTRY: At day's average traded price (VWAP proxy).
    """
    close = df["Close"]
    vol = df["Volume"]
    n = len(df)
    if n < 30:
        return None

    current = close.iloc[-1]
    vol_avg = vol.rolling(20).mean()
    vol_ratio = vol.iloc[-1] / vol_avg.iloc[-1] if vol_avg.iloc[-1] > 0 else 1

    score = 0
    notes = []

    if vol_ratio < 1.8:
        return None

    score += 30
    notes.append(f"Volume spike {vol_ratio:.1f}x average")

    if current > df["Open"].iloc[-1]:
        score += 20
        notes.append("Bullish close (above open)")
    else:
        return None

    day_range = df["High"].iloc[-1] - df["Low"].iloc[-1]
    if day_range > 0:
        pos = (current - df["Low"].iloc[-1]) / day_range
        if pos > 0.75:
            score += 15
            notes.append("Strong close near day high")

    spike_count = sum(1 for i in range(-10, 0) if vol_avg.iloc[i] > 0 and vol.iloc[i] > 1.5 * vol_avg.iloc[i])
    if spike_count >= 3:
        score += 15
        notes.append(f"{spike_count} volume spikes in 10 days — accumulation")

    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    if current > ema20:
        score += 10
        notes.append("Above 20 EMA")

    if score < 45:
        return None

    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=14).average_true_range().iloc[-1]

    # ENTRY: At average of day's range (VWAP proxy) or pullback to open
    day_avg = (df["High"].iloc[-1] + df["Low"].iloc[-1] + current) / 3
    entry = min(day_avg, current)
    entry_note = f"Buy near Rs.{entry:.0f} (day avg / VWAP zone)"
    notes.append(entry_note)

    sl = df["Low"].iloc[-1] - 0.5 * atr  # Below day's low
    risk = entry - sl
    t1 = entry + 2.0 * risk
    t2 = entry + 3.0 * risk
    rr = (t1 - entry) / (entry - sl) if (entry - sl) > 0 else 0

    return SwingSetup(
        symbol=symbol, setup_type="VOLUME_SURGE", score=min(score, 100),
        current_price=current, entry_price=round(entry, 2),
        entry_note=entry_note, stop_loss=round(sl, 2),
        target=round(t1, 2), target_2=round(t2, 2), risk_reward=round(rr, 1),
        trigger="Unusual Volume Surge", notes=notes,
    )


def scan_supertrend_flip(df: pd.DataFrame, symbol: str) -> Optional[SwingSetup]:
    """
    Detect Supertrend flipping bullish.
    ENTRY: At supertrend line (trailing support).
    """
    close = df["Close"]
    n = len(df)
    if n < 30:
        return None

    current = close.iloc[-1]
    atr_series = volatility.AverageTrueRange(df["High"], df["Low"], close, window=10).average_true_range()
    hl2 = (df["High"] + df["Low"]) / 2
    upper = hl2 + 3 * atr_series
    lower = hl2 - 3 * atr_series

    direction = pd.Series(index=df.index, dtype=int)
    st_line = pd.Series(index=df.index, dtype=float)
    final_upper = upper.copy()
    final_lower = lower.copy()
    direction.iloc[0] = -1
    st_line.iloc[0] = upper.iloc[0]

    for i in range(1, n):
        prev_close = close.iloc[i - 1]
        # Clamp bands: upper can only decrease, lower can only increase
        if not pd.isna(final_lower.iloc[i - 1]) and prev_close >= final_lower.iloc[i - 1]:
            final_lower.iloc[i] = max(lower.iloc[i], final_lower.iloc[i - 1])
        else:
            final_lower.iloc[i] = lower.iloc[i]
        if not pd.isna(final_upper.iloc[i - 1]) and prev_close <= final_upper.iloc[i - 1]:
            final_upper.iloc[i] = min(upper.iloc[i], final_upper.iloc[i - 1])
        else:
            final_upper.iloc[i] = upper.iloc[i]

        if close.iloc[i] > final_upper.iloc[i]:
            direction.iloc[i] = 1
        elif close.iloc[i] < final_lower.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]

        if direction.iloc[i] == 1:
            st_line.iloc[i] = final_lower.iloc[i]
        else:
            st_line.iloc[i] = final_upper.iloc[i]

    flipped = False
    for i in range(-3, 0):
        if direction.iloc[i] == 1 and direction.iloc[i - 1] == -1:
            flipped = True
            break

    if not flipped:
        return None

    score = 60
    notes = ["Supertrend flipped BULLISH"]

    vol_avg = df["Volume"].rolling(20).mean().iloc[-1]
    if vol_avg > 0 and df["Volume"].iloc[-1] > 1.3 * vol_avg:
        score += 15
        notes.append("Volume confirming the flip")

    rsi_val = momentum.RSIIndicator(close, window=14).rsi().iloc[-1]
    if rsi_val < 65:
        score += 10
        notes.append(f"RSI has room to run ({rsi_val:.0f})")

    atr = atr_series.iloc[-1]

    # ENTRY: At supertrend line (acts as trailing support)
    entry = st_line.iloc[-1]
    entry = min(entry, current)  # Don't set above current
    entry_note = f"Buy near Rs.{entry:.0f} (supertrend support)"
    notes.append(entry_note)

    sl = entry - 1.5 * atr
    risk = entry - sl
    t1 = entry + 2.5 * risk
    t2 = entry + 3.5 * risk
    rr = (t1 - entry) / risk if risk > 0 else 0

    return SwingSetup(
        symbol=symbol, setup_type="SUPERTREND", score=min(score, 100),
        current_price=current, entry_price=round(entry, 2),
        entry_note=entry_note, stop_loss=round(sl, 2),
        target=round(t1, 2), target_2=round(t2, 2), risk_reward=round(rr, 1),
        trigger="Supertrend Bullish Flip", notes=notes,
    )


SCANNERS = [scan_breakout, scan_reversal, scan_squeeze, scan_volume_surge, scan_supertrend_flip]


def scan_trend_continuation(df: pd.DataFrame, symbol: str):
    """
    Detect strong established uptrend suitable for pullback entry.
    This catches smooth uptrends that other scanners miss.
    """
    close = df["Close"]
    n = len(df)
    if n < 100:
        return None

    current = close.iloc[-1]
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean() if n >= 200 else ema50

    score = 0
    notes = []

    # Must be in clear uptrend: EMA alignment
    if not (ema20.iloc[-1] > ema50.iloc[-1]):
        return None
    if not (current > ema50.iloc[-1]):
        return None

    score += 20
    notes.append("EMA alignment: 20 > 50 (confirmed uptrend)")

    if n >= 200 and ema50.iloc[-1] > ema200.iloc[-1]:
        score += 15
        notes.append("Golden cross active (50 EMA > 200 EMA)")

    # Slope: price must be rising — check multiple periods
    slope_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100
    slope_50d = (close.iloc[-1] / close.iloc[-50] - 1) * 100 if n >= 50 else slope_20d
    best_slope = max(slope_20d, slope_50d / 2.5)  # Normalize 50d to comparable scale

    if best_slope > 5:
        score += 20
        notes.append(f"Strong momentum ({slope_20d:.1f}% 20d, {slope_50d:.1f}% 50d)")
    elif best_slope > 1:
        score += 12
        notes.append(f"Positive momentum ({slope_20d:.1f}% 20d, {slope_50d:.1f}% 50d)")
    elif slope_50d > 5:
        score += 8
        notes.append(f"Longer-term uptrend ({slope_50d:.1f}% 50d) with recent pause")
    else:
        return None  # Not enough momentum

    # Price relative to 20 EMA
    dist_20ema = (current - ema20.iloc[-1]) / ema20.iloc[-1] * 100
    if 0 < dist_20ema < 3:
        score += 20
        notes.append(f"Near 20 EMA support ({dist_20ema:.1f}% above) — ideal entry")
    elif 0 < dist_20ema < 6:
        score += 15
        notes.append(f"{dist_20ema:.1f}% above 20 EMA — good pullback zone")
    elif 0 < dist_20ema < 10:
        score += 8
        notes.append(f"{dist_20ema:.1f}% above 20 EMA — wait for pullback")
    elif dist_20ema < 0:
        return None  # Below 20 EMA = trend may be breaking

    # ADX for trend strength
    adx_ind = trend.ADXIndicator(df["High"], df["Low"], close, window=14)
    adx_val = adx_ind.adx().iloc[-1]
    di_plus = adx_ind.adx_pos().iloc[-1]
    di_minus = adx_ind.adx_neg().iloc[-1]
    if adx_val > 25 and di_plus > di_minus:
        score += 15
        notes.append(f"ADX {adx_val:.0f} with DI+ > DI- (strong bullish trend)")
    elif adx_val > 20:
        score += 8

    if score < 38:
        return None

    atr = volatility.AverageTrueRange(df["High"], df["Low"], close, window=14).average_true_range().iloc[-1]

    # Entry at 20 EMA (pullback level)
    entry = ema20.iloc[-1]
    entry = min(entry, current)  # Don't set above current
    entry_note = f"Buy near ₹{entry:.0f} (20 EMA pullback zone)"
    notes.append(entry_note)

    sl = ema50.iloc[-1] - 0.5 * atr  # Below 50 EMA
    sl = min(sl, entry * 0.95)  # At least 5% below entry
    sl = max(sl, entry * 0.85)  # But not more than 15% below
    risk = entry - sl
    if risk <= 0:
        return None
    t1 = entry + max(2.5 * atr, 2 * risk)
    t2 = entry + max(4 * atr, 3 * risk)
    rr = (t1 - entry) / risk

    return SwingSetup(
        symbol=symbol, setup_type="TREND_CONTINUATION", score=min(score, 100),
        current_price=current, entry_price=round(entry, 2),
        entry_note=entry_note, stop_loss=round(sl, 2),
        target=round(t1, 2), target_2=round(t2, 2), risk_reward=round(rr, 1),
        trigger="Trend Continuation — Pullback Entry", notes=notes,
    )


# Add to scanner list
SCANNERS.append(scan_trend_continuation)


def run_screener(stock_data: dict[str, pd.DataFrame], top_n: int = 15) -> list[SwingSetup]:
    """Run all scanners on all stocks. Enriches with analyzer score. Returns ranked list."""
    from .scorer import analyze_stock  # Import here to avoid circular

    all_setups = []
    for symbol, df in stock_data.items():
        if len(df) < 60:
            continue
        for scanner in SCANNERS:
            try:
                setup = scanner(df, symbol)
                if setup is not None and setup.risk_reward >= 1.5:
                    all_setups.append(setup)
            except Exception:
                pass

    # Enrich each setup with analyzer score (the real 6-dimension score)
    for setup in all_setups:
        try:
            if setup.symbol in stock_data:
                s = analyze_stock(stock_data[setup.symbol], setup.symbol, run_backtests=False)
                setup.analyzer_score = s.final_score
                setup.verdict = s.verdict
                setup.confidence = s.confidence
        except Exception:
            setup.analyzer_score = setup.score  # Fallback

    # Sort by analyzer score (the consistent 6-dimension score), not scan score
    all_setups.sort(key=lambda s: s.analyzer_score, reverse=True)
    return all_setups[:top_n]


def print_screener_results(setups: list[SwingSetup]) -> str:
    """Print screener results."""
    lines = [f"\nFound {len(setups)} setups\n"]
    if not setups:
        lines.append("No setups found today. Market may be choppy — stay cash.")
        report = "\n".join(lines)
        print(report)
        return report

    for i, s in enumerate(setups, 1):
        lines.append(f"  #{i} {s.symbol} — {s.trigger} (Score: {s.score:.0f})")
        lines.append(f"      Entry:  Rs.{s.entry_price:,.0f}  ({s.entry_note})")
        lines.append(f"      SL:     Rs.{s.stop_loss:,.0f}  |  T1: Rs.{s.target:,.0f}  |  T2: Rs.{s.target_2:,.0f}")
        lines.append(f"      R:R:    {s.risk_reward:.1f}:1  |  Current: Rs.{s.current_price:,.0f}")
        for note in s.notes:
            if note != s.entry_note:
                lines.append(f"      > {note}")
        lines.append("")

    report = "\n".join(lines)
    print(report)
    return report
