"""Portfolio-level risk governor — owned by Kavya Rao (B.6).

Three guards:
  1. Max concurrent open positions (default 5)
  2. Cooling-off period after weekly drawdown breach (default 5%)
  3. Audit log of every verdict the user sees (for post-mortem)

All guards are CONFIGURABLE via st.session_state so the user can re-tune
in pages/11_Settings.py without code change.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Paths — committed in .gitignore (positions.json + trade_journal.json + audit_log.jsonl)
_REPO_ROOT = Path(__file__).resolve().parent.parent
_AUDIT_LOG_PATH = _REPO_ROOT / "audit_log.jsonl"

DEFAULTS = {
    "max_open_positions": 5,
    "weekly_dd_threshold_pct": 5.0,   # cooling-off triggers when last 7d realised loss > 5% of capital
    "cooling_off_days": 7,            # length of the cooling-off period after breach
}


@dataclass
class GovernorVerdict:
    can_trade: bool
    open_positions: int
    max_open_positions: int
    weekly_pnl_pct: float
    weekly_dd_threshold_pct: float
    cooling_off_active: bool
    cooling_off_until: Optional[str]   # ISO date when cooling-off ends, or None
    reasons: list[str]


def _setting(name: str, fallback) -> float | int:
    """Read a tunable from st.session_state with a default fallback.

    Imported lazily so this module is importable outside Streamlit (e.g., tests).
    """
    try:
        import streamlit as st
        return st.session_state.get(name, DEFAULTS.get(name, fallback))
    except Exception:
        return DEFAULTS.get(name, fallback)


def count_open_positions(positions: list[dict]) -> int:
    """An open position is any saved dict that has not been marked closed."""
    return sum(1 for p in positions if not p.get("closed_date"))


def at_position_limit(positions: list[dict]) -> bool:
    return count_open_positions(positions) >= int(_setting("max_open_positions", 5))


def weekly_pnl_pct(journal: list[dict], capital: float, days: int = 7) -> float:
    """Sum closed-trade P&L over the last `days` calendar days, as % of capital."""
    if capital <= 0:
        return 0.0
    cutoff = datetime.now() - timedelta(days=days)
    total = 0.0
    for trade in journal:
        # Accept either 'closed_date' or 'exit_date' or 'date'
        date_str = trade.get("closed_date") or trade.get("exit_date") or trade.get("date")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(str(date_str)[:10])
        except (ValueError, TypeError):
            continue
        if d < cutoff:
            continue
        pnl = trade.get("pnl", trade.get("net_pnl", 0.0))
        try:
            total += float(pnl)
        except (TypeError, ValueError):
            continue
    return (total / capital) * 100.0


def is_cooling_off(journal: list[dict], capital: float) -> tuple[bool, Optional[str]]:
    """Cooling-off when last `cooling_off_days` realised P&L < -threshold% of capital.

    Returns (active, until_iso_date) — until_iso_date is None when inactive.
    """
    threshold = float(_setting("weekly_dd_threshold_pct", 5.0))
    window = int(_setting("cooling_off_days", 7))
    pnl_pct = weekly_pnl_pct(journal, capital, days=window)
    if pnl_pct < -threshold:
        # Cooling-off extends `window` days from now.
        until = (datetime.now() + timedelta(days=window)).date().isoformat()
        return True, until
    return False, None


def assess(positions: list[dict], journal: list[dict], capital: float) -> GovernorVerdict:
    """Single entry point for the UI. Returns the full risk verdict."""
    max_open = int(_setting("max_open_positions", 5))
    threshold = float(_setting("weekly_dd_threshold_pct", 5.0))
    open_n = count_open_positions(positions)
    pnl = weekly_pnl_pct(journal, capital)
    cool, until = is_cooling_off(journal, capital)
    reasons: list[str] = []
    can_trade = True

    if open_n >= max_open:
        can_trade = False
        reasons.append(
            f"At max concurrent positions ({open_n}/{max_open}) — close one before opening another"
        )

    if cool:
        can_trade = False
        reasons.append(
            f"Cooling-off period active until {until} — last {int(_setting('cooling_off_days', 7))}d "
            f"realised P&L {pnl:+.1f}% breached the {threshold:.1f}% drawdown threshold"
        )

    if can_trade:
        reasons.append(
            f"Risk envelope clear: {open_n}/{max_open} positions, {pnl:+.1f}% weekly P&L (threshold {threshold:.1f}%)"
        )

    return GovernorVerdict(
        can_trade=can_trade, open_positions=open_n, max_open_positions=max_open,
        weekly_pnl_pct=pnl, weekly_dd_threshold_pct=threshold,
        cooling_off_active=cool, cooling_off_until=until, reasons=reasons,
    )


def log_verdict(
    symbol: str, verdict: str, score: float, win_probability: float,
    tape_regime: str, engine: str = "v2",
) -> None:
    """Append one line to audit_log.jsonl for every verdict the user sees.

    Used for post-mortem analysis: did the engine flag X on day Y? What did
    the user do? Audit trail for compliance + behavioral review.
    """
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "symbol": symbol, "verdict": verdict,
        "score": round(float(score), 2),
        "win_probability": round(float(win_probability), 2),
        "tape_regime": tape_regime, "engine": engine,
    }
    try:
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # Audit log failure must never break the user's trading flow.
