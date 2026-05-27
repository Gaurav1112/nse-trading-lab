"""Security helpers shared across all pages.

Centralises SYMBOL_RE, symbol validation, and CSV-injection protection so
there is one authoritative implementation instead of per-page duplicates.
"""
from __future__ import annotations

import re
import pandas as pd

SYMBOL_RE = re.compile(r"^[A-Z0-9&.\-^]{1,20}$")

# Characters that trigger formula execution in Excel / Google Sheets
_FORMULA_PREFIXES = ("=", "+", "-", "@", "|", "%")


def _validate_sym_ui(sym: str) -> str:
    """Validate a user-entered symbol; return upper-cased form or raise ValueError."""
    s = (sym or "").upper().strip()
    if not SYMBOL_RE.match(s):
        raise ValueError(f"Invalid symbol: {sym!r}")
    return s


def _safe_csv(df: pd.DataFrame | None) -> str:
    """Return a CSV string with formula-injection protection on all string cells."""
    if df is None or df.empty:
        return ""
    safe = df.copy()
    for col in safe.select_dtypes(include="object").columns:
        safe[col] = safe[col].apply(
            lambda v: f"'{v}" if isinstance(v, str) and v.startswith(_FORMULA_PREFIXES) else v
        )
    return safe.to_csv(index=False)


def _safe_filename(name: str, fallback: str = "export") -> str:
    """Sanitise a string for use as a filename (no path separators or dots)."""
    clean = re.sub(r"[^A-Za-z0-9_\-]", "_", name)
    return clean or fallback
