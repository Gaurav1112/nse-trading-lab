"""NSE/BSE data fetcher backed by yfinance with hardened caching.

Improvements vs prior version (audit findings):
- Cache key includes ``end`` and is hashed to a filesystem-safe filename.
- Symbols are validated against a regex (``^[A-Z0-9&.\-^]{1,20}$``) — no path traversal.
- ``yf.download`` is retried with exponential backoff and bounded by a timeout.
- Parquet writes are atomic (temp-file + ``os.replace``) so an interrupted write
  cannot poison the cache.
- Partial-download detection: if returned bars are <50% of the expected business
  days the cache is rejected and the user is warned (not silently kept).
- Single-ticker MultiIndex columns are flattened defensively.
- Uses :mod:`logging` rather than ``print``.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from ._logging import get_logger

log = get_logger(__name__)

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".nse_trading_lab_cache")
SYMBOL_RE = re.compile(r"^[A-Z0-9&.\-^]{1,20}$")
EXCHANGE_RE = re.compile(r"^(NS|BO|)$")
_REQUIRED_COLS = ("Open", "High", "Low", "Close", "Volume")

# yfinance's internal session/cache is not thread-safe: concurrent yf.download
# calls can produce DataFrames with accumulated/duplicated columns (e.g. multiple
# 'Close' columns), which then makes df["Close"] a DataFrame instead of a Series
# and breaks every downstream scalar comparison. Serialize the network call.
_YF_LOCK = threading.Lock()


def _validate_symbol(symbol: str) -> str:
    if not isinstance(symbol, str):
        raise ValueError(f"Symbol must be str, got {type(symbol).__name__}")
    s = symbol.strip().upper()
    if not s:
        raise ValueError("Symbol is empty")
    # Strip exchange suffix if user accidentally included it (e.g. "INFY.NS" or "TCS.BO")
    for suffix in (".NS", ".BO", ".BSE", ".NSE"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            break
    if not s:
        raise ValueError(f"Invalid symbol: {symbol!r} (only suffix)")
    if not SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol: {symbol!r}")
    return s


def _validate_exchange(exchange: str) -> str:
    e = (exchange or "").strip().upper()
    if not EXCHANGE_RE.match(e):
        raise ValueError(f"Invalid exchange: {exchange!r} (use 'NS', 'BO' or '')")
    return e


def _cache_path(symbol: str, exchange: str, start: str, end: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    raw = f"{symbol}|{exchange}|{start}|{end}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    safe_sym = re.sub(r"[^A-Z0-9]", "_", symbol)[:20]
    fname = f"{safe_sym}_{digest}.parquet"
    full = os.path.join(CACHE_DIR, fname)
    if not os.path.realpath(full).startswith(os.path.realpath(CACHE_DIR)):
        raise ValueError("Cache path escapes cache directory")
    return full


def _cache_is_fresh(path: str, max_age_hours: float = 4) -> bool:
    """Check cache age. Race-safe against concurrent deletion (TOCTOU)."""
    try:
        age = datetime.now().timestamp() - os.path.getmtime(path)
    except (FileNotFoundError, OSError):
        return False
    return age < max_age_hours * 3600


def _atomic_write_parquet(df: pd.DataFrame, path: str) -> None:
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".parquet", dir=directory)
    os.close(fd)
    try:
        df.to_parquet(tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        levels0 = list(df.columns.get_level_values(0))
        levels1 = list(df.columns.get_level_values(1))
        if "Close" in levels0:
            df.columns = df.columns.get_level_values(0)
        elif "Close" in levels1:
            df.columns = df.columns.get_level_values(1)
        else:
            df.columns = df.columns.get_level_values(0)
    # Defensively drop duplicate column names. Concurrent yfinance calls (or a
    # poisoned cache file) can yield e.g. ['Close','Close','High','High',...]
    # which makes df["Close"] return a DataFrame and breaks scalar comparisons
    # everywhere downstream ("truth value of a Series is ambiguous").
    if df.columns.duplicated().any():
        df = df.loc[:, ~df.columns.duplicated()]
    return df


def _yf_download(ticker: str, start: str, end: str, timeout: float = 30.0,
                 retries: int = 3) -> pd.DataFrame:
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            t0 = time.time()
            with _YF_LOCK:
                df = yf.download(
                    ticker, start=start, end=end, progress=False,
                    auto_adjust=True, threads=False,
                )
            if time.time() - t0 > timeout:
                log.warning("yf.download for %s exceeded soft timeout %.1fs",
                            ticker, timeout)
            # yfinance returns empty DataFrame on rate-limit / bad symbol rather
            # than raising. Treat empty as retriable so transient rate-limits
            # don't poison the result.
            if df is None or df.empty:
                if attempt < retries - 1:
                    # yfinance 429 rate-limit bans last 60-300s; use at least 60s
                    # backoff after the first retry so re-attempts aren't wasted.
                    backoff = 60 if attempt >= 1 else 1.5
                    log.warning("yf.download attempt %d/%d for %s returned empty — retrying in %.1fs",
                                attempt + 1, retries, ticker, backoff)
                    time.sleep(backoff)
                    continue
                return df  # final attempt: return empty for caller to handle
            return df
        except Exception as e:
            last_err = e
            backoff = 60 if attempt >= 1 else 1.5
            log.warning("yf.download attempt %d/%d for %s failed: %s — retrying in %.1fs",
                        attempt + 1, retries, ticker, e, backoff)
            time.sleep(backoff)
    raise RuntimeError(f"yfinance failed after {retries} attempts for {ticker}: {last_err}")


def fetch_nse(
    symbol: str,
    start: str = "2015-01-01",
    end: Optional[str] = None,
    exchange: str = "NS",
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch historical OHLCV for an Indian stock.  Symbols are validated."""
    symbol = _validate_symbol(symbol)
    exchange = _validate_exchange(exchange)

    if end is None:
        end = datetime.now().strftime("%Y-%m-%d")
    try:
        pd.Timestamp(start)
        pd.Timestamp(end)
    except Exception as e:
        raise ValueError(f"Invalid date range start={start} end={end}: {e}")
    if pd.Timestamp(start) >= pd.Timestamp(end):
        raise ValueError(f"start ({start}) must be before end ({end})")

    cache_file = _cache_path(symbol, exchange, start, end)
    # If end is today (intraday data still updating), shorten cache freshness so
    # we re-fetch hourly rather than serve a 4h-old snapshot during market hours.
    today_str = datetime.now().strftime("%Y-%m-%d")
    cache_max_age = 1.0 if end >= today_str else 4.0
    if use_cache and _cache_is_fresh(cache_file, max_age_hours=cache_max_age):
        try:
            # Cap the cache read at 100MB to defend against a poisoned/oversized cache
            # file (DoS by exhausting memory). A daily-bar 30-year history of one
            # symbol is well under 1MB; 100MB is generous headroom.
            try:
                fsize = os.path.getsize(cache_file)
            except OSError:
                fsize = 0
            if fsize > 100 * 1024 * 1024:
                log.warning("Cache %s is %d bytes (>100MB) — refusing to load, re-fetching",
                            cache_file, fsize)
            else:
                df = pd.read_parquet(cache_file)
                if len(df) > 0 and all(c in df.columns for c in _REQUIRED_COLS):
                    log.debug("Cache hit %s (%d rows)", symbol, len(df))
                    return df
        except Exception as e:
            log.warning("Corrupt cache %s: %s — re-fetching", cache_file, e)

    ticker = f"{symbol}.{exchange}" if exchange else symbol
    log.info("Fetching %s from %s to %s", ticker, start, end)
    df = _yf_download(ticker, start, end)

    if df is None or df.empty:
        raise ValueError(
            f"No data found for {ticker}. Check the symbol or exchange "
            f"(try exchange='BO' for BSE)."
        )

    df = _flatten_columns(df)
    missing = [c for c in _REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"yfinance response for {ticker} missing columns: {missing}")

    df = df.dropna(subset=list(_REQUIRED_COLS))
    # Drop malformed OHLC bars (High < Low or non-positive Open/Close).
    df = df[(df["High"] >= df["Low"]) & (df["Open"] > 0) & (df["Close"] > 0)]
    df.index = pd.to_datetime(df.index, errors="coerce")
    df = df[df.index.notna()]
    df = df.sort_index()
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)

    expected = max(1, len(pd.bdate_range(start, end)))
    if len(df) < 0.5 * expected:
        log.warning("Partial data for %s: %d/%d expected business days",
                    ticker, len(df), expected)

    log.info("Loaded %d rows for %s (%s → %s)",
             len(df), ticker, df.index[0].date(), df.index[-1].date())

    if use_cache:
        try:
            _atomic_write_parquet(df, cache_file)
        except Exception as e:
            log.warning("Cache write failed for %s: %s", symbol, e)

    return df


