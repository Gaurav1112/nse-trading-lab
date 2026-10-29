"""Desk data models and enums."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Status(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


class Action(str, Enum):
    TAKE = "TAKE"
    WAIT = "WAIT"
    SKIP = "SKIP"


class Mode(str, Enum):
    SWING = "swing"
    INTRADAY = "intraday"


@dataclass(frozen=True)
class Limits:
    capital: float
    risk_per_trade_pct: float = 1.0
    max_position_pct: float = 25.0
    cash_buffer_pct: float = 10.0
    daily_loss_limit_pct: float = 2.0
    min_rr: float = 1.5
    pdt_guard: bool = False


@dataclass(frozen=True)
class TradePlan:
    symbol: str
    market: str
    mode: Mode
    entry: float
    stop: float
    target_1: float
    target_2: float
    atr: float
    max_hold_bars: int
    last_close: float
    last_bar: str  # ISO timestamp of last bar used
    stop_basis: str  # "2×ATR" | "10-bar swing low" | "1×ATR floor" | "user" | "opening-range mid"

    @property
    def risk_per_share(self) -> float:
        return self.entry - self.stop

    @property
    def rr_t1(self) -> float:
        return (self.target_1 - self.entry) / self.risk_per_share


@dataclass(frozen=True)
class Sizing:
    qty: int
    binding_limit: str
    risk_amount: float
    notional: float
    costs: float
    size_halved: bool
    note: str


@dataclass(frozen=True)
class Odds:
    replays: int
    seed: int
    p_target: float
    p_stop: float
    p_time: float
    mc_expectancy_r: float
    history_n: int
    history_win_pct: float | None
    history_expectancy_r: float | None
    history_rs: tuple[float, ...]
    dd_p5_r: float | None
    dd_p50_r: float | None
    dd_p95_r: float | None
    p_5_loss_streak: float | None
    noise_expectancy_min: float | None
    noise_expectancy_max: float | None
    noise_negative_frac: float | None


@dataclass(frozen=True)
class LiveQuote:
    """Latest traded price from 1-minute bars. `as_of` is tz-aware (market tz)."""
    price: float
    day_open: float
    as_of: datetime


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    reason: str


@dataclass(frozen=True)
class DeskVerdict:
    action: Action
    headline: str
    plan: TradePlan
    sizing: Sizing
    odds: Odds | None
    checks: tuple[Check, ...]
    ticket: tuple[str, ...]
    trigger: float | None
    current_price: float
    input_hash: str
    created_at: str
    cost_per_share: float | None = None  # per-share round-trip cost the odds used (None: no odds computed)

    def blocks(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.BLOCK]

    def warns(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.WARN]
