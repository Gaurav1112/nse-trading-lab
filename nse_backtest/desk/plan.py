"""Trade plan geometry: ATR, swing stop, opening-range plan."""
from __future__ import annotations

import math
from dataclasses import replace
from datetime import datetime, timedelta

import pandas as pd

from nse_backtest.desk import fills
from nse_backtest.desk.calendars import flat_by_for
from nse_backtest.desk.markets import MarketProfile
from nse_backtest.desk.models import Mode, TradePlan

_OHLC = ("Open", "High", "Low", "Close")
_BAR = timedelta(minutes=15)


def _validate(df: pd.DataFrame) -> None:
    missing = [c for c in _OHLC if c not in df.columns]
    if missing:
        raise ValueError(f"missing columns: {', '.join(missing)}")
    if len(df) == 0:
        raise ValueError("empty frame")
    if df.index.has_duplicates or not df.index.is_monotonic_increasing:
        raise ValueError("bars are not in time order (duplicate or unsorted timestamps)")


def _require_finite(**values: float) -> None:
    for name, value in values.items():
        if not math.isfinite(value):
            raise ValueError(f"non-finite price data: {name}={value}")


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()],
                   axis=1).max(axis=1)  # first bar: prev is NaN, skipped -> H-L
    return tr


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Rolling simple mean of true range; early bars use all available bars."""
    _validate(df)
    return _true_range(df).rolling(period, min_periods=1).mean()


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Simple mean of the last `period` true ranges (all bars if fewer)."""
    return float(atr_series(df, period).iloc[-1])


def swing_stop(entry: float, atr_value: float, lowest_low_10: float) -> tuple[float, str]:
    cand_atr = entry - 2 * atr_value
    cand_low = lowest_low_10 * 0.999
    if cand_atr >= cand_low:
        stop, basis = cand_atr, "2×ATR"
    else:
        stop, basis = cand_low, "10-bar swing low"
    if entry - stop < atr_value:
        stop, basis = entry - atr_value, "1×ATR floor"
    return stop, basis


def build_plan(df: pd.DataFrame, symbol: str, mode: Mode, profile: MarketProfile, *,
               entry: float | None = None, stop: float | None = None,
               target: float | None = None, max_hold_bars: int = 15) -> TradePlan:
    _validate(df)
    a = atr(df)
    last_ts = df.index[-1]
    if mode is Mode.SWING:
        if len(df) < 20:
            raise ValueError("need at least 20 bars")
        e = entry if entry is not None else float(df["Close"].iloc[-1])
        if stop is not None:
            s, basis = stop, "user"
        else:
            s, basis = swing_stop(e, a, float(df["Low"].tail(10).min()))
        r_mult2, hold = 3.0, max_hold_bars
        r1 = 1.5
    else:
        session = df[df.index.normalize() == last_ts.normalize()]
        if len(session) < 1:
            raise ValueError("no session rows")
        orb = session.iloc[0]
        e = entry if entry is not None else float(orb["High"])
        if stop is not None:
            s, basis = stop, "user"
        else:
            s, basis = (float(orb["High"]) + float(orb["Low"])) / 2, "opening-range mid"
        r_mult2, r1 = 3.0, 1.5  # T1 at 1.5R so an auto plan meets the default min_rr
        flat_dt = datetime.combine(last_ts.date(), flat_by_for(profile, last_ts.date()))
        hold = max(1, int((flat_dt - last_ts.to_pydatetime().replace(tzinfo=None)) / _BAR))
    r = e - s
    t1 = target if target is not None else e + r1 * r
    last_close = float(df["Close"].iloc[-1])
    _require_finite(entry=e, stop=s, target_1=t1, atr=a, last_close=last_close)
    if s >= e:
        raise ValueError("stop must be below entry")
    if t1 <= e:
        raise ValueError("target must be above entry")
    t2 = max(t1, e + r_mult2 * r)
    plan = TradePlan(symbol=symbol, market=profile.code, mode=mode, entry=float(e), stop=float(s),
                     target_1=float(t1), target_2=float(t2), atr=a, max_hold_bars=hold,
                     last_close=last_close, last_bar=last_ts.isoformat(),
                     stop_basis=basis)
    if mode is Mode.INTRADAY:
        plan = replace(plan, entry_limit=fills.entry_limit(plan.entry, plan.stop, profile.tick))
    return plan
