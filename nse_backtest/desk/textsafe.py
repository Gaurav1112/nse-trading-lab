"""Safe display of fetched or exception text: no home directory or file paths, no Markdown, no bad links.

One helper for the research panel, the trade card, the intraday scan and the Desk page.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

MAX_LEN = 300
# http(s) URLs are kept whole; any other scheme://... and any absolute / home-relative / drive path is a path.
_HTTP = r"https?://[^\s'\"]*"
_OTHER_URL = r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s'\"]*"
_PATH = r"(?<![\w.~])(?:[A-Za-z]:|~)?[\\/][^\s'\"]*"
_TOKEN_RE = re.compile(f"({_HTTP})|{_OTHER_URL}|{_PATH}")
_MD_RE = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")
_URL_BAD = set('<>"`')
_URL_SAFE = ":/?#@!$&'*+,;=%-._~"  # "(" and ")" are encoded: they would end a Markdown link target


def _home() -> str | None:
    try:
        h = str(Path.home())
    except (RuntimeError, KeyError, OSError):
        return None
    return h if h not in ("", "/") else None


def scrub(msg, limit: int = MAX_LEN) -> str:
    """``msg`` as text with the home directory redacted (literally, so a home with spaces is caught too) and every
    filesystem path or non-http(s) URL cut to its file name; http(s) URLs are kept whole. At most ``limit``
    characters."""
    text = str(msg)
    home = _home()
    if home:
        text = text.replace(home, "~")

    def one(m: re.Match) -> str:
        if m.group(1):  # http(s): kept whole
            return m.group(0)
        t = m.group(0)
        if "://" in t:
            t = t.split("://", 1)[1]
        return re.split(r"[\\/]", t.rstrip("/\\"))[-1]
    return _TOKEN_RE.sub(one, text)[:limit]


def md_escape(text) -> str:
    """Backslash-escape Markdown punctuation (``$`` and ``:`` too: no maths, no autolinks) so text is shown as
    text, never as links or formatting."""
    return _MD_RE.sub(r"\\\1", str(text))


def safe_url(url) -> str | None:
    """An http(s) URL safe inside a Markdown link, or None. Rejects any control or space character (<= 0x20, and
    DEL) and any of ``<>"` ``; percent-quotes everything else outside the URL-safe set (parentheses included)."""
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        return None
    if any(ord(c) <= 0x20 or ord(c) == 0x7F or c in _URL_BAD for c in url):
        return None
    return quote(url, safe=_URL_SAFE)


def shown(msg) -> str:
    """scrub then md_escape: for an exception or fetched reason inside st.error / st.warning text."""
    return md_escape(scrub(msg))
