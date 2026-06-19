"""Intraday RSI scanner — 15-minute oversold finder for mean-reversion entries.

Honest scope: this scanner is a SEPARATE workflow from the swing engine.
Its picks are NOT validated by the walk-forward A/B (which is daily-bar,
swing-only). Treat the output as a *watchlist for further investigation*,
not as a backtested strategy.

Mechanism:
  1. Batch-fetch 15-minute bars for the universe via yfinance.
  2. Compute Wilder's RSI(14) on the Close series for each symbol.
  3. Filter to symbols with current RSI < threshold (default 15).
  4. Sort by RSI ascending so the most-oversold appears first.

Caveats:
  - yfinance 15-min data has ~15-minute publication lag.
  - RSI < 15 on 15-min bars is rare — most days you'll see 0 hits.
    That's normal. Lower thresholds (RSI<30) are more common.
  - During market hours the most recent bar may be incomplete.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class IntradayHit:
    symbol: str
    current_price: float
    rsi: float
    volume: int
    change_pct_today: float          # vs today's open
    last_bar_ts: pd.Timestamp
    bars_in_session: int             # how many 15m bars used


def _wilders_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    """Wilder's RSI — exponentially smoothed avg gains / avg losses.
    Returns the most-recent RSI value, or None if insufficient data.
    """
    if closes is None or len(closes) < period + 1:
        return None
    delta = closes.diff().dropna()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    # Use Wilder's smoothing (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    last_avg_gain = float(avg_gain.iloc[-1])
    last_avg_loss = float(avg_loss.iloc[-1])
    if last_avg_loss == 0:
        return 100.0 if last_avg_gain > 0 else 50.0
    rs = last_avg_gain / last_avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _batch_fetch_15m(symbols: list[str], period: str = "5d"):
    """Single yf.download for all symbols at 15m interval. Returns the
    grouped-by-ticker multi-index DataFrame, or None on failure."""
    if not symbols:
        return None
    try:
        import yfinance as yf
        tickers = " ".join(f"{s}.NS" for s in symbols)
        df = yf.download(
            tickers=tickers, period=period, interval="15m",
            group_by="ticker", auto_adjust=True, progress=False, threads=False,
        )
        if df is None or len(df) == 0:
            return None
        return df
    except Exception:
        return None


def _series_for(df, sym: str) -> Optional[pd.DataFrame]:
    """Pull out the per-symbol OHLCV slice from a grouped yf.download frame."""
    full_sym = f"{sym}.NS"
    if df is None or len(df) == 0:
        return None
    # Single-symbol case: yfinance flattens the columns
    try:
        if hasattr(df, "columns") and isinstance(df.columns, pd.MultiIndex):
            if full_sym not in df.columns.get_level_values(0):
                return None
            sub = df[full_sym].dropna()
        else:
            sub = df.dropna()
        if len(sub) == 0 or "Close" not in sub.columns:
            return None
        return sub
    except Exception:
        return None


def scan_rsi(
    symbols: list[str],
    *,
    rsi_threshold: float = 15.0,
    rsi_period: int = 14,
    fetch_period: str = "5d",
) -> list[IntradayHit]:
    """Scan `symbols` for 15-min RSI below `rsi_threshold`.

    Returns the hits sorted by RSI ascending (most oversold first).
    """
    df = _batch_fetch_15m(symbols, period=fetch_period)
    if df is None:
        return []
    hits: list[IntradayHit] = []
    for sym in symbols:
        sub = _series_for(df, sym)
        if sub is None or len(sub) < rsi_period + 1:
            continue
        closes = sub["Close"]
        rsi = _wilders_rsi(closes, period=rsi_period)
        if rsi is None or rsi >= rsi_threshold:
            continue
        last_close = float(closes.iloc[-1])
        last_ts = sub.index[-1]
        # Today's session: bars from today (in IST)
        today_ist = datetime.now(tz=IST).date()
        try:
            today_idx = sub.index.tz_convert(IST).date if sub.index.tz else sub.index.date
            today_mask = pd.Series(today_idx) == today_ist
        except Exception:
            today_mask = pd.Series([False] * len(sub))
        today_sub = sub[today_mask.values] if today_mask.any() else sub.tail(25)
        if len(today_sub) > 0:
            open_today = float(today_sub["Open"].iloc[0])
            change_pct = (last_close - open_today) / open_today * 100 if open_today else 0
            bars_today = len(today_sub)
        else:
            change_pct = 0.0
            bars_today = 0
        volume = int(sub["Volume"].tail(1).iloc[0]) if "Volume" in sub.columns else 0
        hits.append(IntradayHit(
            symbol=sym, current_price=last_close, rsi=float(rsi),
            volume=volume, change_pct_today=float(change_pct),
            last_bar_ts=last_ts, bars_in_session=bars_today,
        ))
    hits.sort(key=lambda h: h.rsi)
    return hits
