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

from . import nse_lists
from .audit import data_dir
from .checks import CheckContext
from .markets import MarketProfile
from .models import Limits

log = logging.getLogger(__name__)

DEFAULT_CAPITAL = {"NSE": 100_000.0, "US": 10_000.0}
_PCT_FIELDS = ("risk_per_trade_pct", "max_position_pct", "cash_buffer_pct", "daily_loss_limit_pct")
_SETTINGS_FILE = "desk_settings.json"


# ---------------------------------------------------------------- market data

def load_daily(profile: MarketProfile, symbol: str, years: int = 4) -> pd.DataFrame:
    from nse_backtest.data import fetch_nse
    start = (date.today() - timedelta(days=int(365.25 * years))).isoformat()
    return fetch_nse(symbol, start, exchange=profile.exchange)


def load_intraday(profile: MarketProfile, symbol: str) -> pd.DataFrame:
    import yfinance
    df = yfinance.download(profile.yf_symbol(symbol), period="60d", interval="15m",
                           progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        raise ValueError(f"no intraday data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    df = df.copy()
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(profile.tz).tz_localize(None)
    return df


def load_regime(profile: MarketProfile) -> pd.DataFrame | None:
    try:
        from nse_backtest.data import fetch_nse
        start = (date.today() - timedelta(days=800)).isoformat()
        # index symbols (^NSEI, ^GSPC) take no exchange suffix: '^NSEI.NS' does not exist on Yahoo
        exchange = "" if profile.regime_index.startswith("^") else profile.exchange
        df = fetch_nse(profile.regime_index, start, exchange=exchange)
        return df if df is not None and len(df) else None
    except Exception as e:  # noqa: BLE001 - regime unknown is handled fail-closed downstream
        log.warning("regime data unavailable for %s: %s", profile.regime_index, e)
        return None


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
    turnover = gap = None
    if symbol_df is not None:
        from .daily import market_stats
        turnover, gap = market_stats(symbol_df)
    return CheckContext(profile=profile, limits=limits, now=now, journal=tuple(journal),
                        governor_can_trade=can_trade, governor_reasons=reasons, ban=ban, regime=regime,
                        turnover_20d=turnover, today_gap_pct=gap, journal_readable=journal_readable)
