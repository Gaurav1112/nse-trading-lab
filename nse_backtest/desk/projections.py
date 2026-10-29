"""Projections: ranges of the desk's own replays of past behaviour, not forecasts.

Pure and deterministic (``np.random.default_rng(seed)``, seed 19). Every function returns None when its inputs
are missing: no curve is ever made up.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .scoreboard import Status
from .tradecard import TradeCard

MIN_BAND_CLOSES = 30
_RESOLVED = (Status.WON, Status.LOST, Status.TIME)


@dataclass(frozen=True)
class OutcomeMoney:
    # money per Monte Carlo replay, net of costs; up to 10,000 values, so kept out of --json / text output
    pnl: tuple[float, ...] = field(metadata={"serialize": False})
    p5: float
    p25: float
    median: float
    p75: float
    p95: float
    p_target: float
    p_stop: float
    p_time: float
    # The replays assume a fill at the trigger. Intraday: a fill at the stop-limit's limit worsens each outcome by
    # up to qty x |limit - entry| (above the trigger for a long's buy, below it for a short's sell); None for swing
    # (a LIMIT buy never fills above entry).
    limit_slippage: float | None = None


def outcome_money(card: TradeCard) -> OutcomeMoney | None:
    """Money P&L per replay = R x qty x risk_per_share (costs are already inside R), assuming a fill at the trigger
    (``limit_slippage`` says how much worse a limit fill can be). None without shares, odds or replay R values."""
    odds = card.verdict.odds
    if card.qty == 0 or odds is None or not odds.mc_rs:
        return None
    pnl = np.asarray(odds.mc_rs, dtype=float) * card.qty * card.risk_per_share
    p5, p25, p50, p75, p95 = (float(x) for x in np.percentile(pnl, [5, 25, 50, 75, 95]))
    slip = None if card.entry_limit is None else card.qty * abs(card.entry_limit - card.entry)
    return OutcomeMoney(tuple(pnl.tolist()), p5, p25, p50, p75, p95, odds.p_target, odds.p_stop, odds.p_time, slip)


@dataclass(frozen=True)
class PriceBand:
    start: pd.Timestamp | None  # the last close's bar; step i is i+1 bars after it (None: caller sets it)
    steps: int
    p5: tuple[float, ...]
    p25: tuple[float, ...]
    p50: tuple[float, ...]
    p75: tuple[float, ...]
    p95: tuple[float, ...]


def _bootstrap(last: float, lr: np.ndarray, steps: int, paths: int, block: int, seed: int) -> PriceBand:
    rng = np.random.default_rng(seed)
    nblocks = -(-steps // block)
    starts = rng.integers(0, len(lr) - block + 1, size=(paths, nblocks))
    idx = (starts[:, :, None] + np.arange(block)).reshape(paths, -1)[:, :steps]
    prices = last * np.exp(np.cumsum(lr[idx], axis=1))
    bands = np.percentile(prices, [5, 25, 50, 75, 95], axis=0)
    return PriceBand(None, steps, *(tuple(b.tolist()) for b in bands))


def price_band_from_returns(last_close: float, log_returns, steps: int, *, paths: int = 2000, block: int = 5,
                            seed: int = 19) -> PriceBand | None:
    """Block bootstrap of the given log returns from ``last_close``, ``steps`` bars ahead (``start`` is None; the
    caller sets it). None for fewer than 30 returns, a non-finite return or a non-finite / non-positive close."""
    if steps < 1:
        raise ValueError("steps must be >= 1")
    lr = np.asarray(log_returns, dtype=float)
    if (len(lr) < MIN_BAND_CLOSES or not np.isfinite(lr).all()
            or not (math.isfinite(last_close) and last_close > 0)):
        return None
    return _bootstrap(float(last_close), lr, steps, paths, block, seed)


def price_band(closes: pd.Series, steps: int, *, paths: int = 2000, block: int = 5,
               seed: int = 19) -> PriceBand | None:
    """Block bootstrap of the stock's own close-to-close log returns from the last close, ``steps`` bars ahead.
    None for fewer than 30 closes or any non-finite / non-positive close."""
    if steps < 1:
        raise ValueError("steps must be >= 1")
    px = np.asarray(closes, dtype=float)
    if len(px) < MIN_BAND_CLOSES or not (np.isfinite(px).all() and (px > 0).all()):
        return None
    band = _bootstrap(float(px[-1]), np.diff(np.log(px)), steps, paths, block, seed)
    return replace(band, start=pd.Timestamp(closes.index[-1]))


@dataclass(frozen=True)
class AccountProjection:
    n_trades: int
    start_capital: float
    p5: tuple[float, ...]  # capital after trade i; len n_trades + 1, index 0 = start
    p50: tuple[float, ...]
    p95: tuple[float, ...]
    p_dd_10: float  # share of paths whose peak-to-trough fall reaches 10% of start capital
    p_finish_down: float  # share of paths ending below start capital
    source: str  # "Monte Carlo replays" | "setup history"


def account_projection(capital: float, risk_amount: float, rs, n_trades: int, *, paths: int = 2000,
                       seed: int = 19, source: str = "Monte Carlo replays") -> AccountProjection | None:
    """Each path draws ``n_trades`` R values with replacement from ``rs``; P&L per trade = R x risk_amount
    (fixed money risk, no compounding). None for empty ``rs`` or non-positive capital / risk."""
    if n_trades < 1:
        raise ValueError("n_trades must be >= 1")
    r = np.asarray(rs, dtype=float)
    if r.size == 0 or not (math.isfinite(capital) and capital > 0 and math.isfinite(risk_amount)
                           and risk_amount > 0):
        return None
    if not np.isfinite(r).all():
        raise ValueError("rs must be finite")
    rng = np.random.default_rng(seed)
    pnl = rng.choice(r, size=(paths, n_trades), replace=True) * risk_amount
    eq = capital + np.concatenate([np.zeros((paths, 1)), np.cumsum(pnl, axis=1)], axis=1)
    dd = (np.maximum.accumulate(eq, axis=1) - eq).max(axis=1)
    p5, p50, p95 = (tuple(b.tolist()) for b in np.percentile(eq, [5, 50, 95], axis=0))
    return AccountProjection(n_trades, float(capital), p5, p50, p95, float((dd >= 0.10 * capital).mean()),
                             float((eq[:, -1] < capital).mean()), source)


def projected_vs_real(outcomes) -> tuple[list[float], list[float], list[tuple[float, float]], int]:
    """Resolved trades in time order: (cumulative projected R over trades with an MC prediction, cumulative
    realised R over all of them, [(projected, realised)] per predicted trade, count without a prediction)."""
    done = sorted((o for o in outcomes if o.status in _RESOLVED and o.realised_r is not None),
                  key=lambda o: o.candidate.created_at)
    pairs = [(o.candidate.mc_expectancy_r, o.realised_r) for o in done if o.candidate.mc_expectancy_r is not None]
    projected = np.cumsum([p for p, _ in pairs]).tolist()
    realised = np.cumsum([o.realised_r for o in done]).tolist()
    return projected, realised, pairs, len(done) - len(pairs)


def resolved_without_r(outcomes) -> int:
    """Resolved trades (won, lost or time exit) without a realised R: projected_vs_real leaves them out, so callers
    show this count instead of dropping them silently."""
    return sum(1 for o in outcomes if o.status in _RESOLVED and o.realised_r is None)
