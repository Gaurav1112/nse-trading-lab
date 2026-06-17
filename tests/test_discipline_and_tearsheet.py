"""Tests for discipline + tear sheet components."""
from __future__ import annotations

from datetime import datetime, timedelta

from components.discipline import assess, post_mortem
from components.tear_sheet import build


def _trade(d_offset_days: int, ret: float, override: bool = False,
           score: float = 70, tape: str = "MIXED", symbol: str = "X") -> dict:
    d = (datetime.now() - timedelta(days=d_offset_days)).strftime("%Y-%m-%d")
    return {
        "symbol": symbol, "closed_date": d, "net_return_pct": ret,
        "score_at_entry": score, "tape_at_entry": tape,
        "opened_against_engine": override, "entry_date": d, "pnl": ret * 1000,
    }


def test_discipline_empty_journal():
    d = assess(journal=[], positions=[])
    assert d.process_adherence_index >= 0
    assert d.consecutive_losses == 0
    assert not d.cooling_off_recommended


def test_cooling_off_fires_on_two_consecutive_losses():
    journal = [
        _trade(10, +2.0),       # win
        _trade(5, -1.5),         # loss
        _trade(2, -2.0),         # loss
    ]
    d = assess(journal, positions=[])
    assert d.consecutive_losses == 2
    assert d.cooling_off_recommended


def test_consecutive_loss_streak_resets_on_win():
    journal = [
        _trade(10, -1.0),       # loss
        _trade(5, -1.5),         # loss
        _trade(2, +0.5),         # win — resets
    ]
    d = assess(journal, positions=[])
    assert d.consecutive_losses == 0


def test_override_vs_aligned_gap_in_notes():
    journal = [
        _trade(20, +3.0, override=False),
        _trade(15, +2.0, override=False),
        _trade(10, +1.0, override=False),
        _trade(8, -1.0, override=True),
        _trade(5, -2.0, override=True),
        _trade(3, -1.5, override=True),
    ]
    d = assess(journal, positions=[])
    assert d.override_count_total == 3
    assert d.aligned_avg_return_pct > d.override_avg_return_pct
    assert any("cost you" in n.lower() or "override" in n.lower() for n in d.notes)


def test_post_mortem_returns_bullets():
    t = _trade(5, -2.5, score=78, tape="HOSTILE")
    bullets = post_mortem(t)
    assert len(bullets) >= 2
    assert any("Loser" in b or "🔴" in b for b in bullets)
    assert any("HOSTILE" in b for b in bullets)


def test_post_mortem_override_call_out():
    t = _trade(3, -1.0, override=True, score=55, tape="HOSTILE")
    bullets = post_mortem(t)
    # below-65 score
    assert any("below the 65" in b or "override" in b.lower() for b in bullets)


def test_tear_sheet_empty():
    ts = build(journal=[], capital=100_000)
    assert ts.n_closed == 0
    assert ts.notes


def test_tear_sheet_aggregates_monthly():
    journal = [
        _trade(60, +1.0),     # ~2 months ago
        _trade(30, +2.0),     # ~1 month ago
        _trade(5, +1.5),       # this month
        _trade(2, -1.0),       # this month
    ]
    ts = build(journal, capital=100_000)
    assert ts.n_closed == 4
    assert ts.total_return_pct > 0
    # Monthly returns dict has at least 2 distinct months
    assert len(ts.monthly_returns) >= 2


def test_tear_sheet_max_drawdown():
    # Sequence: +5, +3 (peak +8), -10 (cum -2, dd from peak = 10)
    journal = [
        _trade(10, +5.0),
        _trade(8, +3.0),
        _trade(3, -10.0),
    ]
    ts = build(journal, capital=100_000)
    assert abs(ts.max_drawdown_pct - 10.0) < 0.5
