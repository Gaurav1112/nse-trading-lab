"""Desk I/O: market data, user state files, per-market settings and CheckContext assembly.

This and nse_lists are the only desk modules that touch the network or the user's files.
positions.json and trade_journal.json are read here and never written by the desk.
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
from dataclasses import fields, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import calendars as cal
from . import nse_lists
from .audit import data_dir
from .checks import CheckContext
from .markets import MarketProfile
from .models import LiveQuote, Limits

log = logging.getLogger(__name__)

DEFAULT_CAPITAL = {"NSE": 100_000.0, "US": 10_000.0}
_PCT_FIELDS = ("risk_per_trade_pct", "max_position_pct", "cash_buffer_pct", "daily_loss_limit_pct")
_SETTINGS_FILE = "desk_settings.json"


# ---------------------------------------------------------------- market data

_OHLC = ["Open", "High", "Low", "Close"]


def _clean_bars(df: pd.DataFrame, what: str) -> pd.DataFrame:
    """Drop bars with a missing O/H/L/C, keep the last of duplicate timestamps, sort by time."""
    missing = [c for c in _OHLC if c not in df.columns]
    if missing:
        raise ValueError(f"{what}: missing columns {', '.join(missing)}")
    df = df.dropna(subset=_OHLC)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if not df.index.is_monotonic_increasing:
        raise ValueError(f"{what}: bars are not in time order")
    if len(df) == 0:
        raise ValueError(f"{what}: no usable bars")
    return df


def _market_now(profile: MarketProfile, now: datetime | None = None) -> datetime:
    now = now or datetime.now(ZoneInfo(profile.tz))
    return now.astimezone(ZoneInfo(profile.tz)) if now.tzinfo is not None else now.replace(tzinfo=ZoneInfo(profile.tz))


def _needs_refetch(profile: MarketProfile, df: pd.DataFrame, now: datetime) -> bool:
    """Just after the close a cached frame may hold a mid-session partial bar: fetch once more, uncached."""
    today = now.date()
    close = cal.session_close_for(profile, today)  # early-close days end at 13:00 ET
    return (cal.is_trading_day(profile, today) and pd.Timestamp(df.index[-1]).date() == today
            and timedelta() <= datetime.combine(today, now.time()) - datetime.combine(today, close) < timedelta(hours=3))


def _finalise_daily(profile: MarketProfile, symbol: str, df: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Clean fetched daily bars: drop non-trading-day bars of covered years and today's partial bar."""
    today = now.date()
    close = cal.session_close_for(profile, today)
    df = _clean_bars(df, f"daily bars for {symbol}")
    # a bar on a non-trading day of a covered year (e.g. a one-hour Muhurat Sunday) is never a real close
    off = [cal.covered(profile, d) and not cal.is_trading_day(profile, d) for d in pd.DatetimeIndex(df.index).date]
    if any(off):
        df = df[[not x for x in off]]
        if len(df) == 0:
            raise ValueError(f"daily bars for {symbol}: no bars on trading days")
    if pd.Timestamp(df.index[-1]).date() == today and now.time() < close:
        df = df.iloc[:-1]  # partial bar: plans are built on completed bars
        if len(df) == 0:
            raise ValueError(f"daily bars for {symbol}: no completed bars")
    return df


def load_daily(profile: MarketProfile, symbol: str, years: int = 4, *, now: datetime | None = None,
               adjusted: bool = True) -> pd.DataFrame:
    """Completed daily bars. Today's bar is fetched when it exists and dropped while the session is open.
    adjusted=False: unadjusted bars (no dividend adjustment), as the scoreboard replays printed tickets."""
    from nse_backtest.data import fetch_nse
    now = _market_now(profile, now)
    today = now.date()
    start = (today - timedelta(days=int(365.25 * years))).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    df = fetch_nse(symbol, start, end=end, exchange=profile.exchange, adjusted=adjusted)
    if _needs_refetch(profile, df, now):
        df = fetch_nse(symbol, start, end=end, exchange=profile.exchange, use_cache=False, adjusted=adjusted)
    return _finalise_daily(profile, symbol, df, now)


