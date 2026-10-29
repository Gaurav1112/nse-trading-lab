"""The ORB-short edge gate: the pooled ORB-short history over the eligible universe, HOSTILE days only, at 2x
slippage, and its once-a-day cache. A short may be a desk TAKE only when this is proven (checks.short_edge_check).

Deterministic: no random draws; trades are pooled in (symbol, day) order and averaged with math.fsum.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from nse_backtest import tape_monitor

from .markets import MarketProfile
from .models import SHORT_EDGE_MIN_TRADES, ShortEdge
from .nse_lists import _atomic_write
from .plan import SHORT_RR_T1
from .timemachine import SHORT_SLIPPAGE_MULT, short_cost_fn, short_trades

MIN_TRADES = SHORT_EDGE_MIN_TRADES
CACHE_FILE = "desk_shortcheck.json"
CACHE_VERSION = 1
_REGIME_MAX_GAP_DAYS = 7  # an index bar older than this before day d leaves d unclassified (stale index)

__all__ = ["ShortEdge", "MIN_TRADES", "CACHE_FILE", "pooled_short_edge", "regime_by_day", "save_edge", "load_edge",
           "edge_line"]


def regime_by_day(index_df: pd.DataFrame | None, days: Iterable[date]) -> dict[date, str]:
    """The market regime of each trading day in ``days``, as the desk would have seen it before that day's open:
    tape_monitor.assess_tape (the live classifier) on the index's daily bars strictly BEFORE the day (day d's own
    bar closes after an intraday entry, so it is never used; nothing later is either). A day is left out when the
    index has too little history before it (assess_tape needs 200 bars) or its last bar before the day is more than
    a week old (a stale index is not a regime)."""
    out: dict[date, str] = {}
    if index_df is None or len(index_df) == 0:
        return out
    idx = pd.DatetimeIndex(index_df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    norm = idx.normalize()
    for d in sorted(set(days)):
        cut = pd.Timestamp(d)
        prior = index_df[norm < cut]
        if len(prior) == 0 or (cut - norm[norm < cut][-1]) > pd.Timedelta(days=_REGIME_MAX_GAP_DAYS):
            continue
        tape = tape_monitor.assess_tape(prior)
        if tape is not None:
            out[d] = tape.regime
    return out


def pooled_short_edge(profile: MarketProfile, frames15: Mapping[str, pd.DataFrame | None],
                      regime_by_day: Mapping[date, str], *, slippage_mult: float = SHORT_SLIPPAGE_MULT) -> ShortEdge:
    """Every ORB short the desk's live rule would have taken on a HOSTILE day across ``frames15`` (the eligible
    universe's 15-minute bars; each frame's latest session is left out), at ``slippage_mult`` x the slippage, pooled.
    proven = at least 200 trades and a positive expectancy. A frame that is missing or cannot be replayed is
    skipped (it adds no trades: it can only make proving harder)."""
    if profile.code != "NSE":
        raise ValueError("ORB shorts are NSE only")
    cost = short_cost_fn(profile, slippage_mult)
    rs: list[float] = []
    for sym in sorted(frames15):
        df = frames15[sym]
        if df is None:
            continue
        try:
            trades = short_trades(df, profile, regime_by_day, rr=SHORT_RR_T1, cost_fn=cost)
        except (KeyError, ValueError, TypeError):
            continue
        rs += [t.r for t in trades if math.isfinite(t.r)]
    if not rs:
        return ShortEdge.from_trades(0, None)
    return ShortEdge.from_trades(len(rs), math.fsum(rs) / len(rs))


def edge_line(e: ShortEdge) -> str:
    """One line: the numbers and whether they prove the edge."""
    exp = "no expectancy yet" if e.expectancy_r is None else f"{e.expectancy_r:+.2f}R"
    head = f"{e.n} trades, {exp} at {SHORT_SLIPPAGE_MULT:g}× slippage, HOSTILE days"
    if e.proven:
        return f"{head}: proven"
    return f"{head}: not proven (needs at least {MIN_TRADES} trades and a positive R)"


# ---------------------------------------------------------------- the daily cache

def save_edge(path: Path, profile: MarketProfile, day: date, edge: ShortEdge, *, symbols: int,
              now: datetime | None = None) -> None:
    """Write today's pooled figure (atomically, owner-only directory assumed: audit.data_dir)."""
    path = Path(path)
    body = {"version": CACHE_VERSION, "market": profile.code, "day": day.isoformat(), "n": edge.n,
            "expectancy_r": edge.expectancy_r, "proven": edge.proven, "slippage_mult": SHORT_SLIPPAGE_MULT,
            "min_trades": MIN_TRADES, "symbols": int(symbols),
            "computed_at": (now or datetime.now()).isoformat()}
    _atomic_write(path, json.dumps(body, indent=2, allow_nan=False) + "\n")


def _bad(why: str) -> tuple[None, str]:
    return None, f"cache unreadable ({why}): run shortcheck again"


def load_edge(path: Path, profile: MarketProfile, day: date) -> tuple[ShortEdge | None, str]:
    """(edge, "") from a valid cache for ``day``, else (None, why). Never trusts the stored ``proven``: it must equal
    the rule applied to the stored numbers, and the rule's constants (2x slippage, 200 trades) must match."""
    path = Path(path)
    if not path.exists():
        return None, "not computed today: run shortcheck"
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return _bad(type(e).__name__)
    if not isinstance(d, dict) or d.get("version") != CACHE_VERSION:
        return _bad("unknown format")
    if d.get("market") != profile.code:
        return _bad("another market")
    if d.get("slippage_mult") != SHORT_SLIPPAGE_MULT or d.get("min_trades") != MIN_TRADES:
        return _bad("computed under another rule")
    if d.get("day") != day.isoformat():
        return None, f"not computed today (last run {d.get('day')}): run shortcheck"
    n, exp = d.get("n"), d.get("expectancy_r")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        return _bad("trade count")
    if exp is None:
        if n != 0:
            return _bad("expectancy")
    elif isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
        return _bad("expectancy")
    edge = ShortEdge.from_trades(n, exp)
    if d.get("proven") is not edge.proven:
        return _bad("proven flag does not match the numbers")
    return edge, ""



def cached_symbols(path: Path) -> int | None:
    """How many symbols the cached figure pooled (for display only); None when unreadable."""
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8")).get("symbols")
    except (OSError, UnicodeDecodeError, ValueError, AttributeError):
        return None
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None
