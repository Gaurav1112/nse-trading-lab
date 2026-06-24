"""Intraday-specific safety gates. Without these, the Intraday Scanner is
"raw RSI extreme → Kite link" with zero confirmation. With them, signals
require: volume confirmation, reversal candle, not-in-sector-capitulation,
not-stale-bar, not-in-volatile-window.

Each gate returns (passed, reason). Callers chain them and only surface
hits that pass every gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable

import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))


# ── 1. Reversal candle: oversold ≠ bouncing. ────────────────────────────
def reversal_candle_confirmed(df: pd.DataFrame) -> tuple[bool, str]:
    """The last 15-min bar must show a reversal pattern:
    - close > open (green candle)
    - lower wick is at least 30% of the bar range (long lower shadow = rejection)
    Otherwise the stock is still falling; RSI<15 is capitulation, not bounce.
    """
    if df is None or len(df) < 2 or "Close" not in df.columns:
        return False, "Reversal: insufficient data"
    last = df.iloc[-1]
    o, h, l, c = float(last["Open"]), float(last["High"]), float(last["Low"]), float(last["Close"])
    bar_range = h - l
    if bar_range <= 0:
        return False, "Reversal: zero-range bar (likely halted)"
    green = c > o
    lower_wick = min(o, c) - l
    wick_pct = lower_wick / bar_range
    if green and wick_pct >= 0.30:
        return True, f"Reversal confirmed (green +{((c-o)/o*100):.2f}%, lower wick {wick_pct*100:.0f}% of range)"
    if not green:
        return False, f"No reversal: bar still red ({((c-o)/o*100):.2f}%) — still falling"
    return False, f"No reversal: weak wick ({wick_pct*100:.0f}% < 30%) — not enough rejection"


# ── 2. Volume confirmation: real bounces happen on volume. ──────────────
def volume_confirmed(df: pd.DataFrame, multiplier: float = 1.5) -> tuple[bool, str]:
    """Bounce bar volume must be at least `multiplier`x the 20-bar average.
    Mean-reversion without volume = continuation, not reversal.
    """
    if df is None or len(df) < 21 or "Volume" not in df.columns:
        return False, "Volume: insufficient data"
    last_vol = float(df["Volume"].iloc[-1])
    avg20 = float(df["Volume"].tail(21).iloc[:-1].mean())
    if avg20 <= 0:
        return False, "Volume: invalid baseline"
    ratio = last_vol / avg20
    if ratio >= multiplier:
        return True, f"Volume confirmed ({ratio:.2f}x 20-bar avg)"
    return False, f"Weak volume ({ratio:.2f}x < {multiplier}x) — bounce lacks conviction"


# ── 3. Sector capitulation: don't catch knives when whole sector is bleeding. ──
def detect_sector_capitulation(
    hit_symbols: Iterable[str], min_names: int = 3,
) -> dict[str, str]:
    """Returns {symbol: warning_text} for symbols whose sector has ≥`min_names`
    candidates in the current oversold list. When 3+ IT names are simultaneously
    showing extreme RSI, the move is sector-wide capitulation, not stock-specific
    mean-reversion. Mean-reversion edge collapses in this regime.

    The user's 2026-06-19 loss was exactly this pattern: INFY/TCS/HCLTECH all
    at RSI<15 simultaneously = IT sector capitulating, not stock-specific
    oversold conditions.
    """
    from nse_backtest.sectors import sector_of
    by_sector: dict[str, list[str]] = {}
    for sym in hit_symbols:
        s = sector_of(sym)
        if s == "Unclassified":
            continue
        by_sector.setdefault(s, []).append(sym)
    warnings: dict[str, str] = {}
    for sector, names in by_sector.items():
        if len(names) >= min_names:
            warn = (
                f"🔻 {sector} sector capitulation: {len(names)} names "
                f"({', '.join(names)}) all oversold. Likely not mean-reversion — "
                "sector-wide selling continues."
            )
            for sym in names:
                warnings[sym] = warn
    return warnings


# ── 4. Bar age: refuse stale signals. ──────────────────────────────────
def bar_is_fresh(last_bar_ts: pd.Timestamp, max_age_min: int = 30) -> tuple[bool, str]:
    """Reject signals from bars older than max_age_min minutes during market hours."""
    if last_bar_ts is None:
        return False, "Bar age: unknown timestamp"
    try:
        if hasattr(last_bar_ts, "tz") and last_bar_ts.tz:
            last_ts = last_bar_ts.tz_convert(IST)
            now = datetime.now(tz=IST)
        else:
            # Assume tz-naive timestamps are already in IST (or close enough
            # for the age computation — tests + some yfinance returns are naive)
            last_ts = last_bar_ts
            now = datetime.now(tz=IST).replace(tzinfo=None)
    except Exception:
        return False, "Bar age: timezone issue"
    age = (now - last_ts).total_seconds() / 60
    if age <= max_age_min:
        return True, f"Bar age {int(age)}m (fresh)"
    return False, f"Bar age {int(age)}m > {max_age_min}m — signal too stale to act"


# ── 5. Time-of-day filter: avoid the open and close volatility windows. ──
def good_time_of_day_to_enter() -> tuple[bool, str]:
    """Block new intraday entries during the most volatile windows:
    - 09:15-09:30: opening 15 min — spreads 3x median, fakeouts dominate
    - 15:00-15:30: closing 30 min — squareoff volume + dump risk; MIS auto-flat at 15:15
    """
    now = datetime.now(tz=IST)
    if now.weekday() >= 5:
        return False, "Market closed (weekend)"
    hm = (now.hour, now.minute)
    if hm < (9, 15):
        return False, "Pre-open — limit orders route to call auction"
    if hm > (15, 30):
        return False, "Market closed"
    if hm <= (9, 30):
        return False, "Opening 15 min — spreads 3x median, avoid new entries"
    if hm >= (15, 0):
        return False, "Last 30 min — MIS auto-squareoff at 15:15, avoid new entries"
    return True, f"Time window OK ({now.strftime('%H:%M IST')})"


# ── 6. Auto-squareoff countdown. ───────────────────────────────────────
def time_to_mis_squareoff() -> tuple[int, str]:
    """Returns (minutes_remaining, formatted_text). MIS positions force-close at 15:15."""
    now = datetime.now(tz=IST)
    squareoff = now.replace(hour=15, minute=15, second=0, microsecond=0)
    if now > squareoff:
        return 0, "Auto-squareoff already passed today"
    delta_min = int((squareoff - now).total_seconds() / 60)
    return delta_min, f"{delta_min} min to MIS auto-squareoff (15:15 IST)"


@dataclass
class IntradayConfirmation:
    """Composite verdict on an intraday signal — all gates must pass."""
    passed_all: bool
    gates: dict[str, tuple[bool, str]]  # gate_name → (passed, reason)

    @property
    def failed_gates(self) -> list[str]:
        return [name for name, (ok, _) in self.gates.items() if not ok]

    @property
    def summary(self) -> str:
        if self.passed_all:
            return "✅ All gates passed"
        return f"❌ {len(self.failed_gates)} gates failed: " + ", ".join(self.failed_gates)


def confirm_intraday_long(
    df: pd.DataFrame, last_bar_ts: pd.Timestamp,
    *, in_sector_capitulation: bool = False,
) -> IntradayConfirmation:
    """Run every gate on a single candidate. Caller passes in_sector_capitulation
    (computed across the universe-level scanner output)."""
    gates: dict[str, tuple[bool, str]] = {
        "Time of day": good_time_of_day_to_enter(),
        "Bar fresh": bar_is_fresh(last_bar_ts),
        "Reversal candle": reversal_candle_confirmed(df),
        "Volume": volume_confirmed(df),
        "Not sector-wide capitulation":
            (not in_sector_capitulation,
             "Sector-wide capitulation detected" if in_sector_capitulation
             else "Stock-specific oversold (not sector-wide)"),
    }
    return IntradayConfirmation(
        passed_all=all(ok for ok, _ in gates.values()),
        gates=gates,
    )
