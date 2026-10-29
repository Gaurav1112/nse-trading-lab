"""Desk data models and enums."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
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


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"  # intraday ORB breakdown shorts (MIS), NSE F&O stocks only


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
    entry_limit: float | None = None  # intraday: the entry stop-limit's limit price (swing: None)
    # LAST field: LONG plans hash as before (verdict.input_hash leaves side out unless SHORT).
    side: Side = Side.LONG

    @property
    def is_short(self) -> bool:
        return self.side is Side.SHORT

    @property
    def risk_per_share(self) -> float:
        """Distance from the entry to the stop: entry - stop for a long, stop - entry for a short."""
        return self.stop - self.entry if self.is_short else self.entry - self.stop

    @property
    def rr_t1(self) -> float:
        reward = self.entry - self.target_1 if self.is_short else self.target_1 - self.entry
        return reward / self.risk_per_share


@dataclass(frozen=True)
class Sizing:
    qty: int
    binding_limit: str
    risk_amount: float
    notional: float
    costs: float
    size_halved: bool
    note: str
    spread_per_side: float = 0.00075  # the bid-ask spread per side the sizer costed (costs.DEFAULT_SPREAD by default)


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
    # Per-replay R of the Monte Carlo (capped at timemachine.MC_RS_CAP). Bulky and derived: kept out of the
    # input hash, the audit row and --json output (see summary_fields).
    mc_rs: tuple[float, ...] = field(default=(), metadata={"serialize": False})


def summary_fields(x) -> dict:
    """A dataclass's fields as a shallow dict, without those marked ``metadata={"serialize": False}``."""
    return {f.name: getattr(x, f.name) for f in fields(x) if f.metadata.get("serialize", True)}


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
    quote_live: bool | None = None  # True: current_price is a fresh live quote (<= 5 min old, same session)
    quote_as_of: str | None = None  # the quote's own time (ISO, market tz); None without a quote
    # True only for the daily plan's first pass (last close, no live quote, no earnings, no robustness, not audited):
    # see verdict.is_quick. Not part of the input hash or the audit row.
    first_pass: bool = False
    # The market regime value the checks ran in (TRENDING / MIXED / HOSTILE; None = unknown). Not part of the input
    # hash: the regime check already carries it.
    regime: str | None = None

    def blocks(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.BLOCK]

    def warns(self) -> list[Check]:
        return [c for c in self.checks if c.status is Status.WARN]
