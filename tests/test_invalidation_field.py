"""Tests for the invalidation-field discipline (borrowed from AITrader §2)."""
from __future__ import annotations

import pytest
import components.state as state


@pytest.fixture
def empty_session(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_POSITIONS_PATH", str(tmp_path / "positions.json"))
    monkeypatch.setattr(state, "_JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    import streamlit as st
    st.session_state.clear()
    st.session_state["positions"] = []
    st.session_state["journal"] = []
    yield


def test_invalidation_persists_through_close(empty_session):
    state.add_position({
        "symbol": "RELIANCE", "buy_price": 1300.0, "qty": 10,
        "stop_loss": 1250.0, "target_1": 1400.0, "target_2": 1450.0,
        "entry_date": "2026-06-25",
        "thesis": "Breakout above 200-EMA on heavy volume",
        "invalidation": "Close below 1280 or Nifty closes below 23,500",
        "score_at_entry": 78.0, "tape_at_entry": "MIXED",
    })
    state.close_position(0, sell_price=1350.0, sell_date="2026-06-29",
                         exit_reason="TARGET_1", lesson="Worked as expected")
    j = state.get_journal()
    assert len(j) == 1
    assert j[0]["invalidation"] == "Close below 1280 or Nifty closes below 23,500"
    assert j[0]["thesis"] == "Breakout above 200-EMA on heavy volume"


def test_invalidation_default_empty_when_missing(empty_session):
    """Old positions without invalidation field still close cleanly."""
    state.add_position({
        "symbol": "INFY", "buy_price": 1000, "qty": 5,
        "stop_loss": 980, "target_1": 1050, "entry_date": "2026-06-20",
        "thesis": "legacy position without invalidation",
    })
    # Resolve the INFY position's current index — Streamlit session_state
    # can carry entries from earlier tests in the same module despite the
    # fixture's clear(). Find by symbol rather than trusting [0].
    positions = state.get_positions()
    idx = next(i for i, p in enumerate(positions) if p.get("symbol") == "INFY")
    state.close_position(idx, sell_price=1020)
    # And read the matching journal entry by symbol too.
    j = [e for e in state.get_journal() if e.get("symbol") == "INFY"]
    assert len(j) >= 1
    assert j[-1]["invalidation"] == ""   # safe default


def test_post_mortem_surfaces_invalidation():
    """The post-mortem narrative on Trade Replay must mention invalidation
    when it was recorded, so the user can audit whether they honored their
    own pre-committed exit condition."""
    from components.discipline import post_mortem
    t = {
        "symbol": "TCS", "net_return_pct": -2.5, "score_at_entry": 70,
        "tape_at_entry": "MIXED", "entry_date": "2026-06-20",
        "closed_date": "2026-06-25",
        "invalidation": "Nifty regime flips to HOSTILE or close below 200-EMA",
    }
    bullets = post_mortem(t)
    assert any("invalidation" in b.lower() for b in bullets)
    assert any("trigger" in b.lower() for b in bullets)


def test_post_mortem_no_invalidation_no_bullet():
    """Trades recorded before this discipline existed shouldn't show empty noise."""
    from components.discipline import post_mortem
    t = {
        "symbol": "WIPRO", "net_return_pct": 2.5, "score_at_entry": 70,
        "tape_at_entry": "MIXED",
    }
    bullets = post_mortem(t)
    assert not any("invalidation" in b.lower() for b in bullets)
