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


def _yf_retry(call, label: str, timeout: float = 30.0, retries: int = 3,
              rate_limit_backoff: float = 60.0) -> pd.DataFrame:
    """Run one ``yf.download`` call under ``_YF_LOCK`` with the shared retry/backoff policy.

    Empty responses (rate-limit / bad symbol) are retried; the last one is returned for the
    caller to handle. Exceptions are retried and re-raised as RuntimeError after ``retries``.
    """
    last_err: Optional[Exception] = None
    for attempt in range(retries):
        try:
            t0 = time.time()
            with _YF_LOCK:
                df = call()
            if time.time() - t0 > timeout:
                log.warning("yf.download for %s exceeded soft timeout %.1fs",
                            label, timeout)
            # yfinance returns empty DataFrame on rate-limit / bad symbol rather
            # than raising. Treat empty as retriable so transient rate-limits
            # don't poison the result.
            if df is None or df.empty:
                if attempt < retries - 1:
                    # yfinance 429 rate-limit bans last 60-300s; use at least 60s
                    # backoff after the first retry so re-attempts aren't wasted.
                    backoff = rate_limit_backoff if attempt >= 1 else 1.5
                    log.warning("yf.download attempt %d/%d for %s returned empty — retrying in %.1fs",
                                attempt + 1, retries, label, backoff)
                    time.sleep(backoff)
                    continue
                return df  # final attempt: return empty for caller to handle
            return df
        except Exception as e:
            last_err = e
            backoff = rate_limit_backoff if attempt >= 1 else 1.5
            log.warning("yf.download attempt %d/%d for %s failed: %s — retrying in %.1fs",
                        attempt + 1, retries, label, e, backoff)
            time.sleep(backoff)
    raise RuntimeError(f"yfinance failed after {retries} attempts for {label}: {last_err}")


def _yf_download(ticker: str, start: str, end: str, timeout: float = 30.0,
                 retries: int = 3) -> pd.DataFrame:
    # backoff policy (1.5s, then 60s for yfinance 429 bans) lives in _yf_retry
    return _yf_retry(
        lambda: yf.download(ticker, start=start, end=end, progress=False,
                            auto_adjust=True, threads=False),
        ticker, timeout=timeout, retries=retries, rate_limit_backoff=60,
    )


def _check_dates(start: str, end: str) -> None:
    try:
        pd.Timestamp(start)
        pd.Timestamp(end)
    except Exception as e:
        raise ValueError(f"Invalid date range start={start} end={end}: {e}")
    if pd.Timestamp(start) >= pd.Timestamp(end):
        raise ValueError(f"start ({start}) must be before end ({end})")


def _cache_max_age(end: str) -> float:
    # If end is today (intraday data still updating), shorten cache freshness so
    # we re-fetch hourly rather than serve a 4h-old snapshot during market hours.
    today_str = datetime.now().strftime("%Y-%m-%d")
    return 1.0 if end >= today_str else 4.0


def _read_fresh_cache(cache_file: str, max_age_hours: float) -> Optional[pd.DataFrame]:
    """The cached frame when the file is fresh, readable and complete; otherwise None."""
    if not _cache_is_fresh(cache_file, max_age_hours=max_age_hours):
        return None
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
                log.debug("Cache hit %s (%d rows)", cache_file, len(df))
                return df
    except Exception as e:
        log.warning("Corrupt cache %s: %s — re-fetching", cache_file, e)
    return None


def _clean_download(df: Optional[pd.DataFrame], ticker: str, start: str, end: str) -> pd.DataFrame:
    """Everything fetch_nse does to a raw yfinance frame. Raises ValueError when unusable."""
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
    if df.empty:
        raise ValueError(f"No usable bars for {ticker} after cleaning")

    expected = max(1, len(pd.bdate_range(start, end)))
    if len(df) < 0.5 * expected:
        log.warning("Partial data for %s: %d/%d expected business days",
                    ticker, len(df), expected)

    log.info("Loaded %d rows for %s (%s → %s)",
             len(df), ticker, df.index[0].date(), df.index[-1].date())
    return df


def _write_cache(df: pd.DataFrame, cache_file: str, symbol: str) -> None:
    try:
        _atomic_write_parquet(df, cache_file)
    except Exception as e:
        log.warning("Cache write failed for %s: %s", symbol, e)


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
    _check_dates(start, end)

    cache_file = _cache_path(symbol, exchange, start, end)
    if use_cache:
        cached = _read_fresh_cache(cache_file, _cache_max_age(end))
        if cached is not None:
            return cached

    ticker = f"{symbol}.{exchange}" if exchange else symbol
    log.info("Fetching %s from %s to %s", ticker, start, end)
    df = _clean_download(_yf_download(ticker, start, end), ticker, start, end)

    if use_cache:
        _write_cache(df, cache_file, symbol)

    return df


