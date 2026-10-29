"""Desk scoreboard core (pure: no network, no file I/O).

Replays every logged TAKE verdict (record "desk") and every owner override of a WAIT/SKIP (record "override")
against what prices actually did, using conservative fill and exit rules, then summarises per (record, market,
mode): the two records are never pooled. It is a paper record as if every ticket were taken exactly as printed,
not the user's real P&L. Where intrabar order is unknown the worse outcome is chosen.

The bar containing created_at counts (its remainder came after the verdict), but a fill that only that bar made
possible is ambiguous (the touch may have come before the TAKE): it is kept when it loses or exits at <= 0R, its
target never counts, and when it would win the order is replayed from the later bars: a later fill counts only if
its R is at most the containing-bar fill's (a counted result is never better than either possible history), else
NOT_FILLED ("fill only possible on the bar the TAKE was logged in — counted only if it loses").
The logged price (current_price) is used only when it is TRUSTED: a live quote (quote_live True) inside the TAKE
bar's [Low, High + 0.5%]. Without a quote the desk logs the plan's last close, so an untrusted price (not live, or
unknown in old rows) is ignored entirely: the row is scored with its branch's no-price model (swing and stop-limit:
the ambiguous containing-bar rules; LIMIT tickets: the resting model). The branch itself still follows the ticket
that was printed. A live price outside that range contradicts the bars: data_unavailable.
The one accepted residual optimism: a trusted live price in [entry, limit] (desk TAKE or override) fills at that
price, although an order typed in later could fill up to the limit (at most about 0.25R worse).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import calendars as cal
from . import fills
from .costs import round_trip_cost
from .markets import get_profile
from .models import Mode
from .timemachine import _walk

SCOREBOARD_VERSION = 11  # bump when a resolution rule or the cache entry shape changes: other versions are ignored
MIN_TRADES = 30
MIN_PRED_COVERAGE = 0.8
MAX_UNAVAILABLE_SHARE = 0.10  # more unreplayable trades than this (of resolved + unavailable) -> Inconclusive
DEFAULT_MAX_HOLD = {Mode.SWING: 15, Mode.INTRADAY: 26}
RECORD_OF_ACTION = {"TAKE": "desk", "OVERRIDE": "override"}  # audit action -> scoreboard record
RECORD_LABELS = {"desk": "Desk picks", "override": "Your overrides"}


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
    key: str                    # f"{input_hash}|{created_at.isoformat()}", plus "|override" for an override
    last_bar: str | None = None     # the plan's last bar (ISO); None in rows logged before it was recorded
    last_close: float | None = None  # the plan's last daily Close; None in rows logged before R1
    entry_limit: float | None = None  # intraday: the buy stop-limit's limit price (None: computed from the tick)
    record: str = "desk"        # "desk" (a logged TAKE) or "override" (the owner chose a WAIT/SKIP anyway)
    current_price: float | None = None  # the price the verdict was decided at; None in rows logged before it
    quote_live: bool | None = None  # current_price was a live quote; False: the last close; None: unknown (old row)


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
    pred_coverage: float | None = None   # share of resolved trades carrying an MC prediction
    record: str = "desk"                 # which record these stats are for: "desk" or "override", never both


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
    record = RECORD_OF_ACTION.get(row.get("action"))
    if record is None:
        raise ValueError(f"not a scored action {row.get('action')!r}")
    if record == "override" and row.get("desk_action") not in ("WAIT", "SKIP"):
        raise ValueError(f"override of a TAKE verdict (desk_action {row.get('desk_action')!r})")
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
    if mode is Mode.INTRADAY:
        local = created.astimezone(ZoneInfo(profile.tz))
        if local.time() >= cal.flat_by_for(profile, local.date()):  # early-close days flatten earlier
            raise ValueError("created after flat-by")
    raw_cps = row.get("cost_per_share")
    if raw_cps is not None:
        cps = float(raw_cps)
        if not math.isfinite(cps) or cps < 0:
            raise ValueError(f"invalid cost_per_share {raw_cps}")
    else:
        q = max(qty, 1)
        cps = round_trip_cost(profile, mode, entry, t1, q) / q
        if not math.isfinite(cps) or cps < 0:
            raise ValueError("invalid computed cost_per_share")
    last_bar = row.get("last_bar")
    last_close = _opt(row, "last_close")
    limit = None
    if mode is Mode.INTRADAY:
        raw = row.get("entry_limit")  # None in rows logged before the stop-limit ticket
        if raw is not None:
            try:
                raw = float(raw)
            except (TypeError, ValueError):
                raise ValueError(f"invalid entry_limit {row['entry_limit']!r}") from None
            ceiling = fills.entry_limit(entry, stop, profile.tick) + profile.tick  # more = a corrupt value
            if not (math.isfinite(raw) and entry <= raw <= ceiling + 1e-9):
                raise ValueError(f"invalid entry_limit {row['entry_limit']!r}")
        limit = fills.limit_or_default(raw, entry, stop, profile.tick)
    return Candidate(created, profile.code, str(row["symbol"]), mode, entry, stop, t1, hold, float(cps), qty,
                     _opt(row, "mc_expectancy_r"), _opt(row, "history_expectancy_r"),
                     f"{row['input_hash']}|{created.isoformat()}" + ("|override" if record == "override" else ""),
                     str(last_bar) if last_bar not in (None, "") else None,
                     last_close if last_close is not None and last_close > 0 else None, limit, record,
                     cp if (cp := _opt(row, "current_price")) is not None and cp > 0 else None,
                     _quote_live(row))


def _quote_live(row: dict) -> bool | None:
    """The row's quote_live; rows logged before it: False when current_price equals the logged last close (the
    plan's fallback without a quote), else None (unknown). Only True makes the logged price trusted."""
    q = row.get("quote_live")
    if isinstance(q, bool):
        return q
    cp, lc = row.get("current_price"), row.get("last_close")
    if cp is not None and lc is not None and cp == lc:
        return False
    return None


