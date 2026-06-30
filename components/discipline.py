"""Discipline metrics — engine adherence, consecutive-loss cooling-off, streak counter.

A behavioural-edge complement to the P&L tear sheet. Tells the user how
well they followed the engine's rules independently of whether the engine
itself made money. Process > outcomes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class DisciplineReport:
    rule_following_streak_days: int            # consecutive days no override
    override_count_total: int                   # all-time
    override_avg_return_pct: float              # how well overrides did
    aligned_avg_return_pct: float               # vs engine-aligned trades
    consecutive_losses: int                     # current streak of closed losers
    cooling_off_recommended: bool               # True when ≥2 consecutive losers
    process_adherence_index: float              # 0..100 weekly composite
    notes: list[str]


def _date(t: dict) -> Optional[datetime]:
    for k in ("closed_date", "exit_date", "date", "entry_date"):
        v = t.get(k)
        if v:
            try:
                return datetime.fromisoformat(str(v)[:10])
            except (ValueError, TypeError):
                continue
    return None


def _ret(t: dict) -> Optional[float]:
    for k in ("net_return_pct", "return_pct", "pct"):
        v = t.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def assess(journal: list[dict], positions: list[dict]) -> DisciplineReport:
    """Compute the discipline metrics over the user's full journal."""
    overrides = [t for t in journal if t.get("opened_against_engine")]
    aligned = [t for t in journal if not t.get("opened_against_engine")]
    override_returns = [r for r in (_ret(t) for t in overrides) if r is not None]
    aligned_returns = [r for r in (_ret(t) for t in aligned) if r is not None]

    avg_override = sum(override_returns) / len(override_returns) if override_returns else 0.0
    avg_aligned = sum(aligned_returns) / len(aligned_returns) if aligned_returns else 0.0

    # Consecutive losses on the journal in chronological order
    chrono = sorted(
        [t for t in journal if _date(t) is not None and _ret(t) is not None],
        key=lambda t: _date(t),
    )
    cons_losses = 0
    for t in reversed(chrono):
        r = _ret(t)
        if r is not None and r < 0:
            cons_losses += 1
        else:
            break

    # Streak: days since the last override
    last_override_date = max((_date(t) for t in overrides if _date(t)), default=None)
    if last_override_date is None:
        # never overridden; count from earliest aligned trade or today
        anchor = min((_date(t) for t in chrono if _date(t)), default=datetime.now())
        streak_days = (datetime.now() - anchor).days
    else:
        streak_days = (datetime.now() - last_override_date).days

    # Process Adherence Index — weighted composite (0-100)
    # Components:
    #   a) override rate (lower = better)
    #   b) override-vs-aligned delta (negative override return = good discipline)
    #   c) cooling-off respect (no trades opened during cooling-off would be ideal)
    n = len(journal) or 1
    override_rate_score = 100 * (1 - len(overrides) / n)            # 100 if never overrode
    if overrides and aligned:
        # Bigger gap (aligned > overrides) = better discipline
        gap = avg_aligned - avg_override
        gap_score = max(0.0, min(100.0, 50.0 + gap * 10))           # 50 baseline + 10pts/pp
    else:
        gap_score = 50.0
    cooling_score = 0.0 if cons_losses >= 2 else 100.0
    pai = round((0.5 * override_rate_score + 0.3 * gap_score + 0.2 * cooling_score), 1)

    notes = []
    if cons_losses >= 2:
        notes.append(
            f"⚠️ {cons_losses} consecutive losing trades — Composer.trade-style cooling-off: "
            "skip the next setup or paper-trade until a winner closes."
        )
    if overrides and len(overrides) >= 3 and avg_override < avg_aligned:
        delta = avg_aligned - avg_override
        notes.append(
            f"Overriding the engine cost you {delta:+.2f}pp avg per trade across "
            f"{len(overrides)} overrides — the discipline gap is measurable."
        )
    if not overrides and len(aligned) >= 5:
        notes.append("Never overridden the engine — perfect adherence. Process edge is yours to lose.")

    return DisciplineReport(
        rule_following_streak_days=int(streak_days),
        override_count_total=len(overrides),
        override_avg_return_pct=round(avg_override, 3),
        aligned_avg_return_pct=round(avg_aligned, 3),
        consecutive_losses=cons_losses,
        cooling_off_recommended=(cons_losses >= 2),
        process_adherence_index=pai,
        notes=notes,
    )


def post_mortem(trade: dict) -> list[str]:
    """Generate Tickeron-style auto-narrative bullets for a single closed trade.
    Returns a list of plain-English insights based on the trade's structure.
    """
    out = []
    ret = _ret(trade)
    score = trade.get("score_at_entry")
    tape = trade.get("tape_at_entry")
    held_d = None
    entry_d = _date({"entry_date": trade.get("entry_date") or trade.get("date")})
    exit_d = _date({"closed_date": trade.get("closed_date") or trade.get("exit_date")})
    if entry_d and exit_d:
        held_d = (exit_d - entry_d).days

    if ret is None:
        return ["Net return unavailable — cannot generate narrative."]

    # Headline
    if ret > 0:
        out.append(f"🟢 Winner closed at {ret:+.2f}%. Held {held_d or '?'} days.")
    else:
        out.append(f"🔴 Loser closed at {ret:+.2f}%. Held {held_d or '?'} days.")

    # Score context
    if isinstance(score, (int, float)):
        if score >= 75:
            out.append(f"Entry was a HIGH-conviction GO (score {score:.0f}/100) — "
                       f"{'consistent with' if ret > 0 else 'against'} the engine's own probability.")
        elif score >= 65:
            out.append(f"Entry was a borderline GO (score {score:.0f}/100) — "
                       "borderline setups should size SMALLER per Kelly.")
        else:
            out.append(f"⚠️ Entry score was {score:.0f}/100 (below the 65 GO threshold) — "
                       "this was an override trade, not an engine pick.")

    # Tape context
    if tape == "HOSTILE":
        out.append("Tape was HOSTILE at entry. Held-out 2026 expectancy in HOSTILE is -1.71%. "
                   f"This trade {'beat' if ret > 0 else 'matches'} that regime baseline.")
    elif tape == "MIXED":
        out.append("Tape was MIXED at entry. Historical expectancy: +2%. "
                   f"This trade {'beat' if ret > 2 else 'underperformed'} the regime baseline.")
    elif tape == "TRENDING":
        out.append("Tape was TRENDING. Historical expectancy: +7%. "
                   f"This trade {'beat' if ret > 7 else 'underperformed'} the regime baseline.")

    # Override context
    if trade.get("opened_against_engine"):
        out.append("🛑 You opened this against the engine's downgrade. "
                   "Track override-vs-engine ROI on Trade Replay to see if your judgment overlay is adding value.")

    # Thesis vs invalidation (AITrader-style discipline review)
    invalidation = trade.get("invalidation", "")
    if invalidation:
        out.append(f"❌ Pre-committed invalidation: \"{invalidation[:120]}\". "
                   "Honest review: did this condition trigger BEFORE the SL fired? If yes, "
                   "and you didn't exit early, that's an emotional override — log it in "
                   "the lesson field and adjust your discipline for next time.")

    # Time-on-trade context
    if held_d is not None:
        if ret < 0 and held_d > 12:
            out.append(f"⏳ Held {held_d} days through a losing exit — time-stop is at 12 bars; "
                       "consider whether your manual hold-times skew long on losers.")
        if ret > 0 and held_d < 3:
            out.append("⚡ Profitable in <3 days — fast moves can be regime-driven gifts, not reliable signal.")

    return out
