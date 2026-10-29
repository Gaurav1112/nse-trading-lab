"""Scoreboard I/O: reads the audit log, keeps the resolved-outcome cache and replays every logged TAKE,
owner override and regime sat-out (three records, each with its own candidates, cache entries and stats).

The pure rules live in `scoreboard`; this module only feeds them bars and remembers final results.
Safety rules: every candidate comes back (UNAVAILABLE, never dropped); only final statuses (won, lost, time,
not_filled) are cached; a cached entry is served only when it matches the current candidate field for field
and is internally consistent; a corrupt cache file is backed up and rebuilt, loudly. Nothing here places an order.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import calendars as cal
from .markets import get_profile
from .models import Mode
from .scoreboard import (
    RECORD_OF_ACTION, SCOREBOARD_VERSION, Candidate, Outcome, ScoreStats, Status, check_intraday_price_scale,
    check_price_scale, exit_status, load_candidates,
    _from_quote, logged_mid_session, realised_r, resolve_intraday, resolve_swing, summarize, unavailable,
)

Warn = Callable[[str], None] | None

SCOREBOARD_DISCLAIMER = ("Paper record of the desk's TAKEs and your overrides, as if each ticket were followed "
                         "exactly — not your real P&L.")
SCOREBOARD_COVERAGE = ("Covers TAKEs from Check a Trade, Today's Plan and the Intraday-tab scan; "
                       "your overrides are scored as a separate record, and so are the plan's TAKEs the desk sat out "
                       "only because of the market regime (what sitting out saved or cost).")
AUDIT_FILE = "desk_audit.jsonl"
CACHE_FILE = "desk_scoreboard.json"
MAX_AUDIT_BYTES = 50 * 1024 * 1024
MAX_CACHE_BYTES = 20 * 1024 * 1024
INTRADAY_MAX_AGE_DAYS = 58  # yfinance serves 15-minute bars for the last 60 days; keep a margin
FINAL = (Status.WON, Status.LOST, Status.TIME, Status.NOT_FILLED)
_RESOLVED = (Status.WON, Status.LOST, Status.TIME)
_CANDIDATE_FIELDS = ("market", "symbol", "mode", "entry", "stop", "target_1", "max_hold_bars", "cost_per_share",
                     "qty", "last_bar", "last_close", "entry_limit", "record", "current_price", "quote_live",
                     "quote_as_of", "side")


@dataclass(frozen=True)
class ScoreboardResult:
    outcomes: list[Outcome]            # every candidate, in created_at order — none silently dropped
    stats: list[ScoreStats]            # summarize(outcomes)
    rejects: list[tuple[int, str]]     # (audit line number, reason): unreadable lines, malformed TAKE/OVERRIDE/SAT_OUT rows
    cache_note: str | None             # set when the cache was unreadable, of another version or not writable
    note: str | None = None            # set when there is no audit log at all


class AuditReadError(Exception):
    """The audit log exists but cannot be read (permissions, a directory, an I/O error)."""


MISMATCH_NOTE = "cached result no longer matches the logged ticket (kept in cache)"


def _warn(on_warning: Warn, msg: str) -> None:
    if on_warning is not None:
        on_warning(msg)


# from an absolute path's first "/" to its last "/" before a quote, colon or line end (spaces allowed: folder
# names have them); everything up to and including that last "/" is dropped, leaving the file name. Greedy on
# purpose: a message with two paths may lose words between them, but never shows a directory.
_ABS_DIRS = re.compile(r"(?<![\w.])/[^'\":\n]*/")


def _scrub(text: str) -> str:
    """Absolute paths in a message reduced to their file names (the data directory is the user's business)."""
    return _ABS_DIRS.sub("", text)


def _err(e: BaseException) -> str:
    """A user-visible reason: strerror (or the class name) plus the file name for OS errors, never a full path."""
    if isinstance(e, OSError) and (e.strerror or e.filename):
        msg = e.strerror or type(e).__name__
        name = e.filename
        if name and isinstance(name, (str, bytes, os.PathLike)):
            return f"{msg}: {Path(os.fsdecode(name)).name}"
        return msg
    return _scrub(str(e)) or type(e).__name__


# ---------------------------------------------------------------- sessions

def fill_session_of(market: str, created_at: datetime) -> date:
    """The session a swing LIMIT order logged at created_at belongs to: that day's session if it is a trading
    day and the order came before its close, else the next trading session. Uncovered years: weekday rule."""
    profile = get_profile(market)
    local = created_at.astimezone(ZoneInfo(profile.tz))
    d = local.date()
    if cal.is_trading_day(profile, d) and local.time() < cal.session_close_for(profile, d):
        return d
    return cal.next_trading_day(profile, d)


def _local_date(market: str, created_at: datetime) -> date:
    return created_at.astimezone(ZoneInfo(get_profile(market).tz)).date()


# ---------------------------------------------------------------- audit

def read_audit(path: Path) -> tuple[list[tuple[int, dict]], list[tuple[int, str]]]:
    """(line number, row) for every JSON-object line; unreadable lines become rejects. Missing file -> empty."""
    rows, rejects, _ = _read_audit(path)
    return rows, rejects


def _read_audit(path: Path) -> tuple[list[tuple[int, dict]], list[tuple[int, str]], bool]:
    """read_audit plus whether the file was missing. Larger than MAX_AUDIT_BYTES or unreadable -> AuditReadError."""
    path = Path(path)
    try:
        size = path.stat().st_size
        if size > MAX_AUDIT_BYTES:
            raise AuditReadError(f"audit log too large ({size // (1024 * 1024)} MB)")
        raw = path.read_bytes()
    except FileNotFoundError:
        return [], [], True
    except OSError as e:
        raise AuditReadError(f"cannot read audit log {path.name}: {e.strerror or type(e).__name__}") from e
    rows: list[tuple[int, dict]] = []
    rejects: list[tuple[int, str]] = []
    for n, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, RecursionError):
            obj = None
        if not isinstance(obj, dict):
            rejects.append((n, "unreadable audit line"))
            continue
        rows.append((n, obj))
    return rows, rejects, False


