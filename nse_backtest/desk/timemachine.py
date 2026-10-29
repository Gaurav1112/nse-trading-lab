"""Time machine: the honest odds of one exact trade.

Adapted from github.com/Jakeschincariol/day-trader-skill (MIT). Robustness ideas (trade shuffle, candle noise) from Jesse.

Three independent views: a block-bootstrap Monte Carlo from the stock's own
bars, a replay of every past time the same setup fired, and robustness tests
(trade-shuffle drawdowns, candle-noise). Deterministic under ``seed``.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from nse_backtest.desk.costs import DEFAULT_SPREAD, side_cost
from nse_backtest.desk.fills import entry_limit, entry_limit_short, stop_limit_fill, stop_limit_fill_short
from nse_backtest.desk.markets import MarketProfile, get_profile
from nse_backtest.desk.models import Mode, Odds, Side, TradePlan
from nse_backtest.desk.plan import atr_series, short_flat_by, swing_stop

_INTRADAY_LOOKBACK_BARS = 40 * 25  # ~40 sessions of 15-minute bars (25 per NSE session)
MC_RS_CAP = 10_000  # most per-replay R values kept on Odds.mc_rs


def bar_ratios(df: pd.DataFrame, mode: Mode, profile=None) -> np.ndarray:
    """Rows of (O, H, L, C) / previous close, shape (n, 4); non-finite rows dropped.

    INTRADAY also drops each session's first bar (it contains the overnight gap) and,
    when ``profile`` is given, every bar at/after ``profile.flat_by`` (the desk never holds there).
    """
    prev = df["Close"].shift(1)
    ratios = pd.concat([df[c] / prev for c in ("Open", "High", "Low", "Close")], axis=1)
    keep = prev.notna().to_numpy().copy()  # first row has no previous close
    if mode is Mode.INTRADAY:
        dates = pd.Series(df.index.normalize(), index=df.index)
        keep &= (dates == dates.shift(1)).to_numpy()  # same date as previous bar
        if profile is not None:
            keep &= np.array([t.time() < profile.flat_by for t in df.index], dtype=bool)
    arr = ratios.to_numpy(dtype=float)
    keep &= np.isfinite(arr).all(axis=1)
    return arr[keep]


def monte_carlo(ratios, entry, stop, target, max_bars, *, cost_per_share=0.0,
                replays=10_000, seed=19, block=5) -> tuple[float, float, float, float]:
    """Block-bootstrap the stock's own bar ratios; returns (p_target, p_stop, p_time, expectancy_r)."""
    return monte_carlo_rs(ratios, entry, stop, target, max_bars, cost_per_share=cost_per_share,
                          replays=replays, seed=seed, block=block)[:4]