def load_daily_many(profile: MarketProfile, symbols, years: int = 4, *,
                    now: datetime | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """`load_daily` for many symbols: (frames, errors) keyed by symbol as given; never raises per symbol.

    Cached symbols are read from the same cache files as `load_daily`; the rest come from one yfinance
    call (per-symbol fallback when that call fails). Each frame equals what `load_daily` returns."""
    from nse_backtest.data import fetch_many_daily
    symbols = list(symbols)
    if not symbols:
        return {}, {}
    now = _market_now(profile, now)
    today = now.date()
    start = (today - timedelta(days=int(365.25 * years))).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    raw, errors = fetch_many_daily(symbols, start, end, profile.exchange,
                                   refetch_if=lambda sym, df: _needs_refetch(profile, df, now))
    errors = dict(errors)
    frames: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if sym not in raw:
            errors.setdefault(sym, f"no daily data for {sym}")
            continue
        try:
            frames[sym] = _finalise_daily(profile, sym, raw[sym], now)
        except Exception as e:  # noqa: BLE001 - one bad symbol never fails the batch
            errors[sym] = str(e)
    return frames, errors


def _yf_frame(df) -> pd.DataFrame:
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df.copy()


def load_intraday(profile: MarketProfile, symbol: str) -> pd.DataFrame:
    from nse_backtest.data import _validate_symbol
    _validate_symbol(symbol)  # ValueError before any network call
    import yfinance
    df = yfinance.download(profile.yf_symbol(symbol), period="60d", interval="15m",
                           progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        raise ValueError(f"no intraday data for {symbol}")
    df = _yf_frame(df)
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(profile.tz).tz_localize(None)
    return _clean_bars(df, f"intraday bars for {symbol}")


def live_quote(profile: MarketProfile, symbol: str) -> LiveQuote | None:
    """Latest price from today's 1-minute bars; None on any failure (the desk then WARNs)."""
    from nse_backtest.data import _validate_symbol
    try:
        _validate_symbol(symbol)
    except ValueError as e:
        log.warning("live quote: invalid symbol %r: %s", symbol, e)
        return None
    try:
        import yfinance
        df = yfinance.download(profile.yf_symbol(symbol), period="1d", interval="1m",
                               progress=False, auto_adjust=False)
        if df is None or len(df) == 0:
            raise ValueError("no 1-minute bars")
        df = _yf_frame(df)
        closes = pd.to_numeric(df["Close"], errors="coerce")
        opens = pd.to_numeric(df["Open"], errors="coerce")
        closes = closes[closes.map(lambda x: math.isfinite(x) and x > 0)]
        opens = opens[opens.map(lambda x: math.isfinite(x) and x > 0)]
        if len(closes) == 0 or len(opens) == 0:
            raise ValueError("no finite prices in 1-minute bars")
        ts = pd.Timestamp(closes.index[-1])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        return LiveQuote(price=float(closes.iloc[-1]), day_open=float(opens.iloc[0]),
                         as_of=ts.tz_convert(profile.tz).to_pydatetime())
    except Exception as e:  # noqa: BLE001 - no quote -> live_price WARNs
        log.warning("live quote unavailable for %s: %s", symbol, e)
        return None


def load_regime(profile: MarketProfile) -> pd.DataFrame:
    """Regime-index daily bars. Raises on failure so the caller can surface the reason."""
    from nse_backtest.data import fetch_nse
    start = (date.today() - timedelta(days=800)).isoformat()
    # index symbols (^NSEI, ^GSPC) take no exchange suffix: '^NSEI.NS' does not exist on Yahoo
    exchange = "" if profile.regime_index.startswith("^") else profile.exchange
    df = fetch_nse(profile.regime_index, start, exchange=exchange)
    if df is None or len(df) == 0:
        raise ValueError(f"no regime index data for {profile.regime_index}")
    return _clean_bars(df, f"regime index {profile.regime_index}")


def _to_date(x) -> date | None:
    if isinstance(x, (list, tuple)):
        for item in x:
            d = _to_date(item)
            if d is not None:
                return d
        return None
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        ts = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(ts) else ts.date()


def next_earnings(profile: MarketProfile, symbol: str) -> tuple[date | None, bool]:
    """(next earnings date, checked). (None, False) means unknown, which the desk WARNs on."""
    from nse_backtest.data import _validate_symbol
    try:
        _validate_symbol(symbol)
    except ValueError as e:
        log.warning("earnings lookup: invalid symbol %r: %s", symbol, e)
        return None, False
    try:
        import yfinance
        cal = yfinance.Ticker(profile.yf_symbol(symbol)).calendar
        raw = cal.get("Earnings Date") if isinstance(cal, dict) else None
        d = _to_date(raw)
        return (d, True) if d is not None else (None, False)
    except Exception as e:  # noqa: BLE001
        log.warning("earnings lookup failed for %s: %s", symbol, e)
        return None, False


# ---------------------------------------------------------------- user state (read-only)

class UserDataError(ValueError):
    """positions.json / trade_journal.json exists but cannot be trusted (unreadable, corrupt, not a list)."""


def _read_list(name: str) -> list[dict]:
    """Missing file -> []. Unreadable, corrupt or not a JSON list -> UserDataError (never a silent [])."""
    path = data_dir() / name
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UserDataError(f"cannot read {name}: {e}") from e
    if not isinstance(data, list):
        raise UserDataError(f"{name} is not a JSON list")
    return [r for r in data if isinstance(r, dict)]


def load_positions() -> list[dict]:
    return _read_list("positions.json")


def load_journal() -> list[dict]:
    return _read_list("trade_journal.json")


def _for_market(rows: list[dict], profile: MarketProfile) -> list[dict]:
    return [r for r in rows if str(r.get("market") or "NSE").upper() == profile.code]


def market_positions(profile: MarketProfile) -> list[dict]:
    """Open-position records for this market, with `market` normalised to upper case."""
    return [dict(p, market=profile.code) for p in _for_market(load_positions(), profile)]


# ---------------------------------------------------------------- settings

def default_limits(profile: MarketProfile) -> Limits:
    return Limits(capital=DEFAULT_CAPITAL[profile.code])


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_limits(limits: Limits) -> None:
    if not _finite(limits.capital) or limits.capital <= 0:
        raise ValueError("capital must be a finite number greater than 0")
    for name in _PCT_FIELDS:
        v = getattr(limits, name)
        if not _finite(v) or not (0 < v <= 100):
            raise ValueError(f"{name} must be a finite number above 0 and at most 100")
    if not _finite(limits.min_rr) or limits.min_rr <= 0:
        raise ValueError("min_rr must be a finite number greater than 0")
    if not isinstance(limits.pdt_guard, bool):
        raise ValueError("pdt_guard must be true or false")


def _settings_path() -> Path:
    return data_dir() / _SETTINGS_FILE


def _read_settings_status() -> tuple[dict, bool]:
    """(settings, ok). ok is False when the file exists but could not be used."""
    path = _settings_path()
    try:
        if not path.exists():
            return {}, True
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("top level is not an object")
        return data, True
    except (OSError, ValueError) as e:
        log.warning("cannot read %s (%s); using defaults", path, e)
        return {}, False


def _read_settings() -> dict:
    return _read_settings_status()[0]


def load_limits_with_status(profile: MarketProfile) -> tuple[Limits, bool]:
    """(limits, fell_back). fell_back is True when a settings file was unreadable or invalid."""
    settings, ok = _read_settings_status()
    entry = settings.get(profile.code)
    base = default_limits(profile)
    if not isinstance(entry, dict):
        return base, not ok
    names = {f.name for f in fields(Limits)}
    try:
        limits = replace(base, **{k: v for k, v in entry.items() if k in names})
        validate_limits(limits)
        return limits, False
    except (TypeError, ValueError) as e:
        log.warning("invalid %s settings (%s); using defaults", profile.code, e)
        return base, True


def load_limits(profile: MarketProfile) -> Limits:
    return load_limits_with_status(profile)[0]


def _backup_corrupt_settings() -> None:
    """Copy an unusable settings file aside before it is overwritten, so nothing is lost."""
    path = _settings_path()
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.corrupt-{datetime.now():%Y%m%d%H%M%S}"))


def save_limits(profile: MarketProfile, limits: Limits) -> None:
    validate_limits(limits)
    settings, ok = _read_settings_status()
    if not ok:
        _backup_corrupt_settings()
    settings[profile.code] = {f.name: getattr(limits, f.name) for f in fields(Limits)}
    path = _settings_path()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- context

_ENTRY_KEYS = ("entry_date", "open_date")
_EXIT_KEYS = ("closed_date", "exit_date")


def _day(row: dict, keys) -> date | None:
    raw = next((row[k] for k in keys if row.get(k)), None)
    try:
        return date.fromisoformat(str(raw)[:10]) if raw is not None else None
    except ValueError:
        return None


def _last_trading_days(profile: MarketProfile, today: date, n: int = 5) -> set[date]:
    """Today (when it is a trading day) and the trading days before it, n in all, from the exchange calendar."""
    out = {today} if cal.is_trading_day(profile, today) else set()
    d = today
    for _ in range(n):  # bounded: each step is one bounded calendar walk
        if len(out) >= n:
            break
        d = cal.previous_trading_day(profile, d)
        out.add(d)
    return out


def day_trades_5d(journal: list[dict], today: date, profile: MarketProfile | None = None) -> int | None:
    """US round trips opened and closed on the same day within the last 5 trading days.
    None when a row that could be one has no readable entry or exit date (count unknowable)."""
    from .markets import US
    try:
        window = _last_trading_days(profile or US, today)
    except RuntimeError as e:  # calendar walk hit its bound: count unknowable, pdt WARNs
        log.warning("PDT window unavailable: %s", e)
        return None
    n = 0
    for r in journal:
        if str(r.get("market") or "NSE").upper() != "US":
            continue
        exit_d = _day(r, _EXIT_KEYS)
        if exit_d is None:
            return None
        if exit_d not in window:
            continue
        entry_d = _day(r, _ENTRY_KEYS)
        if entry_d is None:
            return None
        n += entry_d == exit_d
    return n


def build_context(profile: MarketProfile, limits: Limits, *, now: datetime | None = None,
                  symbol_df: pd.DataFrame | None = None, regime: str | None = None) -> CheckContext:
    """CheckContext. `regime` is the tape regime; None/unknown is assessed as HOSTILE (most
    conservative book cap) but stays None on the context so the regime check WARNs."""
    now = now or datetime.now(ZoneInfo(profile.tz))
    can_trade: bool | None
    reasons: tuple[str, ...] = ()
    journal_readable = True
    try:
        positions = _for_market(load_positions(), profile)
    except UserDataError as e:
        log.warning("%s", e)
        positions = None
    try:
        journal = _for_market(load_journal(), profile)
    except UserDataError as e:
        log.warning("%s", e)
        journal = None
        journal_readable = False
    if positions is None or journal is None:
        positions, journal = [], journal or []
        can_trade = None
        reasons = ("Could not read positions.json/trade_journal.json — risk governor not evaluated",)
    else:
        try:
            from components import risk_governor
            known = regime if regime in ("TRENDING", "MIXED", "HOSTILE") else "HOSTILE"
            verdict = risk_governor.assess(positions, journal, limits.capital, known)
            can_trade, reasons = bool(verdict.can_trade), tuple(verdict.reasons)
        except Exception as e:  # noqa: BLE001 - desk WARNs on None
            log.warning("risk governor failed: %s", e)
            can_trade = None
    ban = None
    if profile.has_ban_list:
        ban = nse_lists.fetch_fno_ban(data_dir() / "cache", now.date())
    turnover = None
    if symbol_df is not None:
        from .daily import turnover_20d
        turnover = turnover_20d(symbol_df)
    # today's gap needs a live day-open: set by the caller from a quote, else None (gap WARNs)
    trades_5d = day_trades_5d(journal, now.date(), profile) if profile.code == "US" and journal_readable else (
        0 if journal_readable else None)
    return CheckContext(profile=profile, limits=limits, now=now, journal=tuple(journal),
                        governor_can_trade=can_trade, governor_reasons=reasons, ban=ban, regime=regime,
                        turnover_20d=turnover, today_gap_pct=None, day_trades_5d=trades_5d,
                        journal_readable=journal_readable)