def _row_market(row: dict) -> str | None:
    try:
        return get_profile(str(row.get("market"))).code
    except ValueError:
        return None


def _screen(rows: list[dict], session_of) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Rows whose TAKE/OVERRIDE/SAT_OUT parses and can be placed in a session; others come back with a reason. A single
    odd row (a date at the edge of the calendar, a 1e999 quantity) must become a reject, never crash the
    scoreboard. Rows of other actions are kept: load_candidates ignores them."""
    keep: list[dict] = []
    bad: list[tuple[dict, str]] = []
    for r in rows:
        action = r.get("action")
        if action not in RECORD_OF_ACTION:
            keep.append(r)
            continue
        good, why = load_candidates([r], session_of=lambda m, t: None)
        if why:
            bad.append((r, f"malformed {action}: {why[0][1]}"))
            continue
        try:
            session_of(good[0].market, good[0].created_at)
        except (ValueError, ArithmeticError, RuntimeError) as e:
            bad.append((r, f"malformed {action}: cannot place created_at in a session ({_err(e)})"))
            continue
        keep.append(r)
    return keep, bad


def candidates_from(rows: list[tuple[int, dict]], market: str | None = None
                    ) -> tuple[list[Candidate], list[tuple[int, str]]]:
    """Candidates (created_at order) and (line, reason) rejects for malformed TAKE/OVERRIDE/SAT_OUT rows. Swing rows are
    deduped on the fill session, intraday rows on the market-local date of created_at, each within its record. A
    row whose market cannot be read is kept whatever the filter, so a malformed row is always reported."""
    if market is not None:
        rows = [(n, r) for n, r in rows if _row_market(r) in (market, None)]
    line_of = {id(r): n for n, r in rows}
    intraday = [r for _, r in rows if r.get("mode") == Mode.INTRADAY.value]
    swing = [r for _, r in rows if r.get("mode") != Mode.INTRADAY.value]
    out: list[Candidate] = []
    rejects: list[tuple[int, str]] = []
    for group, session_of in ((swing, fill_session_of), (intraday, _local_date)):
        group, early = _screen(group, session_of)
        rejects += [(line_of[id(r)], why) for r, why in early]
        good, bad = load_candidates(group, session_of=session_of)
        out += good
        rejects += [(line_of[id(r)], f"malformed {r.get('action')}: {why}") for r, why in bad]
    out.sort(key=lambda c: (c.created_at, c.key))
    return out, rejects


# ---------------------------------------------------------------- cache

def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _candidate_record(c: Candidate) -> dict:
    return {"market": c.market, "symbol": c.symbol, "mode": c.mode.value, "entry": c.entry, "stop": c.stop,
            "target_1": c.target_1, "max_hold_bars": c.max_hold_bars, "cost_per_share": c.cost_per_share,
            "qty": c.qty, "created_at": c.created_at.isoformat(), "mc_expectancy_r": c.mc_expectancy_r,
            "history_expectancy_r": c.history_expectancy_r, "key": c.key, "last_bar": c.last_bar,
            "last_close": c.last_close, "entry_limit": c.entry_limit, "record": c.record,
            "current_price": c.current_price, "quote_live": c.quote_live,
            "quote_as_of": c.quote_as_of.isoformat() if c.quote_as_of is not None else None,
            "side": c.side.value}


def _entry(o: Outcome) -> dict:
    return {"candidate": _candidate_record(o.candidate),
            "outcome": {"status": o.status.value, "fill_price": o.fill_price, "fill_at": o.fill_at,
                        "exit_price": o.exit_price, "exit_at": o.exit_at, "realised_r": o.realised_r,
                        "mtm_r": o.mtm_r, "bars_held": o.bars_held, "note": o.note}}


def _well_formed(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    try:  # NaN / Infinity (which json.loads accepts) can never be written back: drop the entry
        json.dumps(entry, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        return False
    cand, out = entry.get("candidate"), entry.get("outcome")
    if not isinstance(cand, dict) or not isinstance(out, dict):
        return False
    return all(k in cand for k in _CANDIDATE_FIELDS) and out.get("status") in {s.value for s in FINAL}


_BOOL_FIELDS = frozenset({"quote_live"})  # bool-valued candidate fields (True/False/None)


def _same(k: str, cached, current) -> bool:
    """A bool field matches only the same bool (or None == None); any other field never matches a bool, since
    True == 1 in Python."""
    if k in _BOOL_FIELDS:
        return type(cached) is type(current) and cached == current
    return type(cached) is not bool and cached == current


def _matches(c: Candidate, entry: dict) -> bool:
    """True when the entry was resolved for exactly this ticket."""
    cand, current = entry["candidate"], _candidate_record(c)
    return all(_same(k, cand.get(k), current[k]) for k in _CANDIDATE_FIELDS)


def _outcome_from(c: Candidate, entry: dict) -> Outcome | None:
    """The cached Outcome for c (whose ticket matches), or None when the entry is internally inconsistent."""
    o = entry["outcome"]
    try:
        status = Status(o["status"])
    except (ValueError, KeyError):
        return None
    held, note = o.get("bars_held"), o.get("note")
    if not isinstance(held, int) or isinstance(held, bool) or held < 0 or not isinstance(note, str):
        return None
    fill, exit_px, r = o.get("fill_price"), o.get("exit_price"), o.get("realised_r")
    fill_at, exit_at = o.get("fill_at"), o.get("exit_at")
    if o.get("mtm_r") is not None:
        return None
    if status is Status.NOT_FILLED:
        if any(x is not None for x in (fill, exit_px, r, fill_at, exit_at)) or held != 0:
            return None
        return Outcome(c, status, None, None, None, None, None, None, 0, note)
    if status not in _RESOLVED:
        return None
    if not (_finite(fill) and _finite(exit_px) and _finite(r) and isinstance(fill_at, str)
            and isinstance(exit_at, str)):
        return None
    # never trust a cached result that the rules would not produce from its own prices
    if exit_status(c, float(exit_px)) is not status or abs(realised_r(c, float(fill), float(exit_px)) - r) > 1e-9:
        return None
    return Outcome(c, status, float(fill), fill_at, float(exit_px), exit_at, float(r), None, held, note)


class ScoreboardCache:
    """desk_scoreboard.json: {"version": SCOREBOARD_VERSION, "outcomes": {Candidate.key: entry}}."""

    def __init__(self, path: Path, now: datetime, on_warning: Warn = None):
        self.path = Path(path)
        self.entries: dict[str, dict] = {}
        self.notes: list[str] = []
        self.dirty = False
        self.writable = True
        self.mismatched: set[str] = set()
        self._on_warning = on_warning
        self._load(now)

    @property
    def note(self) -> str | None:
        return "; ".join(self.notes) or None

    def _note(self, msg: str) -> None:
        self.notes.append(msg)
        _warn(self._on_warning, msg)

    def _backup(self, now: datetime, why: str, tag: str = "corrupt") -> None:
        stamp = now.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
        base = self.path.with_name(f"{self.path.name}.{tag}-{stamp}")
        try:
            # link-then-unlink: os.link fails when the name exists, so an existing backup is never replaced
            for i in range(100):
                target = base if i == 0 else base.with_name(f"{base.name}-{i}")
                try:
                    os.link(self.path, target, follow_symlinks=False)
                    break
                except FileExistsError:
                    continue
            else:
                raise FileExistsError(f"no free backup name for {self.path.name}")
            os.unlink(self.path)
        except (OSError, NotImplementedError) as e:  # NotImplementedError: no hard links on this platform
            self.writable = False  # never overwrite a corrupt file we could not keep a copy of
            self._note(f"scoreboard cache was {why} and could not be backed up ({_err(e)}); "
                       "results recomputed, cache left untouched")
            return
        self.dirty = True
        self._note(f"scoreboard cache was {why}; backed up to {target.name}; rebuilt")

    def _load(self, now: datetime) -> None:
        try:
            size = self.path.stat().st_size
            if size > MAX_CACHE_BYTES:
                self._backup(now, f"too large ({size // (1024 * 1024)} MB)")
                return
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except UnicodeDecodeError:
            self._backup(now, "unreadable")
            return
        except OSError as e:
            self.writable = False
            self._note(f"scoreboard cache could not be read ({_err(e)}); results recomputed, cache left untouched")
            return
        try:
            data = json.loads(text)
        except (ValueError, RecursionError):
            self._backup(now, "unreadable")
            return
        if not isinstance(data, dict) or "version" not in data or not isinstance(data.get("outcomes"), dict):
            self._backup(now, "unreadable (wrong shape)")
            return
        version = data["version"]
        if type(version) is not int or version != SCOREBOARD_VERSION:
            # kept, not overwritten: it may hold intraday results older than yfinance's 60 days
            self._backup(now, f"version {version!r}, not {SCOREBOARD_VERSION}, so it was ignored", tag="other-version")
            return
        for k, v in data["outcomes"].items():
            if _well_formed(v):
                self.entries[k] = v
            else:
                self.dirty = True  # a single bad entry is dropped and recomputed

    def get(self, c: Candidate) -> Outcome | None:
        """The cached outcome, or None. A ticket mismatch keeps the entry (it may be irreplaceable, e.g. intraday
        bars past 60 days) and records the key in `mismatched`; an inconsistent entry is dropped."""
        entry = self.entries.get(c.key)
        if entry is None:
            return None
        if not _matches(c, entry):
            self.mismatched.add(c.key)
            return None
        o = _outcome_from(c, entry)
        if o is None:  # inconsistent: forget it; a fresh final result replaces it
            del self.entries[c.key]
            self.dirty = True
        return o

    def put(self, o: Outcome) -> bool:
        """Store a final outcome (replacing any entry under its key). Returns True when it is in the cache."""
        if o.status not in FINAL:
            return False
        entry = _entry(o)
        if self.entries.get(o.candidate.key) != entry:
            self.entries[o.candidate.key] = entry
            self.dirty = True
        self.mismatched.discard(o.candidate.key)
        return True

    def save(self) -> None:
        if not self.dirty or not self.writable:
            return
        tmp = None
        try:
            payload = json.dumps({"version": SCOREBOARD_VERSION, "outcomes": self.entries}, indent=1,
                                 sort_keys=True, allow_nan=False)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)  # the temp file is 0o600 already (fchmod above)
            tmp = None
            self.dirty = False
        except (OSError, ValueError, TypeError) as e:
            self._note(f"scoreboard cache could not be written ({_err(e)}); results are still correct")
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# ---------------------------------------------------------------- replay

def _replay(c: Candidate, on_warning: Warn, fn, *args, **kw) -> Outcome:
    try:
        return fn(c, *args, **kw)
    except Exception as e:  # noqa: BLE001 - keep the trade visible, but say what broke (it may be a bug)
        note = f"could not replay ({type(e).__name__}): {_err(e)}"
        _warn(on_warning, f"scoreboard: {c.market} {c.symbol} {c.mode.value} {c.created_at.isoformat()}: {note}")
        return unavailable(c, note)


def _replay_swing(c: Candidate, daily: pd.DataFrame, **kw) -> Outcome:
    """The swing replay, refused when the bars are no longer on the ticket's price scale."""
    return check_price_scale(c, daily) or resolve_swing(c, daily, **kw)


