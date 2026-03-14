"""
Sample Data Generator
Generates realistic NSE-like stock price data for testing strategies
when live data is unavailable.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta


def generate_stock_data(
    symbol: str = "SAMPLE",
    start: str = "2018-01-01",
    end: str = "2026-03-01",
    initial_price: float = 500.0,
    annual_return: float = 0.12,
    annual_volatility: float = 0.30,
    seed: int = 42,
) -> pd.DataFrame:
    """
    Generate realistic OHLCV stock data using geometric Brownian motion
    with regime changes (trending / sideways / crash periods).

    Mimics typical Indian mid-cap behavior.
    """
    np.random.seed(seed)

    dates = pd.bdate_range(start=start, end=end)
    n = len(dates)

    # Daily parameters
    daily_return = annual_return / 252
    daily_vol = annual_volatility / np.sqrt(252)

    # Generate regime changes (bull/bear/sideways)
    regime_length = np.random.randint(40, 120, size=n // 60)
    regimes = []
    for length in regime_length:
        regime_type = np.random.choice(["bull", "bear", "sideways"], p=[0.45, 0.25, 0.30])
        regimes.extend([regime_type] * length)
    regimes = regimes[:n]
    while len(regimes) < n:
        regimes.append("sideways")

    # Generate returns with regime-dependent drift
    returns = np.zeros(n)
    for i in range(n):
        if regimes[i] == "bull":
            drift = daily_return * 2.5
            vol = daily_vol * 0.8
        elif regimes[i] == "bear":
            drift = -daily_return * 2.0
            vol = daily_vol * 1.5
        else:
            drift = daily_return * 0.3
            vol = daily_vol * 0.6
        returns[i] = drift + vol * np.random.randn()

    # Occasional gap-ups/gap-downs (earnings, news)
    for _ in range(n // 60):
        idx = np.random.randint(0, n)
        returns[idx] += np.random.choice([-1, 1]) * np.random.uniform(0.03, 0.08)

    # Build price series
    close = np.zeros(n)
    close[0] = initial_price
    for i in range(1, n):
        close[i] = close[i - 1] * (1 + returns[i])
        close[i] = max(close[i], 1.0)  # Prevent negative prices

    # Generate OHLV from close
    high = close * (1 + np.abs(np.random.randn(n) * daily_vol * 0.7))
    low = close * (1 - np.abs(np.random.randn(n) * daily_vol * 0.7))
    open_price = close * (1 + np.random.randn(n) * daily_vol * 0.3)

    # Ensure OHLC consistency
    high = np.maximum(high, np.maximum(open_price, close))
    low = np.minimum(low, np.minimum(open_price, close))

    # Volume: higher on volatile days
    base_volume = 500_000
    vol_factor = np.abs(returns) / daily_vol
    volume = (base_volume * (1 + vol_factor * 3) * np.random.uniform(0.5, 1.5, n)).astype(int)

    df = pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Adj Close": close,
            "Volume": volume,
        },
        index=dates[:n],
    )

    print(f"Generated {symbol}: {len(df)} days, ₹{close[0]:.0f} → ₹{close[-1]:.0f}")
    return df


# Pre-built scenarios for testing different market conditions
def trending_stock():
    """Strong uptrend stock (like RELIANCE 2020-2024)"""
    return generate_stock_data("TRENDING", initial_price=1000, annual_return=0.25, annual_volatility=0.25, seed=101)

def volatile_midcap():
    """Volatile mid-cap (similar to ARSSBL-type behavior)"""
    return generate_stock_data("VOLATILE_MIDCAP", initial_price=500, annual_return=0.05, annual_volatility=0.45, seed=202)

def sideways_stock():
    """Range-bound large cap"""
    return generate_stock_data("SIDEWAYS", initial_price=800, annual_return=0.06, annual_volatility=0.20, seed=303)
