"""
NSE Data Fetcher
Fetches historical OHLCV data for NSE/BSE stocks using yfinance.
Supports .NS (NSE) and .BO (BSE) suffixes.
Includes file-based caching to avoid re-downloading.
"""

import yfinance as yf
import pandas as pd
import os
import hashlib
from datetime import datetime, timedelta
from typing import Optional

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".nse_trading_lab_cache")


def _cache_path(symbol: str, exchange: str, start: str) -> str:
    """Generate cache file path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    key = f"{symbol}_{exchange}_{start}"
    return os.path.join(CACHE_DIR, f"{key}.parquet")


def _cache_is_fresh(path: str, max_age_hours: int = 4) -> bool:
    """Check if cached data is still fresh."""
    if not os.path.exists(path):
        return False
    age = datetime.now().timestamp() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def fetch_nse(
    symbol: str,
    start: str = "2015-01-01",
    end: Optional[str] = None,
    exchange: str = "NS",
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch historical data for an Indian stock.
    Uses local cache to avoid re-downloading (refreshes every 4 hours).
    """
    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")

    # Check cache first
    cache_file = _cache_path(symbol, exchange, start)
    if use_cache and _cache_is_fresh(cache_file):
        try:
            df = pd.read_parquet(cache_file)
            if len(df) > 0:
                return df
        except Exception:
            pass

    ticker = f"{symbol}.{exchange}" if exchange else symbol
    print(f"Fetching {ticker} from {start} to {end}...")

    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)

    if df.empty:
        raise ValueError(
            f"No data found for {ticker}. Check symbol or try exchange='BO' for BSE."
        )

    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Clean up
    df = df.dropna()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    print(f"  Loaded {len(df)} trading days ({df.index[0].date()} to {df.index[-1].date()})")

    # Save to cache
    if use_cache:
        try:
            df.to_parquet(cache_file)
        except Exception:
            pass  # Cache is optional

    return df


def fetch_multiple(
    symbols: list[str],
    start: str = "2015-01-01",
    end: Optional[str] = None,
    exchange: str = "NS",
) -> dict[str, pd.DataFrame]:
    """Fetch data for multiple symbols. Returns dict of symbol -> DataFrame."""
    data = {}
    for sym in symbols:
        try:
            data[sym] = fetch_nse(sym, start, end, exchange)
        except Exception as e:
            print(f"  SKIP {sym}: {e}")
    return data


def fetch_nifty50(start: str = "2015-01-01", end: Optional[str] = None) -> pd.DataFrame:
    """Fetch Nifty 50 index data for benchmarking."""
    return fetch_nse("^NSEI", start, end, exchange="")


# Common Nifty 50 constituents for screening
NIFTY50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "HCLTECH", "AXISBANK", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "BAJFINANCE", "DMART", "NTPC",
    "TATAMOTORS", "WIPRO", "POWERGRID", "M&M", "ULTRACEMCO",
    "ONGC", "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS",
    "COALINDIA", "TECHM", "BAJAJFINSV", "NESTLEIND", "HDFCLIFE",
    "GRASIM", "DIVISLAB", "CIPLA", "BPCL", "DRREDDY",
    "SBILIFE", "BRITANNIA", "EICHERMOT", "INDUSINDBK", "TATACONSUM",
    "APOLLOHOSP", "HINDALCO", "HEROMOTOCO", "BAJAJ-AUTO", "SHRIRAMFIN",
]

# Nifty Next 50 constituents
NIFTY_NEXT50_SYMBOLS = [
    "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "ATGL", "AUROPHARMA",
    "BANDHANBNK", "BANKBARODA", "BEL", "BERGEPAINT", "BOSCHLTD",
    "CANBK", "CHOLAFIN", "COLPAL", "CONCOR", "DABUR",
    "DLF", "GAIL", "GODREJCP", "HAL", "HAVELLS",
    "ICICIPRULI", "IDEA", "IDFCFIRSTB", "INDIANB", "INDIGO",
    "IOC", "IRCTC", "JINDALSTEL", "JSWENERGY", "LICI",
    "LUPIN", "MARICO", "MAXHEALTH", "MOTHERSON", "MUTHOOTFIN",
    "NAUKRI", "NHPC", "NMDC", "OBEROIRLTY", "OFSS",
    "PAGEIND", "PFC", "PIDILITIND", "PNB", "POLYCAB",
    "RECLTD", "SBICARD", "SIEMENS", "TATAPOWER", "TORNTPHARM",
]

# Full Nifty 100
NIFTY100_SYMBOLS = NIFTY50_SYMBOLS + NIFTY_NEXT50_SYMBOLS