def _ticker_frame(data: pd.DataFrame, ticker: str, n_tickers: int) -> Optional[pd.DataFrame]:
    """One ticker's raw frame from a group_by='ticker' multi download; None if absent."""
    if not isinstance(data.columns, pd.MultiIndex):
        # yfinance may drop the ticker level for a single ticker
        return data.copy() if n_tickers == 1 else None
    for level in range(data.columns.nlevels):
        names = [str(v).upper() for v in data.columns.get_level_values(level)]
        if ticker.upper() in names:
            key = data.columns.get_level_values(level)[names.index(ticker.upper())]
            sub = data.xs(key, axis=1, level=level).copy()
            break
    else:
        return None
    # the batch frame's index is the union of all tickers' dates: rows that are NaN in every
    # column are padding from other tickers, not bars of this one (a single download has none)
    sub = sub.dropna(how="all")
    if sub.empty:
        return sub
    # the padding turns an int64 Volume into float64; restore it when every value is integral
    vol = sub.get("Volume")
    if isinstance(vol, pd.Series) and pd.api.types.is_float_dtype(vol):
        finite = vol.notna().all() and bool(((vol % 1) == 0).all())
        if finite:
            sub["Volume"] = vol.astype("int64")
    return sub


def fetch_many_daily(
    symbols,
    start: str,
    end: Optional[str] = None,
    exchange: str = "NS",
    use_cache: bool = True,
    refetch_if=None,
) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Daily OHLCV for many symbols with ONE yfinance call for the uncached ones.

    Returns ``(frames, errors)`` keyed by each symbol exactly as given. Every frame equals what
    ``fetch_nse`` returns for that symbol (same cache files, same download parameters, same
    cleaning). A failure for one symbol is an ``errors`` entry and never affects the others.
    ``refetch_if(symbol, cached_df)`` -> True sends a symbol with a fresh cache to the download
    anyway (served neither from nor, in the fallback, re-read from the cache). If the batch call
    fails (exception or entirely empty) the uncached symbols are fetched one by one with
    ``fetch_nse`` instead, so the result is never worse than the per-symbol path.
    """
    frames: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    symbols = list(symbols)
    if not symbols:
        return frames, errors
    try:
        exchange = _validate_exchange(exchange)
        if end is None:
            end = datetime.now().strftime("%Y-%m-%d")
        _check_dates(start, end)
    except ValueError as e:
        return {}, {raw: str(e) for raw in symbols}

    by_symbol: dict[str, list[str]] = {}  # validated symbol -> raw spellings, in input order
    for raw in symbols:
        try:
            sym = _validate_symbol(raw)
        except ValueError as e:
            errors[raw] = str(e)
            continue
        by_symbol.setdefault(sym, []).append(raw)

    got: dict[str, pd.DataFrame] = {}
    bad: dict[str, str] = {}
    pending: list[str] = []
    forced: set[str] = set()
    max_age = _cache_max_age(end)
    for sym in by_symbol:
        cached = _read_fresh_cache(_cache_path(sym, exchange, start, end), max_age) if use_cache else None
        if cached is not None and refetch_if is not None and refetch_if(sym, cached):
            forced.add(sym)
            cached = None
        if cached is None:
            pending.append(sym)
        else:
            got[sym] = cached

    if pending:
        ticker_of = {sym: (f"{sym}.{exchange}" if exchange else sym) for sym in pending}
        tickers = [ticker_of[s] for s in pending]
        label = f"{len(tickers)} tickers" if len(tickers) > 1 else tickers[0]
        log.info("Fetching %s from %s to %s in one call", label, start, end)
        try:
            data = _yf_retry(
                lambda: yf.download(tickers=tickers, start=start, end=end, group_by="ticker",
                                    auto_adjust=True, threads=False, progress=False),
                label, rate_limit_backoff=60,
            )
            if data is None or data.empty:
                raise ValueError("empty response")
            if not isinstance(data.columns, pd.MultiIndex) and len(tickers) > 1:
                raise ValueError("response has no ticker level")
        except Exception as e:  # noqa: BLE001 - fall back to the per-symbol path
            log.warning("Batch download of %s failed (%s) — fetching one by one", label, e)
            for sym in pending:
                try:
                    got[sym] = fetch_nse(sym, start, end, exchange,
                                         use_cache=use_cache and sym not in forced)
                except Exception as e2:  # noqa: BLE001
                    bad[sym] = f"{sym}: {e2}"
        else:
            for sym in pending:
                ticker = ticker_of[sym]
                try:
                    raw_df = _ticker_frame(data, ticker, len(tickers))
                    if raw_df is None:
                        raise ValueError(f"{ticker} missing from the batch response")
                    df = _clean_download(raw_df, ticker, start, end)
                except Exception as e:  # noqa: BLE001
                    bad[sym] = f"{sym}: {e}"
                    continue
                if use_cache:
                    _write_cache(df, _cache_path(sym, exchange, start, end), sym)
                got[sym] = df

    for sym, raws in by_symbol.items():
        for raw in raws:
            if sym in got:
                frames[raw] = got[sym].copy() if len(raws) > 1 else got[sym]
            else:
                errors[raw] = bad.get(sym, f"{sym}: no data")
    return frames, errors


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