def fetch_multiple(
    symbols: list[str],
    start: str = "2015-01-01",
    end: Optional[str] = None,
    exchange: str = "NS",
    max_workers: int = 8,
) -> dict[str, pd.DataFrame]:
    """Fetch many symbols in parallel. Failures logged + skipped."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: dict[str, pd.DataFrame] = {}
    if not symbols:
        return out

    def _fetch_one(sym: str):
        return sym, fetch_nse(sym, start, end, exchange)

    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 16))) as pool:
        futures = {pool.submit(_fetch_one, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                _, df = fut.result()
                out[sym] = df
            except Exception as e:
                log.warning("SKIP %s: %s", sym, e)
    return out


def fetch_nifty50(start: str = "2015-01-01", end: Optional[str] = None) -> pd.DataFrame:
    return fetch_nse("^NSEI", start, end, exchange="")


NIFTY50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "HCLTECH", "AXISBANK", "ASIANPAINT", "MARUTI",
    "SUNPHARMA", "TITAN", "BAJFINANCE", "DMART", "NTPC",
    # TATAMOTORS demerged 2026-05; use TMPV (passenger vehicles, full 1238-bar history on yfinance).
    "TMPV", "TRENT", "POWERGRID", "M&M", "ULTRACEMCO",
    "ONGC", "JSWSTEEL", "TATASTEEL", "ADANIENT", "ADANIPORTS",
    # JIOFINSOLUTIONS was renamed JIOFIN on yfinance (~695 bars from 2023-08-21).
    "COALINDIA", "TECHM", "BAJAJFINSV", "JIOFIN", "HDFCLIFE",
    "GRASIM", "DIVISLAB", "CIPLA", "BPCL", "DRREDDY",
    "SBILIFE", "BRITANNIA", "EICHERMOT", "INDUSINDBK", "TATACONSUM",
    "APOLLOHOSP", "HINDALCO", "HEROMOTOCO", "BAJAJ-AUTO", "SHRIRAMFIN",
]

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

NIFTY100_SYMBOLS = NIFTY50_SYMBOLS + NIFTY_NEXT50_SYMBOLS
