"""Research panel adapters. DISPLAY ONLY: nothing here feeds a TAKE/WAIT/SKIP verdict.

US symbols run the vendored day-trader-skill tools as isolated subprocesses.
NSE symbols map data the desk already loaded (C7 injects it; no network here).
Any failure becomes `Unavailable(reason)`, which the panel shows.

Adapted from github.com/Jakeschincariol/day-trader-skill (MIT).

Field mapping (read from the vendored scripts)
  news.py --tickers SYM --json --dry-run --hours 168 --no-moves
    {"checked", "moves", "items": [...], "notes": [str]}; exit 0, or 1 when nothing is new (JSON
    still printed); 2 on bad input. item: {kind: "headline"|"filing", form, title, link, time (ISO|null)}
      kind "headline"                -> ResearchItem(kind="headline", title, url=link, when=time)
      kind "filing" and form "8-K"   -> ResearchItem(kind="8-K", ...); other forms are dropped
      notes                          -> Research.notes
    --dry-run skips the state write; the temp cwd makes the relative default paths harmless anyway.
  facts.py SYM --json -> list [{ticker, result{rows[{end,...}]}, filings{report{url}, earnings_8k{url,filed}},
    changes[str], note}]; exit 0, 1 (some tickers failed) or 2 (no UA / lookup failure, stderr says why).
      changes[0]                     -> ResearchItem(kind="earnings", title, when=rows[0].end,
                                        url=earnings_8k.url or report.url)
      note                           -> Research.notes
Both need SEC_USER_AGENT in the environment; it is passed through, never defaulted.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_TIMEOUT = 10.0
_US_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")  # same rule as news.py validate_ticker
_SKILLS = Path(__file__).resolve().parents[2] / ".claude" / "skills"
NEWS_PY = _SKILLS / "trader-news" / "news.py"
FACTS_PY = _SKILLS / "trader-analyst" / "facts.py"
_PATH_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"]*|(?<![\w.])(?:[A-Za-z]:)?[\\/][^\s'\"]*")


@dataclass(frozen=True)
class ResearchItem:
    title: str
    url: str | None
    when: str | None
    kind: str  # "headline" | "8-K" | "earnings" | "dividend" | "split"


@dataclass(frozen=True)
class Research:
    source: str
    fetched_at: str
    items: tuple[ResearchItem, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class Unavailable:
    reason: str


def _scrub(text: str) -> str:
    """Drop directories from any path in text; keep file names."""
    def one(m: re.Match) -> str:
        t = m.group(0)
        return t if "://" in t else re.split(r"[\\/]", t.rstrip("/\\"))[-1]
    return _PATH_RE.sub(one, text)[:200]


_ENV_KEYS = ("PATH", "HOME", "SEC_USER_AGENT")


def run_skill(script: Path, args: list[str], *, timeout: float = DEFAULT_TIMEOUT,
              env: dict | None = None, ok_codes: tuple[int, ...] = (0,)) -> dict | Unavailable:
    """Run one vendored script isolated; return its parsed JSON (a list is wrapped as {"results": ...})."""
    script = Path(script)
    child_env = {k: os.environ[k] for k in _ENV_KEYS if k in os.environ}
    if env:
        child_env.update({k: v for k, v in env.items() if k in _ENV_KEYS})
    try:
        with tempfile.TemporaryDirectory() as cwd:
            proc = subprocess.run([sys.executable, "-I", str(script), *args], cwd=cwd, env=child_env,
                                  capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Unavailable(f"timed out after {timeout:g}s")
    except OSError as e:
        return Unavailable(_scrub(f"{script.name} could not start: {e.strerror or type(e).__name__}"))
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        data = None
    # ok_codes lets news.py's "exit 1, nothing new" through; callers must still check its notes.
    if proc.returncode in ok_codes and isinstance(data, (dict, list)):
        return data if isinstance(data, dict) else {"results": data}
    if proc.returncode != 0:
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        return Unavailable(_scrub(lines[-1].strip()) if lines else f"{script.name} exited {proc.returncode}")
    return Unavailable("bad output")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sec_ua() -> str | None:
    return (os.environ.get("SEC_USER_AGENT") or "").strip() or None


def us_research(symbol: str, *, runner=run_skill) -> Research | Unavailable:
    if not isinstance(symbol, str) or not _US_RE.fullmatch(symbol):
        return Unavailable("invalid US symbol")
    has_ua = _sec_ua() is not None
    notes: list[str] = []
    items: list[ResearchItem] = []

    headline_failed = False
    nargs = ["--tickers", symbol, "--json", "--dry-run", "--hours", "168", "--no-moves"]
    if not has_ua:
        nargs.append("--no-sec")
    news = runner(NEWS_PY, nargs, ok_codes=(0, 1))
    if not isinstance(news, Unavailable) and not news.get("items"):
        # news.py also exits 1 when every fetch failed; the failure is only in its notes.
        prefix = f"headlines for {symbol}:"
        failed = next((str(n) for n in news.get("notes") or [] if str(n).startswith(prefix)), None)
        if failed:
            news = Unavailable(_scrub(failed))
            headline_failed = True
    if isinstance(news, Unavailable):
        news_err: str | None = news.reason
        notes.append(f"headlines unavailable: {news.reason}" if headline_failed
                     else f"news unavailable: {news.reason}")
    else:
        news_err = None
        for it in news.get("items") or []:
            kind = it.get("kind")
            if kind == "headline":
                k = "headline"
            elif kind == "filing" and it.get("form") == "8-K":
                k = "8-K"
            else:
                continue
            items.append(ResearchItem(str(it.get("title") or ""), it.get("link") or None,
                                      it.get("time"), k))
        notes += [str(n) for n in news.get("notes") or []]

    facts_err: str | None = None
    if not has_ua:
        notes.append("SEC data unavailable: set SEC_USER_AGENT")
    else:
        facts = runner(FACTS_PY, [symbol, "--json"])
        if isinstance(facts, Unavailable):
            facts_err = facts.reason
            notes.append(f"earnings unavailable: {facts.reason}")
        else:
            for res in facts.get("results") or []:
                changes = res.get("changes") or []
                rows = (res.get("result") or {}).get("rows") or []
                fil = res.get("filings") or {}
                url = (fil.get("earnings_8k") or {}).get("url") or (fil.get("report") or {}).get("url")
                if changes:
                    items.append(ResearchItem(str(changes[0]), url or None,
                                              rows[0].get("end") if rows else None, "earnings"))
                if res.get("note"):
                    notes.append(str(res["note"]))

    if news_err is not None and (facts_err is not None or not has_ua):
        return Unavailable(news_err)
    return Research("US skills", _now(), tuple(items), tuple(notes))


def _nse_ok(symbol: str) -> bool:
    from nse_backtest.data import _validate_symbol  # the desk's existing NSE symbol validator
    try:
        _validate_symbol(symbol)
    except ValueError:
        return False
    return True


def _day(ts) -> str:
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]


def nse_research(symbol: str, *, earnings=None, actions=None) -> Research | Unavailable:
    if not _nse_ok(symbol):
        return Unavailable("invalid NSE symbol")
    if earnings is None and actions is None:
        return Unavailable("no earnings date or corporate-action data supplied")
    items: list[ResearchItem] = []
    notes: list[str] = []
    if isinstance(earnings, date):
        items.append(ResearchItem("Next earnings", None, earnings.isoformat(), "earnings"))
    else:
        notes.append("earnings date unknown")
    if actions is None:
        notes.append("dividends and splits not loaded")
    else:
        recent = []
        for ts, row in actions.iterrows():
            div = float(row.get("Dividends", 0) or 0)
            spl = float(row.get("Stock Splits", 0) or 0)
            if div > 0:
                recent.append(ResearchItem(f"Dividend {div:g} per share", None, _day(ts), "dividend"))
            if spl > 0:
                recent.append(ResearchItem(f"Split {spl:g}-for-1", None, _day(ts), "split"))
        items += sorted(recent, key=lambda i: i.when or "", reverse=True)[:6]
    return Research("NSE", _now(), tuple(items), tuple(notes))