def monte_carlo_rs(ratios, entry, stop, target, max_bars, *, cost_per_share=0.0, replays=10_000, seed=19,
                   block=5) -> tuple[float, float, float, float, np.ndarray]:
    """``monte_carlo`` plus the per-replay R array (net of ``cost_per_share``), shape (replays,)."""
    if not all(math.isfinite(x) for x in (entry, stop, target, cost_per_share)):
        raise ValueError("entry, stop, target and cost_per_share must be finite")
    if not entry > stop:
        raise ValueError("entry must be above stop")
    if not target > entry:
        raise ValueError("target must be above entry")
    if max_bars < 1:
        raise ValueError("max_bars must be >= 1")
    ratios = np.asarray(ratios, dtype=float)
    if not np.isfinite(ratios).all():
        raise ValueError("ratios must be finite")
    n = len(ratios)
    if n < 2 * block:
        raise ValueError("not enough history for Monte Carlo")
    rng = np.random.default_rng(seed)
    nblocks = -(-max_bars // block)
    starts = rng.integers(0, n - block + 1, size=(replays, nblocks))
    idx = (starts[:, :, None] + np.arange(block)).reshape(replays, -1)[:, :max_bars]
    r = ratios[idx]
    closes = entry * np.cumprod(r[:, :, 3], axis=1)
    prev = np.concatenate([np.full((replays, 1), entry), closes[:, :-1]], axis=1)
    o, h, l = prev * r[:, :, 0], prev * r[:, :, 1], prev * r[:, :, 2]
    gap_stop, gap_tgt = o <= stop, o >= target
    hit_stop, hit_tgt = l <= stop, h >= target
    event = gap_stop | gap_tgt | hit_stop | hit_tgt
    has = event.any(axis=1)
    first = event.argmax(axis=1)
    rows = np.arange(replays)
    exit_px = closes[:, -1].copy()
    outcome = np.zeros(replays, dtype=np.int8)
    gs, gt, hs = gap_stop[rows, first], gap_tgt[rows, first], hit_stop[rows, first]
    o_first = o[rows, first]
    sel = has & gs
    exit_px[sel] = o_first[sel]
    outcome[sel] = -1
    sel = has & ~gs & gt
    exit_px[sel] = o_first[sel]
    outcome[sel] = 1
    sel = has & ~gs & ~gt & hs
    exit_px[sel] = stop
    outcome[sel] = -1  # stop wins a bar that touches both
    sel = has & ~gs & ~gt & ~hs
    exit_px[sel] = target
    outcome[sel] = 1
    risk = entry - stop
    rs = (exit_px - entry - cost_per_share) / risk
    return (float((outcome == 1).mean()), float((outcome == -1).mean()),
            float((outcome == 0).mean()), float(rs.mean()), rs)


def monte_carlo_rs_short(ratios, entry, stop, target, max_bars, *, cost_per_share=0.0, replays=10_000, seed=19,
                         block=5) -> tuple[float, float, float, float, np.ndarray]:
    """The exact mirror of ``monte_carlo_rs`` for a sell: the stop is above the entry, the target below it. The same
    block bootstrap (same draws for the same seed); an Open at or above the stop (or at or below the target) exits
    at that Open; a bar whose High reaches the stop exits at the stop, one whose Low reaches the target at the target;
    the stop wins a bar that touches both. R = (entry - exit - cost_per_share) / (stop - entry)."""
    if not all(math.isfinite(x) for x in (entry, stop, target, cost_per_share)):
        raise ValueError("entry, stop, target and cost_per_share must be finite")
    if not stop > entry:
        raise ValueError("stop must be above entry")
    if not target < entry:
        raise ValueError("target must be below entry")
    if max_bars < 1:
        raise ValueError("max_bars must be >= 1")
    ratios = np.asarray(ratios, dtype=float)
    if not np.isfinite(ratios).all():
        raise ValueError("ratios must be finite")
    n = len(ratios)
    if n < 2 * block:
        raise ValueError("not enough history for Monte Carlo")
    rng = np.random.default_rng(seed)
    nblocks = -(-max_bars // block)
    starts = rng.integers(0, n - block + 1, size=(replays, nblocks))
    idx = (starts[:, :, None] + np.arange(block)).reshape(replays, -1)[:, :max_bars]
    r = ratios[idx]
    closes = entry * np.cumprod(r[:, :, 3], axis=1)
    prev = np.concatenate([np.full((replays, 1), entry), closes[:, :-1]], axis=1)
    o, h, l = prev * r[:, :, 0], prev * r[:, :, 1], prev * r[:, :, 2]
    gap_stop, gap_tgt = o >= stop, o <= target
    hit_stop, hit_tgt = h >= stop, l <= target
    event = gap_stop | gap_tgt | hit_stop | hit_tgt
    has = event.any(axis=1)
    first = event.argmax(axis=1)
    rows = np.arange(replays)
    exit_px = closes[:, -1].copy()
    outcome = np.zeros(replays, dtype=np.int8)
    gs, gt, hs = gap_stop[rows, first], gap_tgt[rows, first], hit_stop[rows, first]
    o_first = o[rows, first]
    sel = has & gs
    exit_px[sel] = o_first[sel]
    outcome[sel] = -1
    sel = has & ~gs & gt
    exit_px[sel] = o_first[sel]
    outcome[sel] = 1
    sel = has & ~gs & ~gt & hs
    exit_px[sel] = stop
    outcome[sel] = -1  # stop wins a bar that touches both
    sel = has & ~gs & ~gt & ~hs
    exit_px[sel] = target
    outcome[sel] = 1
    risk = stop - entry
    rs = (entry - exit_px - cost_per_share) / risk
    return (float((outcome == 1).mean()), float((outcome == -1).mean()),
            float((outcome == 0).mean()), float(rs.mean()), rs)


def _walk(op, hi, lo, cl, start, end, stop, target):
    """Walk bars start..end inclusive. Returns (exit_price, exit_index) or None if no exit in range."""
    for j in range(start, end + 1):
        if op[j] <= stop:
            return op[j], j
        if op[j] >= target:
            return op[j], j
        if lo[j] <= stop:
            return stop, j
        if hi[j] >= target:
            return target, j
    return None


def _swing_history(df, plan, cost_per_share, years):
    close = df["Close"]
    ema50 = close.ewm(span=50, adjust=False).mean().to_numpy()
    ema200 = close.ewm(span=200, adjust=False).mean().to_numpy()
    atrs = atr_series(df).to_numpy()
    low10 = df["Low"].rolling(10).min().to_numpy()
    op, hi, lo, cl = (df[c].to_numpy(dtype=float) for c in ("Open", "High", "Low", "Close"))
    n = len(df)
    rr, hold = plan.rr_t1, plan.max_hold_bars
    out: list[float] = []
    i = max(200, n - 252 * years)
    while i <= n - 2:
        if not (cl[i] > ema50[i] > ema200[i]):
            i += 1
            continue
        entry = float(cl[i])
        if not (math.isfinite(atrs[i]) and math.isfinite(low10[i])):
            i += 1
            continue
        stop, _ = swing_stop(entry, float(atrs[i]), float(low10[i]))
        risk = entry - stop
        # Degenerate-risk guard (applied symmetrically to swing and intraday): a stop
        # closer than 0.1 x ATR makes R meaningless and inflates R-multiples.
        if not risk >= 0.1 * float(atrs[i]) or not risk > 0:
            i += 1
            continue
        target = entry + rr * risk
        last = i + hold
        res = _walk(op, hi, lo, cl, i + 1, min(last, n - 1), stop, target)
        if res is not None:
            px, j = res
        elif last > n - 1:
            break  # window runs past the data: unresolved, discard and stop
        else:
            px, j = cl[last], last
        out.append(float((px - entry - cost_per_share * entry / plan.entry) / risk))
        i = j + 1
    return out


def _intraday_history(df, plan, cost_per_share):
    """Replay the desk's live rule. Opening range = the bar at session_open; stop = OR mid. The entry is the
    live ticket's buy stop-limit (trigger at the OR high, limit = fills.entry_limit), filled by
    fills.stop_limit_fill over the bars inside [no_entry_before, no_entry_after]: a breakout outside the window,
    or one that gaps above the limit and never trades back to it there, is no trade. Target = fill + rr_t1 x R,
    R measured from the actual fill. On the fill bar only the stop counts (its Low at or below the stop is a
    stop-out at the stop: the fill came after price traded up through the trigger, so the stop order did not
    exist at that bar's Open; we cannot know it reached target first); then later bars are walked.
    No later bars = time exit at the fill bar's Close."""
    prof = get_profile(plan.market)
    op, hi, lo, cl = (df[c].to_numpy(dtype=float) for c in ("Open", "High", "Low", "Close"))
    atrs = atr_series(df).to_numpy()
    dates = df.index.normalize()
    times = np.array([t.time() for t in df.index])
    latest = dates[-1]
    out: list[float] = []
    for d in pd.unique(dates):
        if d == latest:
            continue
        pos = np.flatnonzero(dates == d)
        pos = pos[times[pos] < prof.flat_by]
        opens = pos[times[pos] == prof.session_open]
        if len(opens) == 0:
            continue  # no opening-range bar this session
        a = opens[0]
        or_high, or_low = hi[a], lo[a]
        stop = (or_high + or_low) / 2
        window = [p for p in pos[pos > a] if prof.no_entry_before <= times[p] <= prof.no_entry_after]
        if not window or not or_high > stop:
            continue  # no entry window, or a zero-range opening bar (no stop-limit price exists)
        got = stop_limit_fill(op, hi, lo, window[0], window[-1], or_high, entry_limit(or_high, stop, prof.tick))
        if got is None:
            continue
        k, entry = got
        risk = entry - stop
        # Same degenerate-risk guard as swing: drops zero-range opening bars (R ~ 0 explodes R-multiples).
        if not risk > 0 or risk < 0.1 * atrs[k]:
            continue
        target = entry + plan.rr_t1 * risk
        rest = pos[pos > k]
        if lo[k] <= stop:
            px = stop  # stop wins on the fill bar itself, at the stop (the stop order postdates the bar's Open)
        elif len(rest) == 0:
            px = cl[k]  # accepted trade with nothing after: time exit at the fill bar's Close
        else:
            res = _walk(op, hi, lo, cl, rest[0], rest[-1], stop, target)
            px = res[0] if res is not None else cl[rest[-1]]
        out.append(float((px - entry - cost_per_share * entry / plan.entry) / risk))
    return out


# ---------------------------------------------------------------- ORB breakdown shorts (the mirror of the long replay)

SHORT_SLIPPAGE_MULT = 2.0  # the short history (per stock and pooled) is costed at twice the assumed slippage
_BAR = timedelta(minutes=15)


@dataclass(frozen=True)
class ShortTrade:
    """One replayed ORB short. ``outcome``: "won" (target, or an Open at/below it), "lost" (stop, or an Open at/above
    it) or "time" (squared off at the last bar's Close)."""
    day: date
    fill_index: int
    exit_index: int
    trigger: float  # the OR low
    entry_limit: float  # the sell stop-limit's limit (fills.entry_limit_short)
    entry: float  # the actual fill
    stop: float
    target: float
    exit_price: float
    outcome: str
    r: float


def short_cost_fn(profile: MarketProfile, slippage_mult: float = SHORT_SLIPPAGE_MULT
                  ) -> Callable[[float, float], float]:
    """Per-share round-trip cost of a short sold at ``entry`` and bought back at ``exit``: the desk's intraday costs
    with the short's sides (STT on the sell = the entry, stamp duty on the buy = the exit) and the per-side
    slippage (the spread) multiplied by ``slippage_mult``."""
    if not (math.isfinite(slippage_mult) and slippage_mult >= 0):
        raise ValueError("slippage_mult must be finite and >= 0")
    spread = DEFAULT_SPREAD * slippage_mult
    return lambda entry, exit_price: side_cost(profile, Mode.INTRADAY, entry, exit_price, 1, spread,
                                               side=Side.SHORT)


def _walk_short(op, hi, lo, start, end, stop, target):
    """The mirror of _walk for a short. (exit_price, exit_index, outcome) or None if no exit in range."""
    for j in range(start, end + 1):
        if op[j] >= stop:
            return op[j], j, "lost"
        if op[j] <= target:
            return op[j], j, "won"
        if hi[j] >= stop:
            return stop, j, "lost"
        if lo[j] <= target:
            return target, j, "won"
    return None


def _is_hostile(regime_by_day: Mapping | None, d: date) -> bool:
    r = (regime_by_day or {}).get(d)
    return isinstance(r, str) and r.strip().upper() == "HOSTILE"


def short_trades(df, profile: MarketProfile, regime_by_day: Mapping[date, str] | None, *, rr: float = 1.5,
                 cost_fn: Callable[[float, float], float] | None = None) -> list[ShortTrade]:
    """Replay the desk's live ORB-short rule on every past HOSTILE day of ``df`` (15-minute bars; the latest
    session is left out, as it may still be running). The exact mirror of the long replay (_intraday_history):
    opening range = the bar at session_open, stop = OR mid; the entry is the live ticket's sell stop-limit (trigger
    at the OR low, limit = fills.entry_limit_short) filled by fills.stop_limit_fill_short over the bars inside
    [no_entry_before, no_entry_after]. Target = fill - rr x R, R = stop - fill. On the fill bar only the stop counts
    (its High at or above the stop is a stop-out at the stop); then later bars are walked (_walk_short). Only bars
    that end by the short square-off (plan.short_flat_by, 15:10) are held; no exit by then = time exit at the last
    such bar's Close. A day is replayed only when ``regime_by_day`` says HOSTILE for it (no entry = not HOSTILE).
    ``cost_fn(entry, exit)`` is the per-share cost (default none)."""
    if not (math.isfinite(rr) and rr > 0):
        raise ValueError("rr must be positive")
    if len(df) == 0:
        return []
    op, hi, lo, cl = (df[c].to_numpy(dtype=float) for c in ("Open", "High", "Low", "Close"))
    atrs = atr_series(df).to_numpy()
    dates = df.index.normalize()
    times = np.array([t.time() for t in df.index])
    latest = dates[-1]
    out: list[ShortTrade] = []
    for d in pd.unique(dates):
        day = pd.Timestamp(d).date()
        if d == latest or not _is_hostile(regime_by_day, day):
            continue
        square = short_flat_by(profile, day)
        pos = np.flatnonzero(dates == d)
        pos = pos[[(datetime.combine(day, times[p]) + _BAR).time() <= square
                   and times[p] < square for p in pos]] if len(pos) else pos
        opens = pos[times[pos] == profile.session_open]
        if len(opens) == 0:
            continue  # no opening-range bar this session
        a = opens[0]
        or_high, or_low = hi[a], lo[a]
        stop = (or_high + or_low) / 2
        window = [p for p in pos[pos > a] if profile.no_entry_before <= times[p] <= profile.no_entry_after]
        if not window or not stop > or_low:
            continue  # no entry window, or a zero-range opening bar (no stop-limit price exists)
        limit = entry_limit_short(or_low, stop, profile.tick)
        got = stop_limit_fill_short(op, hi, lo, window[0], window[-1], or_low, limit)
        if got is None:
            continue
        k, entry = got
        risk = stop - entry
        # Same degenerate-risk guard as the long replay.
        if not risk > 0 or risk < 0.1 * atrs[k]:
            continue
        target = entry - rr * risk
        rest = pos[pos > k]
        if hi[k] >= stop:
            px, j, outcome = stop, k, "lost"  # the stop order postdates the fill bar's Open
        elif len(rest) == 0:
            px, j, outcome = cl[k], k, "time"
        else:
            res = _walk_short(op, hi, lo, rest[0], rest[-1], stop, target)
            px, j, outcome = res if res is not None else (cl[rest[-1]], int(rest[-1]), "time")
        cost = cost_fn(float(entry), float(px)) if cost_fn is not None else 0.0
        out.append(ShortTrade(day, int(k), int(j), float(or_low), float(limit), float(entry), float(stop),
                              float(target), float(px), outcome, float((entry - px - cost) / risk)))
    return out


def setup_history(df, plan: TradePlan, *, cost_per_share=0.0, years=3,
                  regime_by_day: Mapping[date, str] | None = None) -> list[float]:
    """R-multiples of every past time the same setup fired (non-overlapping). A short replays the ORB short on the
    stock's own HOSTILE days (``regime_by_day``; none given = no history) costed at SHORT_SLIPPAGE_MULT x slippage,
    the pooled gate's exact rule (``cost_per_share`` is not used for a short)."""
    if plan.side is Side.SHORT:
        prof = get_profile(plan.market)
        return [t.r for t in short_trades(df, prof, regime_by_day, rr=plan.rr_t1,
                                          cost_fn=short_cost_fn(prof, SHORT_SLIPPAGE_MULT))]
    if plan.mode is Mode.SWING:
        return _swing_history(df, plan, cost_per_share, years)
    return _intraday_history(df, plan, cost_per_share)


def shuffle_drawdowns(rs, *, runs=2000, seed=19) -> tuple[float, float, float, float] | None:
    """Shuffle the trade order: (dd_p5, dd_p50, dd_p95, p_5_loss_streak), in R; None if < 5 trades."""
    rs = np.asarray(rs, dtype=float)
    if len(rs) < 5:
        return None
    rng = np.random.default_rng(seed)
    perms = np.array([rng.permutation(rs) for _ in range(runs)])
    eq = np.concatenate([np.zeros((runs, 1)), np.cumsum(perms, axis=1)], axis=1)
    dds = np.maximum((np.maximum.accumulate(eq, axis=1) - eq).max(axis=1), 0)
    count = np.zeros(runs)
    longest = np.zeros(runs)
    for col in (perms < 0).T:
        count = (count + 1) * col
        longest = np.maximum(longest, count)
    p5, p50, p95 = (float(x) for x in np.percentile(dds, [5, 50, 95]))
    return p5, p50, p95, float((longest >= 5).mean())


def noise_expectancies(df, plan, *, cost_per_share=0.0, runs=20, seed=19, jitter=0.25,
                       regime_by_day: Mapping[date, str] | None = None) -> list[float]:
    """Re-run setup_history on candle-jittered copies; returns the mean R of each run that traded."""
    rng = np.random.default_rng(seed)
    spread = (df["High"] - df["Low"]).to_numpy(dtype=float)
    out: list[float] = []
    for _ in range(runs):
        shift = rng.uniform(-jitter, jitter, len(df)) * spread
        noisy = df.copy()
        for c in ("Open", "High", "Low", "Close"):
            noisy[c] = df[c].to_numpy(dtype=float) + shift
        kw = {"regime_by_day": regime_by_day} if plan.side is Side.SHORT else {}
        rs = setup_history(noisy, plan, cost_per_share=cost_per_share, **kw)
        if rs:
            out.append(float(np.mean(rs)))
    return out


def odds(df, plan: TradePlan, *, cost_per_share=0.0, replays=10_000, seed=19, block=5,
         lookback=250, robustness=True, regime_by_day: Mapping[date, str] | None = None) -> Odds:
    """The Time Machine's odds. A short (intraday only) runs monte_carlo_rs_short on the same bootstrap and the
    ORB-short history on the stock's own HOSTILE days (``regime_by_day``); a long ignores ``regime_by_day``."""
    short = plan.side is Side.SHORT
    if short and plan.mode is not Mode.INTRADAY:
        raise ValueError("shorts are intraday only")
    window = df.tail(lookback + 1) if plan.mode is Mode.SWING else df.tail(_INTRADAY_LOOKBACK_BARS)
    ratios = bar_ratios(window, plan.mode, profile=get_profile(plan.market))
    mc = monte_carlo_rs_short if short else monte_carlo_rs
    p_t, p_s, p_x, mc_exp, mc_rs = mc(ratios, plan.entry, plan.stop, plan.target_1,
                                      plan.max_hold_bars, cost_per_share=cost_per_share,
                                      replays=replays, seed=seed, block=block)
    kw = {"regime_by_day": regime_by_day} if short else {}
    rs = setup_history(df, plan, cost_per_share=cost_per_share, **kw)
    dd = (None, None, None, None)
    nmin = nmax = nneg = None
    if robustness:
        dd = shuffle_drawdowns(rs, seed=seed) or dd
        noise = noise_expectancies(df, plan, cost_per_share=cost_per_share, seed=seed, **kw)
        if noise:
            nmin, nmax = min(noise), max(noise)
            nneg = float(np.mean([x < 0 for x in noise]))
    return Odds(
        replays=replays, seed=seed, p_target=p_t, p_stop=p_s, p_time=p_x, mc_expectancy_r=mc_exp,
        history_n=len(rs),
        history_win_pct=100.0 * float(np.mean([r > 0 for r in rs])) if rs else None,
        history_expectancy_r=float(np.mean(rs)) if rs else None,
        history_rs=tuple(float(r) for r in rs),
        dd_p5_r=dd[0], dd_p50_r=dd[1], dd_p95_r=dd[2], p_5_loss_streak=dd[3],
        noise_expectancy_min=nmin, noise_expectancy_max=nmax, noise_negative_frac=nneg,
        mc_rs=tuple(mc_rs[:MC_RS_CAP].tolist()))