def _replay_intraday(c: Candidate, bars15: pd.DataFrame, **kw) -> Outcome:
    """The intraday replay, refused when the 15-minute bars are no longer on the ticket's price scale."""
    return check_intraday_price_scale(c, bars15) or resolve_intraday(c, bars15, **kw)


def _decided_on(o: Outcome) -> date | None:
    """The market-local date whose bars decided a final outcome (None when it cannot be told)."""
    c = o.candidate
    try:
        if c.mode is Mode.INTRADAY:
            return _local_date(c.market, c.created_at)
        if o.status is Status.NOT_FILLED:
            return fill_session_of(c.market, c.created_at)
        return pd.Timestamp(o.exit_at).date()
    except (ValueError, TypeError, ArithmeticError, RuntimeError):
        return None


def _cacheable(o: Outcome, now: datetime) -> bool:
    """Final and decided before market-today: bars just after the close can still be provisional."""
    if o.status not in FINAL:
        return False
    decided = _decided_on(o)
    return decided is not None and decided < now.astimezone(ZoneInfo(get_profile(o.candidate.market).tz)).date()


def _resolve_group(cs: list[Candidate], mode: Mode, now: datetime, load_daily, load_intraday,
                   on_warning: Warn = None) -> list[Outcome]:
    """Outcomes for uncached candidates of one (market, symbol, mode), longs and shorts alike; the bars are loaded at
    most once. A short's replay holds it only to its own square-off (scoreboard.short_hold)."""
    profile = get_profile(cs[0].market)
    symbol = cs[0].symbol
    now_l = now.astimezone(ZoneInfo(profile.tz))
    today = now_l.date()
    out: dict[int, Outcome] = {}
    if mode is Mode.INTRADAY:
        for i, c in enumerate(cs):
            if (today - _local_date(c.market, c.created_at)).days > INTRADAY_MAX_AGE_DAYS:
                out[i] = unavailable(c, "intraday bars older than 60 days (yfinance limit)")
        todo = [i for i in range(len(cs)) if i not in out]
        if todo:
            try:
                bars = load_intraday(profile, symbol)
            except Exception as e:  # noqa: BLE001
                _warn(on_warning, f"scoreboard: intraday bars for {profile.code} {symbol} unavailable: {_err(e)}")
                for i in todo:
                    out[i] = unavailable(cs[i], f"intraday bars unavailable: {_err(e)}")
            else:
                for i in todo:
                    c = cs[i]
                    flat_by = cal.flat_by_for(profile, _local_date(c.market, c.created_at))
                    out[i] = _replay(c, on_warning, _replay_intraday, bars, flat_by=flat_by, now=now)
    else:
        closed = cal.is_trading_day(profile, today) and now_l.time() >= cal.session_close_for(profile, today)
        fills = [fill_session_of(c.market, c.created_at) for c in cs]  # cannot fail: candidates_from screened it
        try:
            daily = load_daily(profile, symbol, adjusted=False)  # dividend-adjusted bars are not what was printed
        except Exception as e:  # noqa: BLE001
            _warn(on_warning, f"scoreboard: daily bars for {profile.code} {symbol} unavailable: {_err(e)}")
            for i, c in enumerate(cs):
                out[i] = unavailable(c, f"daily bars unavailable: {_err(e)}")
        else:
            bars15, why = _fill_day_bars(cs, fills, profile, symbol, today, closed, load_intraday, on_warning)
            for i, c in enumerate(cs):
                fresh = _intraday_in_range(fills[i], today)
                out[i] = _replay(c, on_warning, _replay_swing, daily, fill_session=fills[i], now=now,
                                 session_closed_today=closed, bars15=bars15 if fresh else None,
                                 intraday_note=why if fresh else _TOO_OLD)
    return [out[i] for i in range(len(cs))]


