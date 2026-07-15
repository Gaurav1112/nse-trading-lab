from __future__ import annotations
from datetime import datetime, timezone, timedelta
from pipeline.fetch import LTPQuote

IST = timezone(timedelta(hours=5, minutes=30))


def is_market_hours(now: datetime) -> bool:
    """NSE cash market: 09:15–15:30 IST, Mon–Fri (no holiday awareness in v1)."""
    now_ist = now.astimezone(IST)
    if now_ist.weekday() >= 5:
        return False
    open_ = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    close_ = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_ <= now_ist <= close_


def staleness_gate(
    quotes: dict[str, LTPQuote],
    max_age_min: int = 20,
    now: datetime | None = None,
) -> tuple[dict[str, LTPQuote], list[str]]:
    """Split quotes into (fresh, list_of_stale_symbols)."""
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=max_age_min)
    fresh: dict[str, LTPQuote] = {}
    stale: list[str] = []
    for sym, q in quotes.items():
        if q.ts >= cutoff:
            fresh[sym] = q
        else:
            stale.append(sym)
    return fresh, stale


def dual_source_gate(
    primary: dict[str, LTPQuote],
    reference: dict[str, LTPQuote],
    tolerance_pct: float = 0.5,
) -> list[str]:
    """Return symbols where |primary - reference| / reference * 100 > tolerance_pct."""
    divergent: list[str] = []
    for sym, p_quote in primary.items():
        r_quote = reference.get(sym)
        if r_quote is None or r_quote.ltp <= 0:
            continue
        diff_pct = abs(p_quote.ltp - r_quote.ltp) / r_quote.ltp * 100
        if diff_pct > tolerance_pct:
            divergent.append(sym)
    return divergent