def default_session_of(market: str, created_at: datetime) -> date:
    """Market-local calendar date of created_at (the session key used for dedupe unless the caller overrides)."""
    return created_at.astimezone(ZoneInfo(get_profile(market).tz)).date()


def load_candidates(rows: Iterable[dict], *, session_of: Callable[[str, datetime], date] | None = None
                    ) -> tuple[list[Candidate], list[tuple[dict, str]]]:
    """TAKE rows (record "desk") and OVERRIDE rows (record "override") only; others are silently not candidates.
    Malformed TAKE/OVERRIDE rows come back with a reason. Deduped by (record, market, symbol, mode, session the
    order belongs to), keeping the earliest, so a desk pick and an override never share a slot; the session
    defaults to the market-local date of created_at, or is session_of(market, created_at) when given."""
    session_of = session_of or default_session_of
    good: list[Candidate] = []
    rejects: list[tuple[dict, str]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("action") not in RECORD_OF_ACTION:
            continue
        try:
            good.append(_parse_row(row))
        except (ValueError, TypeError, KeyError, OverflowError) as e:
            rejects.append((row, str(e)))
    best: dict[tuple, Candidate] = {}
    for c in sorted(good, key=lambda c: c.created_at):
        d = session_of(c.market, c.created_at)
        best.setdefault((c.record, c.market, c.symbol, c.mode, d), c)
    return sorted(best.values(), key=lambda c: c.created_at), rejects


# ---------------------------------------------------------------- resolution

def _outcome(c: Candidate, status: Status, note: str, *, fill=None, fill_at=None, exit_px=None, exit_at=None,
             r=None, mtm=None, held=0) -> Outcome:
    return Outcome(c, status, fill, fill_at, exit_px, exit_at, r, mtm, held, note)


def _r(c: Candidate, fill: float, px: float) -> float:
    return float((px - fill - c.cost_per_share) / (c.entry - c.stop))


def _mtm(c: Candidate, fill: float, px: float) -> float:
    return _r(c, fill, px)


def unavailable(c: Candidate, note: str) -> Outcome:
    """An UNAVAILABLE outcome for a candidate whose bars could not be loaded (never dropped)."""
    return _outcome(c, Status.UNAVAILABLE, note)


def realised_r(c: Candidate, fill: float, px: float) -> float:
    """Realised R of an exit at px after a fill, net of cost_per_share, over the planned R."""
    return _r(c, fill, px)


def exit_status(c: Candidate, px: float) -> Status:
    """Final status of a filled trade exiting at px: WON at/through target, LOST at/through stop, else TIME."""
    if px >= c.target_1:
        return Status.WON
    if px <= c.stop:
        return Status.LOST
    return Status.TIME


def _finish(c: Candidate, fill: float, fill_at, px: float, exit_at, held: int, note: str) -> Outcome:
    st = exit_status(c, px)
    return _outcome(c, st, note, fill=float(fill), fill_at=str(fill_at), exit_px=float(px), exit_at=str(exit_at),
                    r=_r(c, fill, px), held=held)


CERTAIN_FILL_NOTE = "filled on the bar the TAKE was logged in (it traded inside the trigger-limit range)"
AMBIGUOUS_FILL_NOTE = "fill only possible on the bar the TAKE was logged in — counted only if it loses"


def _only_if_it_loses(c: Candidate, o: Outcome) -> Outcome:
    """A fill that only the bar containing created_at made possible (the touch may have come before the TAKE) is
    kept when it loses or exits at <= 0R, and is NOT_FILLED when it would win or exit positive."""
    if o.status in (Status.WON, Status.LOST, Status.TIME) and o.realised_r is not None and o.realised_r > 1e-12:
        return _outcome(c, Status.NOT_FILLED, AMBIGUOUS_FILL_NOTE)
    return o


def _cap_fallback(c: Candidate, cont: Outcome, later: Outcome) -> Outcome:
    """The ambiguous containing-bar fill ``cont`` would win, so the order is replayed from the later bars
    (``later``). A counted result is never better than either history: a later final outcome counts only when its
    R is at most cont's; a better one, or no later fill, is NOT_FILLED. OPEN / UNAVAILABLE pass through (they are
    not counted; the cap applies again once they resolve)."""
    if later.status in (Status.WON, Status.LOST, Status.TIME) and later.realised_r is not None:
        if cont.realised_r is not None and later.realised_r <= cont.realised_r + 1e-12:
            return later
        return _outcome(c, Status.NOT_FILLED, AMBIGUOUS_FILL_NOTE)
    if later.status is Status.NOT_FILLED:
        return _outcome(c, Status.NOT_FILLED, AMBIGUOUS_FILL_NOTE)
    return later


def _all_finite(*arrs) -> bool:
    return all(bool(np.isfinite(a).all()) for a in arrs)


NO_CALENDAR = "(holiday calendar not available)"
SCALE_TOLERANCE = 0.005      # logged last close vs the same bar now: more than 0.5% apart -> re-scaled prices
SCALE_ENTRY_BAND = 0.20      # rows without last_close: entry more than 20% from that close -> re-scaled prices


def check_price_scale(c: Candidate, daily: pd.DataFrame) -> Outcome | None:
    """UNAVAILABLE when today's (unadjusted) bars are not on the ticket's price scale, else None.

    yfinance's unadjusted bars are still split-adjusted, so a split or bonus after the ticket re-scales the
    whole history; replaying the ticket's entry/stop/target on such bars would be a fiction. The plan's last
    daily Close (logged as last_close) must match the loaded Close on last_bar within 0.5%. Rows logged before
    last_close existed fall back to: entry within 20% of that Close. Without last_bar nothing can be checked."""
    money = get_profile(c.market).money
    if not c.last_bar:
        return unavailable(c, "audit row has no last_bar, so the price scale cannot be checked")
    try:
        day = pd.Timestamp(c.last_bar)
        if pd.isna(day):
            raise ValueError("not a time")
    except (ValueError, TypeError, OverflowError):
        return unavailable(c, f"unreadable last_bar {c.last_bar!r}, so the price scale cannot be checked")
    hits = np.flatnonzero(daily.index.normalize() == pd.Timestamp(day.date()))
    if len(hits) == 0:
        return unavailable(c, f"no daily bar for the ticket's last bar {day.date().isoformat()}")
    now_close = float(daily["Close"].to_numpy(dtype=float)[hits[-1]])
    if not math.isfinite(now_close) or now_close <= 0:
        return unavailable(c, "bad price data")
    if c.last_close is not None:
        if abs(now_close / c.last_close - 1) > SCALE_TOLERANCE:
            return unavailable(c, f"prices re-scaled since the ticket (split/bonus?): logged close "
                                  f"{money(c.last_close)}, now {money(now_close)}")
    elif abs(c.entry / now_close - 1) > SCALE_ENTRY_BAND:
        return unavailable(c, f"prices re-scaled since the ticket (split/bonus?): logged entry {money(c.entry)}, "
                              f"close on the ticket's last bar now {money(now_close)}")
    return None


def check_intraday_price_scale(c: Candidate, bars15: pd.DataFrame) -> Outcome | None:
    """check_price_scale for an intraday ticket: yfinance split-adjusts 15-minute bars even unadjusted.

    The plan's last 15-minute bar (last_bar) may still have been forming when the ticket was printed, so its
    logged close (last_close) is any price the bar traded at: it must lie inside that bar's final Low..High,
    widened by 0.5%. Rows without last_close: entry within 20% of that bar's Close. No last_bar, or no bar at
    it -> UNAVAILABLE."""
    profile = get_profile(c.market)
    money = profile.money
    if not c.last_bar:
        return unavailable(c, "audit row has no last_bar, so the price scale cannot be checked")
    try:
        ts = pd.Timestamp(c.last_bar)
        if pd.isna(ts):
            raise ValueError("not a time")
        if ts.tzinfo is not None:
            ts = ts.tz_convert(profile.tz).tz_localize(None)
    except (ValueError, TypeError, OverflowError):
        return unavailable(c, f"unreadable last_bar {c.last_bar!r}, so the price scale cannot be checked")
    hits = np.flatnonzero(bars15.index == ts)
    if len(hits) == 0:
        return unavailable(c, f"no 15-minute bar for the ticket's last bar {ts:%Y-%m-%d %H:%M}")
    j = int(hits[-1])
    lo, hi, cl = (float(bars15[k].to_numpy(dtype=float)[j]) for k in ("Low", "High", "Close"))
    if not all(math.isfinite(x) and x > 0 for x in (lo, hi, cl)):
        return unavailable(c, "bad price data")
    if c.last_close is not None:
        if not lo * (1 - SCALE_TOLERANCE) <= c.last_close <= hi * (1 + SCALE_TOLERANCE):
            return unavailable(c, f"prices re-scaled since the ticket (split/bonus?): logged close "
                                  f"{money(c.last_close)}, that bar now {money(lo)} to {money(hi)}")
    elif abs(c.entry / cl - 1) > SCALE_ENTRY_BAND:
        return unavailable(c, f"prices re-scaled since the ticket (split/bonus?): logged entry {money(c.entry)}, "
                              f"close on the ticket's last bar now {money(cl)}")
    return None


def _missing_session(market: str, dates: list[date]) -> date | None:
    """The first trading session missing between consecutive bar dates (None when the dates are consecutive
    trading sessions). Uncovered years: every weekday is a session."""
    profile = get_profile(market)
    for a, b in zip(dates, dates[1:]):
        if cal.trading_days_between(profile, a, b) > 0:
            return cal.next_trading_day(profile, a)
    return None


def _with_calendar_note(o: Outcome, first: date, last: date) -> Outcome:
    """Say so on the outcome when any date it relied on lies in a year without a holiday calendar."""
    if cal.first_uncovered_year(get_profile(o.candidate.market), first, last) is None:
        return o
    from dataclasses import replace
    return replace(o, note=f"{o.note} {NO_CALENDAR}" if o.note else NO_CALENDAR)


def logged_mid_session(c: Candidate, fill_session: date) -> bool:
    """True when a swing TAKE was logged during its own fill session (at/after the open, before the close):
    that session's daily bar then holds prices from before the order existed."""
    profile = get_profile(c.market)
    local = c.created_at.astimezone(ZoneInfo(profile.tz))
    return local.date() == fill_session and local.time() >= profile.session_open


MID_SESSION_NOTE = "TAKE logged mid-session; intraday bars needed for the fill day"


def _fill_day_intraday(c: Candidate, bars15: pd.DataFrame | None, day: date, why: str,
                       skip_containing: bool = False) -> Outcome | tuple[float, pd.Timestamp, bool]:
    """The fill session of a TAKE logged mid-session, from the 15-minute bar containing created_at onward: LIMIT
    filled at the first bar with Low <= entry, at min(Open, entry) (at the entry on the containing bar, whose Open
    came before the TAKE); after the fill only the stop can trigger that day. ``skip_containing`` starts the search
    after the containing bar. Returns a final Outcome (NOT_FILLED, LOST or UNAVAILABLE) or (fill, fill_at,
    ambiguous) to continue on daily bars; ambiguous = only the containing bar filled it and the logged price was
    above the entry."""
    if bars15 is None:
        return _outcome(c, Status.UNAVAILABLE, MID_SESSION_NOTE + (f" ({why})" if why else ""))
    profile = get_profile(c.market)
    created = c.created_at.astimezone(ZoneInfo(profile.tz)).replace(tzinfo=None)
    close = cal.session_close_for(profile, day)
    idx = bars15.index
    mask = (idx.normalize() == pd.Timestamp(day)) & (idx > pd.Timestamp(created)) & \
           (np.array([t.time() < close for t in idx], dtype=bool) if len(idx) else True)
    sess = bars15[mask]
    bar0 = pd.Timestamp(created).floor("15min")  # the bar containing created_at: its rest is after the TAKE
    if bar0 not in idx:
        return _outcome(c, Status.UNAVAILABLE, f"gap in intraday bars on the fill day (no bar containing "
                                               f"{created:%H:%M})")
    sess = pd.concat([bars15.loc[[bar0]], sess])
    gap_after = _gap_after(sess.index, created)
    if gap_after is not None:
        return _outcome(c, Status.UNAVAILABLE, f"gap in intraday bars on the fill day (missing after {gap_after:%H:%M})")
    last_start = sess.index[-1].to_pydatetime()
    if last_start < datetime.combine(day, close) - timedelta(minutes=15):
        return _outcome(c, Status.UNAVAILABLE, f"incomplete fill-day intraday bars (last bar {last_start:%H:%M})")
    op, lo = sess["Open"].to_numpy(dtype=float), sess["Low"].to_numpy(dtype=float)
    if not _all_finite(op, lo, sess["High"].to_numpy(dtype=float), sess["Close"].to_numpy(dtype=float)):
        return _outcome(c, Status.UNAVAILABLE, "bad price data")
    if _price_trust(c, sess.iloc[0]) == "contradiction":
        return _contradiction(c, sess.iloc[0])
    k = next((p for p in range(1 if skip_containing else 0, len(sess)) if lo[p] <= c.entry), None)
    if k is None:
        return _outcome(c, Status.NOT_FILLED, AMBIGUOUS_FILL_NOTE if skip_containing
                        else "limit not reached after the TAKE on the fill session")
    # the containing bar's Open came before the TAKE: a fill there is at the limit price itself. It is ambiguous
    # (the touch may have come before the TAKE) unless the logged price was already at or below the limit.
    fill = c.entry if k == 0 else min(op[k], c.entry)
    fill_at = c.created_at.isoformat() if k == 0 else sess.index[k]
    trusted = _price_trust(c, sess.iloc[0]) == "trusted"
    ambiguous = k == 0 and not (trusted and c.current_price <= c.entry)
    m = next((p for p in range(k, len(sess)) if lo[p] <= c.stop), None)
    if m is not None:  # target on the fill day is ignored (as on a daily fill bar); a loss is always kept
        px = c.stop if m == 0 else min(op[m], c.stop)
        return _finish(c, fill, fill_at, px, sess.index[m], 0, "stop touched on the fill day")
    return float(fill), fill_at, ambiguous


def resolve_swing(c: Candidate, daily: pd.DataFrame, *, fill_session: date, now: datetime,
                  session_closed_today: bool = False, bars15: pd.DataFrame | None = None,
                  intraday_note: str = "") -> Outcome:
    """CNC LIMIT at entry for one session. Fill bar: filled iff Low <= entry at min(Open, entry); only the stop
    can trigger on it. Then up to max_hold_bars further bars; no exit -> time exit at the last bar's Close.
    Bars dated on/after today (market-local) are ignored unless session_closed_today: they may be partial.
    The bars from the fill session through the last bar used must be consecutive trading sessions: a missing
    session (where the stop could have been hit) makes the trade UNAVAILABLE, never a guess.
    A TAKE logged during its fill session (logged_mid_session) resolves that session from `bars15` (15-minute
    bars, see _fill_day_intraday); without them it is UNAVAILABLE (`intraday_note` says why)."""
    o, last_used = _resolve_swing(c, daily, fill_session=fill_session, now=now,
                                  session_closed_today=session_closed_today, bars15=bars15,
                                  intraday_note=intraday_note)
    return _with_calendar_note(o, fill_session, max(fill_session, last_used or fill_session))


def _resolve_swing(c: Candidate, daily: pd.DataFrame, *, fill_session: date, now: datetime,
                   session_closed_today: bool, bars15: pd.DataFrame | None, intraday_note: str
                   ) -> tuple[Outcome, date | None]:
    tz = ZoneInfo(get_profile(c.market).tz)
    today = now.astimezone(tz).date()
    if not session_closed_today:
        daily = daily[daily.index.normalize() < pd.Timestamp(today)]
    dates = daily.index.normalize()
    hits = np.flatnonzero(dates == pd.Timestamp(fill_session))
    if len(hits) == 0:
        if fill_session > today or (fill_session == today and not session_closed_today):
            return _outcome(c, Status.OPEN, "waiting for the fill session to close"), None
        return _outcome(c, Status.UNAVAILABLE, "no bar for fill session"), None
    i = int(hits[0])
    op, hi, lo, cl = (daily[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    if not _all_finite(op[i:i + c.max_hold_bars + 1], hi[i:i + c.max_hold_bars + 1],
                       lo[i:i + c.max_hold_bars + 1], cl[i:i + c.max_hold_bars + 1]):
        return _outcome(c, Status.UNAVAILABLE, "bad price data"), None
    if logged_mid_session(c, fill_session):
        got = _fill_day_intraday(c, bars15, fill_session, intraday_note)
        if isinstance(got, Outcome):
            return got, None
        fill, fill_at, ambiguous = got
        o, used = _swing_after_fill(c, daily, dates, i, fill, fill_at)
        if ambiguous and _only_if_it_loses(c, o) is not o:  # it would win: only a later bar can fill it for real
            got = _fill_day_intraday(c, bars15, fill_session, intraday_note, skip_containing=True)
            if isinstance(got, Outcome):
                return _cap_fallback(c, o, got), None
            later, used = _swing_after_fill(c, daily, dates, i, got[0], got[1])
            return _cap_fallback(c, o, later), used
        return o, used
    else:
        if not lo[i] <= c.entry:
            return _outcome(c, Status.NOT_FILLED, "limit not reached on the fill session"), None
        fill = min(op[i], c.entry)
        fill_at = daily.index[i]
        if lo[i] <= c.stop:  # target on the fill bar is ignored (intrabar order unknown)
            return _finish(c, fill, fill_at, min(op[i], c.stop), fill_at, 0, "stop touched on the fill bar"), None
    return _swing_after_fill(c, daily, dates, i, fill, fill_at)


def _swing_after_fill(c: Candidate, daily: pd.DataFrame, dates, i: int, fill: float, fill_at
                      ) -> tuple[Outcome, date | None]:
    """Daily bars after the fill session's bar i: exit, time exit or holding; a missing session is unavailable."""
    op, hi, lo, cl = (daily[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    last = min(i + c.max_hold_bars, len(daily) - 1)
    res = _walk(op, hi, lo, cl, i + 1, last, c.stop, c.target_1) if last > i else None
    if res is not None:
        px, j = res
        o = _finish(c, fill, fill_at, px, daily.index[j], j - i, "")
    elif i + c.max_hold_bars <= len(daily) - 1:
        j = i + c.max_hold_bars
        o = _finish(c, fill, fill_at, cl[j], daily.index[j], c.max_hold_bars, "time exit")
    else:
        j = last
        o = _outcome(c, Status.OPEN, "holding", fill=float(fill), fill_at=str(fill_at),
                     mtm=_mtm(c, fill, cl[last]), held=last - i)
    used = [d.date() for d in dates[i:j + 1]]
    missing = _missing_session(c.market, used)
    if missing is not None:
        return _outcome(c, Status.UNAVAILABLE, f"missing daily bar for {missing.isoformat()}"), used[-1]
    return o, used[-1]


def _gap_after(index: pd.DatetimeIndex, after: datetime) -> datetime | None:
    """Time after which 15-minute bars are missing (None if the series is complete from `after`)."""
    step = timedelta(minutes=15)
    floor = datetime.combine(after.date(), time(0, 0)) + ((after - datetime.combine(after.date(), time(0, 0))) // step) * step
    first_expected = floor + step
    starts = [t.to_pydatetime() for t in index]
    if starts[0] > first_expected:
        return first_expected - step
    for a, b in zip(starts, starts[1:]):
        if b - a > step:
            return a
    return None


PLAIN_PRICE_TOLERANCE = 0.005  # a logged price within 0.5% of the TAKE bar's range counts as traded there


def _price_trust(c: Candidate, bar0) -> str:
    """How far the logged price can be used against the bar containing created_at: "none" (no price), "untrusted"
    (not a live quote, or unknown: ignored entirely), "trusted" (a live quote inside [Low, High + 0.5%]) or
    "contradiction" (a live quote outside that range)."""
    cp = c.current_price
    if cp is None:
        return "none"
    if c.quote_live is not True:
        return "untrusted"
    lo0, hi0 = float(bar0["Low"]), float(bar0["High"])
    return "trusted" if lo0 <= cp <= hi0 + PLAIN_PRICE_TOLERANCE * cp else "contradiction"


def _contradiction(c: Candidate, bar0) -> Outcome:
    return _outcome(c, Status.UNAVAILABLE, f"logged live price {c.current_price} is outside the bar's range "
                                           f"{float(bar0['Low'])}-{float(bar0['High'])}")


def limit_model(c: Candidate, limit: float) -> str | None:
    """Which entry the printed ticket was, from the logged price: "plain" (priced in [entry, limit], desk TAKE or
    override: a marketable `BUY ... LIMIT` filled at about that price), "resting" (priced above the limit: a buy
    limit at the limit placed at created_at), or None (no logged price, or below the trigger: the stop-limit)."""
    cp = c.current_price
    if cp is None or cp < c.entry:
        return None
    if cp > limit:
        return "resting"
    return "plain"  # marketable when typed in, desk TAKE or override alike


def _limit_fill(c: Candidate, model: str, limit: float, frame: pd.DataFrame, profile, session_over: bool,
                incomplete: bool, incomplete_note: str, day: date, skip_containing: bool = False) -> Outcome:
    """Bar 0 of ``frame`` contains created_at; bars 1.. follow it (before flat-by). Plain: filled at the logged price
    at created_at. Resting: filled at the limit (the worst case: the order and the price inside a bar are unknown)
    on bar 0 if its Low reaches the limit, else on the first later bar inside the entry window that does. On the
    fill bar a Low at or below the stop is LOST at the stop (we cannot tell whether it came after the fill) and a
    target is never credited; later bars exit as usual (a gap through the stop at its Open)."""
    op, hi, lo, cl = (frame[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    if not _all_finite(op, hi, lo, cl):
        return _outcome(c, Status.UNAVAILABLE, "bad price data")
    n, idx = len(frame), frame.index
    if model == "plain":
        k, fill, note = 0, float(c.current_price), "limit filled at the logged price"
    else:
        win = [j for j in range(1, n) if profile.no_entry_before <= idx[j].time() <= profile.no_entry_after]
        k = 0 if (lo[0] <= limit and not skip_containing) else next((j for j in win if lo[j] <= limit), None)
        fill, note = float(limit), "resting limit filled at the limit"
        if k is None:
            if not session_over:
                return _outcome(c, Status.OPEN, "limit not filled yet")
            if incomplete:
                return _outcome(c, Status.UNAVAILABLE, incomplete_note)
            return _outcome(c, Status.NOT_FILLED, AMBIGUOUS_FILL_NOTE if skip_containing else
                            f"not filled inside the entry window (cancel by {cal.entry_cancel_by(profile, day):%H:%M})")
    fill_at = c.created_at.isoformat() if k == 0 else str(idx[k])
    o = _exits_after_fill(c, k, fill, fill_at, op, hi, lo, cl, idx, session_over, incomplete, incomplete_note, note)
    if model == "resting" and k == 0 and _only_if_it_loses(c, o) is not o:  # would win: only a later bar counts
        return _cap_fallback(c, o, _limit_fill(c, model, limit, frame, profile, session_over, incomplete,
                                               incomplete_note, day, skip_containing=True))
    return o


def _exits_after_fill(c: Candidate, k: int, fill: float, fill_at: str, op, hi, lo, cl, idx, session_over: bool,
                      incomplete: bool, incomplete_note: str, note: str) -> Outcome:
    """Exit of a fill on bar k: a Low at or below the stop on the fill bar is LOST at the stop (its target never
    counts); later bars exit as usual; else a time exit at flat-by, or OPEN while the session runs."""
    n = len(idx)
    if lo[k] <= c.stop:  # conservative: the fill bar's low may have come after the fill; its target never counts
        return _finish(c, fill, fill_at, c.stop, idx[k], 0, "stop touched on the fill bar")
    res = _walk(op, hi, lo, cl, k + 1, n - 1, c.stop, c.target_1) if k + 1 <= n - 1 else None
    if res is not None:
        px, j = res
        return _finish(c, fill, fill_at, px, idx[j], j - k, note)
    if session_over:
        if incomplete:
            return _outcome(c, Status.UNAVAILABLE, incomplete_note)
        return _finish(c, fill, fill_at, cl[n - 1], idx[n - 1], n - 1 - k, "time exit at flat-by")
    return _outcome(c, Status.OPEN, "holding", fill=fill, fill_at=fill_at, mtm=_mtm(c, fill, cl[n - 1]),
                    held=n - 1 - k)


def resolve_intraday(c: Candidate, bars15: pd.DataFrame, *, flat_by: time, now: datetime) -> Outcome:
    """A row logged at or above the trigger printed a LIMIT ticket: see limit_model and _limit_fill. Otherwise:
    BUY stop-limit (trigger = entry, limit = entry_limit, as the ticket prints it) over created_at's session, from
    the bar containing created_at to flat_by (that bar under the ambiguous-fill rule and its cap, see
    _only_if_it_loses / _cap_fallback; a certain fill there when, without a logged price, it traded wholly inside
    [trigger, limit]), later bars filled by fills.stop_limit_fill: never better than the
    trigger, never worse than the limit, and only inside the desk's entry window [no_entry_before,
    no_entry_after] like the Time Machine (the ticket says to cancel the entry after it). Only the stop can
    trigger on the fill bar, exiting at the stop: the stop order exists only after the fill, so not at that bar's
    Open. Later bars exit at min(Open, stop) on a gap."""
    profile = get_profile(c.market)
    tz = ZoneInfo(profile.tz)
    created = c.created_at.astimezone(tz).replace(tzinfo=None)
    now_l = now.astimezone(tz).replace(tzinfo=None)
    day = created.date()
    session_over = now_l.date() > day or (now_l.date() == day and now_l.time() >= flat_by)
    idx = bars15.index
    mask = (idx.normalize() == pd.Timestamp(day)) & (idx > pd.Timestamp(created)) & \
           (np.array([t.time() < flat_by for t in idx], dtype=bool) if len(idx) else True)
    sess = bars15[mask]
    limit = fills.limit_or_default(c.entry_limit, c.entry, c.stop, profile.tick)
    model = limit_model(c, limit)
    if model is not None and not (profile.no_entry_before <= created.time() < cal.entry_cancel_by(profile, day)):
        return _outcome(c, Status.NOT_FILLED, "logged outside the entry window")
    if model is None and created.time() >= cal.entry_cancel_by(profile, day):  # nothing left to fill in
        return _outcome(c, Status.NOT_FILLED, "logged outside the entry window")
    bar0 = pd.Timestamp(created).floor("15min")  # the bar containing created_at: its remainder is after it
    has_bar0 = bar0 in idx and bar0.time() < flat_by
    if created.time() >= profile.session_open and not has_bar0:
        if sess.empty and not session_over:
            return _outcome(c, Status.OPEN, "session still running")
        if not sess.empty or session_over:
            return _outcome(c, Status.UNAVAILABLE, f"gap in intraday bars (no bar containing {created:%H:%M})")
    frame = pd.concat([bars15.loc[[bar0]], sess]) if has_bar0 else sess
    if frame.empty:
        if not session_over:
            return _outcome(c, Status.OPEN, "session still running")
        return _outcome(c, Status.UNAVAILABLE, "no intraday bars for the session")
    gap_after = _gap_after(frame.index, max(created, datetime.combine(day, profile.session_open)))
    if gap_after is not None:
        return _outcome(c, Status.UNAVAILABLE, f"gap in intraday bars (missing after {gap_after:%H:%M})")
    op, hi, lo, cl = (frame[k].to_numpy(dtype=float) for k in ("Open", "High", "Low", "Close"))
    if not _all_finite(op, hi, lo, cl):
        return _outcome(c, Status.UNAVAILABLE, "bad price data")
    last_start = frame.index[-1]
    incomplete = session_over and last_start.to_pydatetime() < datetime.combine(day, flat_by) - timedelta(minutes=15)
    incomplete_note = f"incomplete session bars (last bar {last_start:%H:%M})"
    trust = _price_trust(c, frame.iloc[0]) if has_bar0 else "none"
    if trust == "contradiction":
        return _contradiction(c, frame.iloc[0])
    if model == "plain" and trust != "trusted":
        model = "resting"  # a price that is not a live quote the bar traded at: never a certain fill
    if model is not None:  # the ticket was a LIMIT: the bar containing created_at counts
        return _limit_fill(c, model, limit, frame, profile, session_over, incomplete, incomplete_note, day)
    in_win = [profile.no_entry_before <= t.time() <= profile.no_entry_after for t in frame.index]
    price = c.current_price if trust == "trusted" else None  # an untrusted price is ignored entirely
    o, ambiguous = _stop_limit_from(c, frame, op, hi, lo, cl, in_win, has_bar0, limit, profile, day, session_over,
                                    incomplete, incomplete_note, skip_containing=False, price=price)
    if ambiguous and _only_if_it_loses(c, o) is not o:  # an ambiguous containing-bar trigger that would win
        later, _ = _stop_limit_from(c, frame, op, hi, lo, cl, in_win, has_bar0, limit, profile, day, session_over,
                                    incomplete, incomplete_note, skip_containing=True, price=price)
        o = _cap_fallback(c, o, later)
    return o


def _stop_limit_from(c: Candidate, frame, op, hi, lo, cl, in_win, has_bar0: bool, limit: float, profile, day: date,
                     session_over: bool, incomplete: bool, incomplete_note: str, *, skip_containing: bool,
                     price: float | None = None) -> tuple[Outcome, bool]:
    """(outcome, ambiguous): ambiguous when only the bar containing created_at triggered the fill."""
    got = None
    if (has_bar0 and not skip_containing and in_win[0] and price is None
            and lo[0] >= c.entry and hi[0] <= limit):
        # no logged price, and the whole containing bar traded inside [trigger, limit]: the order was marketable
        # whenever it was placed in that bar, a certain fill; at the limit, the worst price in the bar
        return _exits_after_fill(c, 0, float(limit), c.created_at.isoformat(), op, hi, lo, cl, frame.index,
                                 session_over, incomplete, incomplete_note, CERTAIN_FILL_NOTE), False
    if has_bar0 and not skip_containing and in_win[0] and hi[0] >= c.entry and lo[0] <= limit:
        # the trigger may have come in the containing bar's remainder: fill there (at the trigger, or at the limit
        # when the whole bar traded above the trigger); only its stop counts, never its target
        # no logged price: the worst price consistent with the bar (it may have been above the trigger at the
        # TAKE); a logged price below the trigger: the trigger itself
        fill0 = min(hi[0], limit) if price is None else (c.entry if lo[0] <= c.entry else limit)
        got = (0, float(fill0))
    else:
        first = 1 if has_bar0 else 0
        window = [p for p in range(first, len(frame)) if in_win[p]]
        got = (fills.stop_limit_fill(op, hi, lo, window[0], window[-1], c.entry, limit)  # BUY stop-limit
               if window else None)
    if got is None:
        if not session_over:
            return _outcome(c, Status.OPEN, "stop-limit not filled yet"), False
        if incomplete:
            return _outcome(c, Status.UNAVAILABLE, incomplete_note), False
        return _outcome(c, Status.NOT_FILLED, AMBIGUOUS_FILL_NOTE if skip_containing else
                        f"not filled inside the entry window (cancel by {cal.entry_cancel_by(profile, day):%H:%M})"), False
    k, fill = got
    ambiguous = has_bar0 and k == 0  # only the bar containing created_at triggered it
    fill_at = c.created_at.isoformat() if ambiguous else str(frame.index[k])
    return _exits_after_fill(c, k, float(fill), fill_at, op, hi, lo, cl, frame.index, session_over, incomplete,
                             incomplete_note, ""), ambiguous


# ---------------------------------------------------------------- statistics

def _verdict(n: int, mean_pred_mc: float | None, ci_low: float, ci_high: float, mean_r: float,
             n_pred: int = 0, unavailable: int = 0) -> str:
    """First rule that applies: too few trades, Losing (bad news is never hidden), too many trades that could
    not be replayed, odds missing, Worse, Matching, else Inconclusive."""
    eps = 1e-9
    if n < MIN_TRADES:
        return f"Not enough trades yet ({n}/{MIN_TRADES}) — keep paper-trading"
    if ci_high < 0:
        return "Losing — do not trade this with real money"
    if unavailable > MAX_UNAVAILABLE_SHARE * (n + unavailable):
        return (f"Inconclusive — {unavailable} trades could not be replayed; "
                "fix the data before trusting this")
    if n_pred < MIN_PRED_COVERAGE * n or mean_pred_mc is None:
        return f"Inconclusive — keep paper-trading (odds missing for {n - n_pred} of {n} trades)"
    if ci_high < mean_pred_mc - eps:
        return "Worse than the desk's odds — do not size up"
    if ci_low > 0 and mean_r >= mean_pred_mc - 0.15:
        return "Matching the odds — real money at small size (≤0.5% risk)"
    return "Inconclusive — keep paper-trading"


def _mean(xs: list[float]) -> float | None:
    return float(np.mean(xs)) if xs else None


def _stats(record: str, market: str, mode: Mode, outs: list[Outcome], seed: int, resamples: int) -> ScoreStats:
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
                          _verdict(0, mean_pred_mc, 0.0, 0.0, 0.0), None, record)
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
                      _verdict(n, mean_pred_mc, ci_low, ci_high, mean_r, len(mc), count(Status.UNAVAILABLE)),
                      len(mc) / n, record)


def summarize(outcomes: Iterable[Outcome], *, seed: int = 19, resamples: int = 2000) -> list[ScoreStats]:
    """Stats per (record, market, mode), desk picks first: a record's trades are never pooled with another's."""
    groups: dict[tuple[str, str, Mode], list[Outcome]] = {}
    for o in outcomes:
        c = o.candidate
        groups.setdefault((c.record, c.market, c.mode), []).append(o)
    order = list(RECORD_LABELS)
    return [_stats(r, m, md, groups[(r, m, md)], seed, resamples)
            for r, m, md in sorted(groups, key=lambda k: (order.index(k[0]) if k[0] in order else len(order),
                                                          k[0], k[1], k[2].value))]