_TOO_OLD = "older than 60 days (yfinance limit)"


def _intraday_in_range(day: date, today: date) -> bool:
    return (today - day).days <= INTRADAY_MAX_AGE_DAYS


def _fill_day_bars(cs: list[Candidate], fills: list[date], profile, symbol: str, today: date, closed: bool,
                   load_intraday, on_warning: Warn) -> tuple[pd.DataFrame | None, str]:
    """15-minute bars for swing TAKEs logged during a fill session that has closed (loaded once, only when one
    needs them and is within yfinance's 60 days), and why they are missing when they are."""
    # the replay starts at max(created_at, quote_as_of), so load bars for the same start
    mids = [f for c, f in zip(cs, fills) if logged_mid_session(_from_quote(c), f)
            and (f < today or (f == today and closed))]
    if not mids:
        return None, ""
    if not any(_intraday_in_range(f, today) for f in mids):
        return None, _TOO_OLD
    try:
        return load_intraday(profile, symbol), ""
    except Exception as e:  # noqa: BLE001 - the trades stay visible as UNAVAILABLE
        _warn(on_warning, f"scoreboard: intraday bars for {profile.code} {symbol} unavailable: {_err(e)}")
        return None, f"intraday bars unavailable: {_err(e)}"


def build_scoreboard(market: str | None, *, now: datetime, audit_path: Path, cache_path: Path,
                     on_warning: Warn, load_daily, load_intraday) -> ScoreboardResult:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    want = None if market is None or str(market).strip().upper() == "ALL" else get_profile(market).code
    rows, rejects, missing = _read_audit(audit_path)
    note = f"No audit log at {Path(audit_path).name} in the data directory" if missing else None
    cands, bad = candidates_from(rows, want)
    rejects = sorted(rejects + bad)
    cache = ScoreboardCache(cache_path, now, on_warning)
    outcomes: list[Outcome | None] = [cache.get(c) for c in cands]
    groups: dict[tuple[str, str, Mode], list[int]] = {}
    for i, c in enumerate(cands):
        if outcomes[i] is None:
            groups.setdefault((c.market, c.symbol, c.mode), []).append(i)
    for (_, _, mode), idx in groups.items():
        for i, o in zip(idx, _resolve_group([cands[i] for i in idx], mode, now, load_daily, load_intraday,
                                            on_warning)):
            stored = _cacheable(o, now) and cache.put(o)
            if not stored and o.status not in FINAL and cands[i].key in cache.mismatched:
                # the old entry belongs to another ticket: kept in the file, never served as this outcome
                o = unavailable(cands[i], f"{MISMATCH_NOTE}; recomputed: {o.status.value}"
                                          + (f" ({o.note})" if o.note else ""))
            outcomes[i] = o
    cache.save()
    final = [o for o in outcomes if o is not None]
    if len(final) != len(cands):  # every candidate is reported, never silently dropped
        raise RuntimeError("scoreboard lost a candidate; refusing to show a partial record")
    return ScoreboardResult(final, summarize(final), rejects, cache.note, note)
