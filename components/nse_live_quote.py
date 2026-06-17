"""NSE-direct live equity quote (no auth, no paid feed).

Replaces yfinance fast_info for CMP display on the Picks/Analyze pages.
yfinance fast_info is known to be stale and occasionally split-adjusted
in confusing ways for Indian symbols. NSE's open endpoint at
https://www.nseindia.com/api/quote-equity?symbol=X gives the actual
last-traded price for any Nifty 50 / Nifty 100 cash equity.

Caveats:
  - NSE rate-limits anonymous clients (~1 req/sec). We cache for 60s.
  - NSE blocks bots without a session cookie; we prime it by visiting
    the homepage first.
  - From Streamlit Cloud's shared egress, NSE may rate-limit harder
    than from your laptop. We fall back to yfinance on any failure.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

_log = logging.getLogger(__name__)

_BASE = "https://www.nseindia.com"
_QUOTE_PATH = "/api/quote-equity"
_TIMEOUT = 5.0
_TTL_SEC = 60

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": _BASE + "/get-quotes/equity",
}

_session: Optional[requests.Session] = None
_cache: dict[str, tuple[float, float]] = {}


def _get_session() -> requests.Session:
    """Returns a primed requests.Session with NSE's anti-bot cookie set."""
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(_HEADERS)
        try:
            s.get(_BASE, timeout=_TIMEOUT)  # primes cookie jar
        except Exception as e:
            _log.debug("NSE session prime failed: %s", e)
        _session = s
    return _session


def get_nse_quote(symbol: str) -> tuple[Optional[float], Optional[float]]:
    """Return (last_price, prev_close) for an NSE cash equity symbol.

    `symbol` is the NSE ticker without ".NS" suffix (e.g. "RELIANCE").
    Returns (None, None) on any failure — caller should fall back to yfinance.
    """
    sym = symbol.replace(".NS", "").replace(".BO", "").upper()
    now = time.time()
    if sym in _cache:
        cached_ts, cached_last = _cache[sym][0], _cache[sym][1]
        if now - cached_ts < _TTL_SEC:
            return cached_last, None  # prev_close cache not kept; not critical

    try:
        s = _get_session()
        r = s.get(_BASE + _QUOTE_PATH, params={"symbol": sym}, timeout=_TIMEOUT)
        if r.status_code != 200:
            return None, None
        payload = r.json()
        info = payload.get("priceInfo") or {}
        last = info.get("lastPrice")
        prev = info.get("previousClose")
        last_f = float(last) if last not in (None, "") else None
        prev_f = float(prev) if prev not in (None, "") else None
        if last_f is not None:
            _cache[sym] = (now, last_f)
        return last_f, prev_f
    except (requests.RequestException, ValueError, KeyError) as e:
        _log.debug("NSE quote failed for %s: %s", sym, e)
        return None, None
