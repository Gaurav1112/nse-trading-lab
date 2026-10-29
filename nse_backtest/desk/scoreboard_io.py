"""Scoreboard I/O: reads the audit log, keeps the resolved-outcome cache and replays every logged TAKE.

The pure rules live in `scoreboard`; this module only feeds them bars and remembers final results.
Safety rules: every candidate comes back (UNAVAILABLE, never dropped); only final statuses (won, lost, time,
not_filled) are cached; a cached entry is served only when it matches the current candidate field for field
and is internally consistent; a corrupt cache file is backed up and rebuilt, loudly. Nothing here places an order.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import calendars as cal
from .markets import get_profile
from .models import Mode
from .scoreboard import (
    SCOREBOARD_VERSION, Candidate, Outcome, ScoreStats, Status, exit_status, load_candidates, realised_r,
    resolve_intraday, resolve_swing, summarize, unavailable,
)

Warn = Callable[[str], None] | None

SCOREBOARD_DISCLAIMER = ("Paper record of the desk's TAKEs as if each ticket were followed exactly — "
                         "not your real P&L.")
AUDIT_FILE = "desk_audit.jsonl"
CACHE_FILE = "desk_scoreboard.json"
INTRADAY_MAX_AGE_DAYS = 58  # yfinance serves 15-minute bars for the last 60 days; keep a margin
FINAL = (Status.WON, Status.LOST, Status.TIME, Status.NOT_FILLED)
_RESOLVED = (Status.WON, Status.LOST, Status.TIME)
_CANDIDATE_FIELDS = ("market", "symbol", "mode", "entry", "stop", "target_1", "max_hold_bars", "cost_per_share",
                     "qty")


@dataclass(frozen=True)
class ScoreboardResult:
    outcomes: list[Outcome]            # every candidate, in created_at order — none silently dropped
    stats: list[ScoreStats]            # summarize(outcomes)
    rejects: list[tuple[int, str]]     # (audit line number, reason) for unreadable lines and malformed TAKE rows
    cache_note: str | None             # set when the cache was unreadable, of another version or not writable


def _warn(on_warning: Warn, msg: str) -> None:
    if on_warning is not None:
        on_warning(msg)


def _err(e: BaseException) -> str:
    return str(e) or type(e).__name__


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
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return [], []
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
    return rows, rejects


def _row_market(row: dict) -> str | None:
    try:
        return get_profile(str(row.get("market"))).code
    except ValueError:
        return None


def candidates_from(rows: list[tuple[int, dict]], market: str | None = None
                    ) -> tuple[list[Candidate], list[tuple[int, str]]]:
    """Candidates (created_at order) and (line, reason) rejects for malformed TAKE rows. Swing rows are deduped on
    the fill session, intraday rows on the market-local date of created_at. A row whose market cannot be read is
    kept whatever the filter, so a malformed TAKE is always reported."""
    if market is not None:
        rows = [(n, r) for n, r in rows if _row_market(r) in (market, None)]
    line_of = {id(r): n for n, r in rows}
    intraday = [r for _, r in rows if r.get("mode") == Mode.INTRADAY.value]
    swing = [r for _, r in rows if r.get("mode") != Mode.INTRADAY.value]
    out: list[Candidate] = []
    rejects: list[tuple[int, str]] = []
    for group, session_of in ((swing, fill_session_of), (intraday, _local_date)):
        good, bad = load_candidates(group, session_of=session_of)
        out += good
        rejects += [(line_of[id(r)], f"malformed TAKE: {why}") for r, why in bad]
    out.sort(key=lambda c: (c.created_at, c.key))
    return out, rejects


# ---------------------------------------------------------------- cache

def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _candidate_record(c: Candidate) -> dict:
    return {"market": c.market, "symbol": c.symbol, "mode": c.mode.value, "entry": c.entry, "stop": c.stop,
            "target_1": c.target_1, "max_hold_bars": c.max_hold_bars, "cost_per_share": c.cost_per_share,
            "qty": c.qty, "created_at": c.created_at.isoformat(), "mc_expectancy_r": c.mc_expectancy_r,
            "history_expectancy_r": c.history_expectancy_r, "key": c.key}


def _entry(o: Outcome) -> dict:
    return {"candidate": _candidate_record(o.candidate),
            "outcome": {"status": o.status.value, "fill_price": o.fill_price, "fill_at": o.fill_at,
                        "exit_price": o.exit_price, "exit_at": o.exit_at, "realised_r": o.realised_r,
                        "mtm_r": o.mtm_r, "bars_held": o.bars_held, "note": o.note}}


def _well_formed(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    cand, out = entry.get("candidate"), entry.get("outcome")
    if not isinstance(cand, dict) or not isinstance(out, dict):
        return False
    return all(k in cand for k in _CANDIDATE_FIELDS) and out.get("status") in {s.value for s in FINAL}


def _outcome_from(c: Candidate, entry: dict) -> Outcome | None:
    """The cached Outcome for c, or None when the entry belongs to different inputs or is inconsistent."""
    cand, o = entry["candidate"], entry["outcome"]
    current = _candidate_record(c)
    for k in _CANDIDATE_FIELDS:
        a, b = cand.get(k), current[k]
        if type(a) is bool or a != b:
            return None
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
        target = base
        for i in range(1, 100):
            if not target.exists():
                break
            target = base.with_name(f"{base.name}-{i}")
        try:
            if target.exists():
                raise FileExistsError(f"{target} exists")
            os.rename(self.path, target)
        except OSError as e:
            self.writable = False  # never overwrite a corrupt file we could not keep a copy of
            self._note(f"scoreboard cache was {why} and could not be backed up ({_err(e)}); "
                       "results recomputed, cache left untouched")
            return
        self.dirty = True
        self._note(f"scoreboard cache was {why}; backed up to {target.name}; rebuilt")

    def _load(self, now: datetime) -> None:
        try:
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
        entry = self.entries.get(c.key)
        if entry is None:
            return None
        o = _outcome_from(c, entry)
        if o is None:  # stale or inconsistent: forget it; a fresh final result replaces it
            del self.entries[c.key]
            self.dirty = True
        return o

    def put(self, o: Outcome) -> None:
        if o.status not in FINAL:
            return
        entry = _entry(o)
        if self.entries.get(o.candidate.key) != entry:
            self.entries[o.candidate.key] = entry
            self.dirty = True

    def save(self) -> None:
        if not self.dirty or not self.writable:
            return
        payload = json.dumps({"version": SCOREBOARD_VERSION, "outcomes": self.entries}, indent=1, sort_keys=True,
                             allow_nan=False)
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            tmp = None
            os.chmod(self.path, 0o600)
            self.dirty = False
        except OSError as e:
            self._note(f"scoreboard cache could not be written ({_err(e)}); results are still correct")
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# ---------------------------------------------------------------- replay

def _replay(c: Candidate, fn, *args, **kw) -> Outcome:
    try:
        return fn(c, *args, **kw)
    except Exception as e:  # noqa: BLE001 - odd bars must mark the trade unavailable, never drop or crash
        return unavailable(c, f"could not replay: {_err(e)}")


def _resolve_group(cs: list[Candidate], mode: Mode, now: datetime, load_daily, load_intraday) -> list[Outcome]:
    """Outcomes for uncached candidates of one (market, symbol, mode); the bars are loaded at most once."""
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
                for i in todo:
                    out[i] = unavailable(cs[i], f"intraday bars unavailable: {_err(e)}")
            else:
                for i in todo:
                    c = cs[i]
                    flat_by = cal.flat_by_for(profile, _local_date(c.market, c.created_at))
                    out[i] = _replay(c, resolve_intraday, bars, flat_by=flat_by, now=now)
    else:
        closed = cal.is_trading_day(profile, today) and now_l.time() >= cal.session_close_for(profile, today)
        try:
            daily = load_daily(profile, symbol)
        except Exception as e:  # noqa: BLE001
            for i, c in enumerate(cs):
                out[i] = unavailable(c, f"daily bars unavailable: {_err(e)}")
        else:
            for i, c in enumerate(cs):
                try:
                    fs = fill_session_of(c.market, c.created_at)
                except Exception as e:  # noqa: BLE001 - broken calendar data: unavailable, loudly
                    out[i] = unavailable(c, f"fill session unknown: {_err(e)}")
                    continue
                out[i] = _replay(c, resolve_swing, daily, fill_session=fs, now=now, session_closed_today=closed)
    return [out[i] for i in range(len(cs))]


def build_scoreboard(market: str | None, *, now: datetime, audit_path: Path, cache_path: Path,
                     on_warning: Warn, load_daily, load_intraday) -> ScoreboardResult:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    want = None if market is None or str(market).strip().upper() == "ALL" else get_profile(market).code
    rows, rejects = read_audit(audit_path)
    cands, bad = candidates_from(rows, want)
    rejects = sorted(rejects + bad)
    cache = ScoreboardCache(cache_path, now, on_warning)
    outcomes: list[Outcome | None] = [cache.get(c) for c in cands]
    groups: dict[tuple[str, str, Mode], list[int]] = {}
    for i, c in enumerate(cands):
        if outcomes[i] is None:
            groups.setdefault((c.market, c.symbol, c.mode), []).append(i)
    for (_, _, mode), idx in groups.items():
        for i, o in zip(idx, _resolve_group([cands[i] for i in idx], mode, now, load_daily, load_intraday)):
            outcomes[i] = o
            cache.put(o)
    cache.save()
    final = [o for o in outcomes if o is not None]
    if len(final) != len(cands):  # every candidate is reported, never silently dropped
        raise RuntimeError("scoreboard lost a candidate; refusing to show a partial record")
    return ScoreboardResult(final, summarize(final), rejects, cache.note)
