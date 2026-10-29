"""Desk scoreboard core (pure: no network, no file I/O).

Replays every logged TAKE verdict against what prices actually did, using conservative fill and exit rules,
then summarises per (market, mode). It is the desk's paper record as if every TAKE were taken exactly per its
ticket, not the user's real P&L. Where intrabar order is unknown the worse outcome is chosen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time
from enum import Enum
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .costs import round_trip_cost
from .markets import get_profile
from .models import Mode
from .timemachine import _walk

MIN_TRADES = 30
DEFAULT_MAX_HOLD = {Mode.SWING: 15, Mode.INTRADAY: 26}


@dataclass(frozen=True)
class Candidate:
    created_at: datetime        # tz-aware
    market: str
    symbol: str
    mode: Mode
    entry: float
    stop: float
    target_1: float
    max_hold_bars: int
    cost_per_share: float
    qty: int
    mc_expectancy_r: float | None
    history_expectancy_r: float | None
    key: str                    # f"{input_hash}|{created_at.isoformat()}"


class Status(str, Enum):
    WON = "won"
    LOST = "lost"
    TIME = "time"
    NOT_FILLED = "not_filled"
    OPEN = "open"
    UNAVAILABLE = "data_unavailable"


@dataclass(frozen=True)
class Outcome:
    candidate: Candidate
    status: Status
    fill_price: float | None
    fill_at: str | None
    exit_price: float | None
    exit_at: str | None
    realised_r: float | None    # None unless WON/LOST/TIME; OPEN carries mark-to-market in mtm_r
    mtm_r: float | None
    bars_held: int
    note: str


@dataclass(frozen=True)
class ScoreStats:
    market: str
    mode: Mode
    n: int
    wins: int
    win_pct: float | None       # percent, 0-100; a win is realised R > 0 (net of costs)
    mean_r: float | None
    mean_pred_mc_r: float | None
    mean_pred_hist_r: float | None
    mean_gap_r: float | None    # mean(realised - predicted MC) over trades that have a prediction
    ci_low: float | None
    ci_high: float | None
    max_dd_r: float | None      # largest peak-to-trough fall of cumulative R (>= 0), series starting at 0
    cumulative_r: tuple[float, ...]
    not_filled: int
    open: int
    unavailable: int
    verdict: str


# ---------------------------------------------------------------- candidates

def _num(row: dict, key: str) -> float:
    if key not in row or row[key] is None:
        raise ValueError(f"missing {key}")
    v = float(row[key])
    if not math.isfinite(v):
        raise ValueError(f"{key} is not finite")
    return v


def _opt(row: dict, key: str) -> float | None:
    v = row.get(key)
    if v is None:
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _parse_row(row: dict) -> Candidate:
    for k in ("created_at", "market", "symbol", "mode", "input_hash"):
        if not row.get(k):
            raise ValueError(f"missing {k}")
    created = datetime.fromisoformat(str(row["created_at"]))
    if created.tzinfo is None:
        raise ValueError("created_at has no timezone offset")
    profile = get_profile(str(row["market"]))
    mode = Mode(row["mode"])
    entry, stop, t1 = _num(row, "entry"), _num(row, "stop"), _num(row, "target_1")
    if not (0 < stop < entry < t1):
        raise ValueError(f"invalid geometry stop={stop} entry={entry} target_1={t1}")
    qty = int(row.get("qty") or 0)
    hold = row.get("max_hold_bars")
    hold = int(hold) if hold is not None else DEFAULT_MAX_HOLD[mode]
    if hold < 1:
        raise ValueError("max_hold_bars < 1")
    cps = _opt(row, "cost_per_share")
    if cps is None:
        q = max(qty, 1)
        cps = round_trip_cost(profile, mode, entry, t1, q) / q
    return Candidate(created, profile.code, str(row["symbol"]), mode, entry, stop, t1, hold, float(cps), qty,
                     _opt(row, "mc_expectancy_r"), _opt(row, "history_expectancy_r"),
                     f"{row['input_hash']}|{created.isoformat()}")


def load_candidates(rows: Iterable[dict]) -> tuple[list[Candidate], list[tuple[dict, str]]]:
    """TAKE rows only (others are silently not candidates). Malformed TAKE rows come back with a reason.
    Deduped by (market, symbol, mode, market-tz trading date of created_at), keeping the earliest."""
    good: list[Candidate] = []
    rejects: list[tuple[dict, str]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("action") != "TAKE":
            continue
        try:
            good.append(_parse_row(row))
        except (ValueError, TypeError, KeyError) as e:
            rejects.append((row, str(e)))
    best: dict[tuple, Candidate] = {}
    for c in sorted(good, key=lambda c: c.created_at):
        d = c.created_at.astimezone(ZoneInfo(get_profile(c.market).tz)).date()
        best.setdefault((c.market, c.symbol, c.mode, d), c)
    return sorted(best.values(), key=lambda c: c.created_at), rejects


# ---------------------------------------------------------------- resolution

def _outcome(c: Candidate, status: Status, note: str, *, fill=None, fill_at=None, exit_px=None, exit_at=None,
             r=None, mtm=None, held=0) -> Outcome:
    return Outcome(c, status, fill, fill_at, exit_px, exit_at, r, mtm, held, note)


def _r(c: Candidate, fill: float, px: float) -> float:
    return float((px - fill - c.cost_per_share) / (c.entry - c.stop))


def _mtm(c: Candidate, fill: float, px: float) -> float:
    return _r(c, fill, px)


def _finish(c: Candidate, fill: float, fill_at, px: float, exit_at, held: int, note: str) -> Outcome:
    if px >= c.target_1:
        st = Status.WON
    elif px <= c.stop:
        st = Status.LOST
    else:
        st = Status.TIME
    return _outcome(c, st, note, fill=float(fill), fill_at=str(fill_at), exit_px=float(px), exit_at=str(exit_at),
                    r=_r(c, fill, px), held=held)


def resolve_swing(c: Candidate, daily: pd.DataFrame, *, fill_session: date, now: datetime) -> Outcome:
    """CNC LIMIT at entry for one session. Fill bar: filled iff Low <= entry at min(Open, entry); only the stop
    can trigger on it. Then up to max_hold_bars further bars; no exit -> time exit at the last bar's Close."""
    tz = ZoneInfo(get_profile(c.market).tz)
    today = now.astimezone(tz).date()
    dates = daily.index.normalize()
    hits = np.flatnonzero(dates == pd.Timestamp(fill_session))
    if len(hits) == 0:
        if fill_session >= today:
            return _outcome(c, Status.OPEN, "waiting for the fill session")
        return _outcome(c, Status.UNAVAILABLE, "no bar for fill session")
    i = int(hits[0])
    op, hi, lo, cl = (daily[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    if not lo[i] <= c.entry:
        return _outcome(c, Status.NOT_FILLED, "limit not reached on the fill session")
    fill = min(op[i], c.entry)
    fill_at = daily.index[i]
    if lo[i] <= c.stop:  # target on the fill bar is ignored (intrabar order unknown)
        return _finish(c, fill, fill_at, min(op[i], c.stop), fill_at, 0, "stop touched on the fill bar")
    last = min(i + c.max_hold_bars, len(daily) - 1)
    res = _walk(op, hi, lo, cl, i + 1, last, c.stop, c.target_1) if last > i else None
    if res is not None:
        px, j = res
        return _finish(c, fill, fill_at, px, daily.index[j], j - i, "")
    if i + c.max_hold_bars <= len(daily) - 1:
        j = i + c.max_hold_bars
        return _finish(c, fill, fill_at, cl[j], daily.index[j], c.max_hold_bars, "time exit")
    return _outcome(c, Status.OPEN, "holding", fill=float(fill), fill_at=str(fill_at),
                    mtm=_mtm(c, fill, cl[last]), held=last - i)


def resolve_intraday(c: Candidate, bars15: pd.DataFrame, *, flat_by: time, now: datetime) -> Outcome:
    """MIS LIMIT at entry over created_at's session, bars strictly after created_at and before flat_by."""
    tz = ZoneInfo(get_profile(c.market).tz)
    created = c.created_at.astimezone(tz).replace(tzinfo=None)
    now_l = now.astimezone(tz).replace(tzinfo=None)
    day = created.date()
    session_over = now_l.date() > day or (now_l.date() == day and now_l.time() >= flat_by)
    idx = bars15.index
    mask = (idx.normalize() == pd.Timestamp(day)) & (idx > pd.Timestamp(created)) & \
           (np.array([t.time() < flat_by for t in idx], dtype=bool) if len(idx) else True)
    sess = bars15[mask]
    if sess.empty:
        if not session_over:
            return _outcome(c, Status.OPEN, "session still running")
        return _outcome(c, Status.UNAVAILABLE, "no intraday bars for the session")
    op, hi, lo, cl = (sess[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    k = next((p for p in range(len(sess)) if hi[p] >= c.entry), None)
    if k is None:
        if not session_over:
            return _outcome(c, Status.OPEN, "limit not reached yet")
        return _outcome(c, Status.NOT_FILLED, "limit not reached before flat-by")
    fill = max(op[k], c.entry)
    fill_at = sess.index[k]
    if lo[k] <= c.stop:
        return _finish(c, fill, fill_at, c.stop, fill_at, 0, "stop touched on the fill bar")
    n = len(sess)
    res = _walk(op, hi, lo, cl, k + 1, n - 1, c.stop, c.target_1) if k + 1 <= n - 1 else None
    if res is not None:
        px, j = res
        return _finish(c, fill, fill_at, px, sess.index[j], j - k, "")
    if session_over:
        return _finish(c, fill, fill_at, cl[n - 1], sess.index[n - 1], n - 1 - k, "time exit at flat-by")
    return _outcome(c, Status.OPEN, "holding", fill=float(fill), fill_at=str(fill_at),
                    mtm=_mtm(c, fill, cl[n - 1]), held=n - 1 - k)


# ---------------------------------------------------------------- statistics

def _verdict(n: int, mean_pred_mc: float | None, ci_low: float, ci_high: float, mean_r: float) -> str:
    eps = 1e-9
    if n < MIN_TRADES:
        return f"Not enough trades yet ({n}/{MIN_TRADES}) — keep paper-trading"
    if ci_high < 0:
        return "Losing — do not trade this with real money"
    if mean_pred_mc is not None:
        if ci_high < mean_pred_mc - eps:
            return "Worse than the desk's odds — do not size up"
        if ci_low > 0 and mean_r >= mean_pred_mc - 0.15:
            return "Matching the odds — real money at small size (≤0.5% risk)"
    return "Inconclusive — keep paper-trading"


def _mean(xs: list[float]) -> float | None:
    return float(np.mean(xs)) if xs else None


def _stats(market: str, mode: Mode, outs: list[Outcome], seed: int, resamples: int) -> ScoreStats:
    resolved = sorted((o for o in outs if o.status in (Status.WON, Status.LOST, Status.TIME)
                       and o.realised_r is not None), key=lambda o: o.candidate.created_at)
    rs = np.array([o.realised_r for o in resolved], dtype=float)
    n = len(rs)
    count = lambda st: sum(1 for o in outs if o.status is st)  # noqa: E731
    mc = [o.candidate.mc_expectancy_r for o in resolved if o.candidate.mc_expectancy_r is not None]
    hist = [o.candidate.history_expectancy_r for o in resolved if o.candidate.history_expectancy_r is not None]
    gaps = [o.realised_r - o.candidate.mc_expectancy_r for o in resolved if o.candidate.mc_expectancy_r is not None]
    mean_pred_mc = _mean(mc)
    if n == 0:
        return ScoreStats(market, mode, 0, 0, None, None, mean_pred_mc, _mean(hist), _mean(gaps), None, None,
                          None, (), count(Status.NOT_FILLED), count(Status.OPEN), count(Status.UNAVAILABLE),
                          _verdict(0, mean_pred_mc, 0.0, 0.0, 0.0))
    rng = np.random.default_rng(seed)
    means = rng.choice(rs, size=(resamples, n), replace=True).mean(axis=1)
    ci_low, ci_high = (float(x) for x in np.percentile(means, [2.5, 97.5]))
    cum = np.cumsum(rs)
    with_start = np.concatenate(([0.0], cum))
    dd = float(np.max(np.maximum.accumulate(with_start) - with_start))
    wins = int((rs > 0).sum())
    mean_r = float(rs.mean())
    return ScoreStats(market, mode, n, wins, 100.0 * wins / n, mean_r, mean_pred_mc, _mean(hist), _mean(gaps),
                      ci_low, ci_high, dd, tuple(float(x) for x in cum), count(Status.NOT_FILLED),
                      count(Status.OPEN), count(Status.UNAVAILABLE),
                      _verdict(n, mean_pred_mc, ci_low, ci_high, mean_r))


def summarize(outcomes: Iterable[Outcome], *, seed: int = 19, resamples: int = 2000) -> list[ScoreStats]:
    groups: dict[tuple[str, Mode], list[Outcome]] = {}
    for o in outcomes:
        groups.setdefault((o.candidate.market, o.candidate.mode), []).append(o)
    return [_stats(m, md, groups[(m, md)], seed, resamples)
            for m, md in sorted(groups, key=lambda k: (k[0], k[1].value))]
