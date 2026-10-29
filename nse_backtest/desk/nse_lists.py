"""NSE F&O lists: the security-in-ban list and the F&O underlyings (market lots) list. Fetch, parse, cache.
Never raises from fetch."""
from __future__ import annotations

import os
import re
import tempfile
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

FNO_BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
FNO_LOTS_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
_ROW = re.compile(r"^\s*\d+\s*,\s*([A-Z0-9&\-]+)\s*$")


@dataclass(frozen=True)
class BanList:
    symbols: frozenset[str]
    status: str  # "ok" | "cached" | "stale" | "unavailable"
    as_of: str | None
    detail: str


def parse_fno_ban(text: str) -> frozenset[str]:
    lines = text.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    if "ban" not in first.lower():
        raise ValueError("unexpected ban list format")
    out = set()
    seen_header = False
    for ln in lines:
        if not ln.strip():
            continue
        if not seen_header:
            seen_header = True
            continue
        m = _ROW.match(ln)
        if not m:
            raise ValueError(f"unexpected ban list row: {ln!r}")
        out.add(m.group(1))
    if not out and "nil" not in first.lower():
        raise ValueError("ban list has no rows and header does not say NIL")
    return frozenset(out)


_MAX_BYTES = 1_000_000
_ALLOWED_HOST = "nsearchives.nseindia.com"


def _default_fetcher(url: str) -> str:
    what = "F&O list" if url == FNO_LOTS_URL else "ban list"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        final = urlparse(resp.geturl())
        if final.scheme != "https" or final.hostname != _ALLOWED_HOST:
            raise ValueError(f"{what} fetch ended off NSE ({final.scheme}://{final.hostname})")
        body = resp.read(_MAX_BYTES + 1)
        if len(body) > _MAX_BYTES:
            raise ValueError(f"{what} response too large (over {_MAX_BYTES} bytes): refusing a truncated list")
        return body.decode("utf-8")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_fno_ban(cache_dir: Path, today: date,
                  fetcher: Callable[[str], str] | None = None) -> BanList:
    cache_dir = Path(cache_dir)
    fetch = fetcher or _default_fetcher
    try:
        text = fetch(FNO_BAN_URL)
        symbols = parse_fno_ban(text)
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write(cache_dir / "fo_secban.csv", text)
            _atomic_write(cache_dir / "fo_secban.date", today.isoformat())
        except OSError as cerr:
            return BanList(symbols, "ok", today.isoformat(),
                           f"Fetched live from NSE; cache write failed ({cerr})")
        return BanList(symbols, "ok", today.isoformat(), "Fetched live from NSE")
    except Exception as err:  # noqa: BLE001 - never raises by contract
        try:
            cached_text = (cache_dir / "fo_secban.csv").read_text(encoding="utf-8")
            cached_date = (cache_dir / "fo_secban.date").read_text(encoding="utf-8").strip()
            symbols = parse_fno_ban(cached_text)
            status = "cached" if cached_date == today.isoformat() else "stale"
            return BanList(symbols, status, cached_date,
                           f"Live fetch failed ({err}); using list cached on {cached_date}")
        except Exception:  # noqa: BLE001
            return BanList(frozenset(), "unavailable", None, str(err))


# ---------------------------------------------------------------- F&O underlyings (who may be shorted intraday)

_SYMBOL = re.compile(r"^[A-Z0-9&\-]+$")
FNO_LOTS_MAX_AGE = timedelta(days=7)  # the list changes rarely; an older cached copy is not trusted


def parse_fno_underlyings(text: str) -> frozenset[str]:
    """The SYMBOL column of NSE's fo_mktlots.csv (index and stock underlyings). Repeated section headers
    ("Derivatives on Individual Securities, Symbol, ...") are skipped. Anything unexpected raises ValueError."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty F&O list")
    header = [c.strip().upper() for c in lines[0].split(",")]
    if "SYMBOL" not in header:
        raise ValueError("unexpected F&O list format (no SYMBOL column)")
    col = header.index("SYMBOL")
    out = set()
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) <= col:
            raise ValueError(f"unexpected F&O list row: {ln!r}")
        sym = cells[col].upper()
        if sym == "SYMBOL":  # a section header row
            continue
        if not _SYMBOL.match(sym):
            raise ValueError(f"unexpected F&O list row: {ln!r}")
        out.add(sym)
    if not out:
        raise ValueError("F&O list has no symbols")
    return frozenset(out)


def fetch_fno_underlyings(cache_dir: Path, today: date,
                          fetch: Callable[[str], str] | None = None) -> frozenset[str] | None:
    """The F&O underlyings, live from NSE (cached atomically); on a failed fetch the cached copy if it is at most
    FNO_LOTS_MAX_AGE old. None when neither is available: the caller must treat F&O eligibility as unverified
    (fail closed). Never raises."""
    cache_dir = Path(cache_dir)
    fetch = fetch or _default_fetcher
    try:
        text = fetch(FNO_LOTS_URL)
        symbols = parse_fno_underlyings(text)
    except Exception:  # noqa: BLE001 - never raises by contract
        try:
            cached = (cache_dir / "fo_mktlots.csv").read_text(encoding="utf-8")
            as_of = date.fromisoformat((cache_dir / "fo_mktlots.date").read_text(encoding="utf-8").strip())
            if not (timedelta(0) <= today - as_of <= FNO_LOTS_MAX_AGE):
                return None
            return parse_fno_underlyings(cached)
        except Exception:  # noqa: BLE001
            return None
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(cache_dir / "fo_mktlots.csv", text)
        _atomic_write(cache_dir / "fo_mktlots.date", today.isoformat())
    except OSError:
        pass  # the live list is still good; the next failed fetch just has no cache
    return symbols
