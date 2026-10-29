"""NSE F&O security-in-ban list: fetch, parse, cache. Never raises from fetch."""
from __future__ import annotations

import os
import re
import tempfile
import urllib.request
from urllib.parse import urlparse
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

FNO_BAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"
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
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        final = urlparse(resp.geturl())
        if final.scheme != "https" or final.hostname != _ALLOWED_HOST:
            raise ValueError(f"ban list fetch ended off NSE ({final.scheme}://{final.hostname})")
        return resp.read(_MAX_BYTES).decode("utf-8")


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
