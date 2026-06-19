"""Tests for the position-close lifecycle (positions[] → journal[])."""
from __future__ import annotations

import pytest

import components.state as state


@pytest.fixture
def empty_session(monkeypatch, tmp_path):
    """Isolate state into a temp dir so tests don't touch the real journal."""
    monkeypatch.setattr(state, "_POSITIONS_PATH", str(tmp_path / "positions.json"))
    monkeypatch.setattr(state, "_JOURNAL_PATH", str(tmp_path / "trade_journal.json"))
    import streamlit as st
    st.session_state.clear()
    st.session_state["positions"] = []
    st.session_state["journal"] = []
    yield


def _seed_position():
    state.add_position({
        "symbol": "RELIANCE", "buy_price": 1300.0, "qty": 10,
        "stop_loss": 1250.0, "target": 1400.0, "target_1": 1400.0,
        "target_2": 1450.0, "entry_date": "2026-06-01",
        "score_at_entry": 78.0, "tape_at_entry": "MIXED",
        "win_prob_at_entry": 65.0, "rr_at_entry": 2.0,
        "invested": 13000.0, "thesis": "test thesis 25 chars",
    })


def test_close_position_moves_to_journal(empty_session):
    _seed_position()
    assert len(state.get_positions()) == 1
    assert len(state.get_journal()) == 0

    ok = state.close_position(0, sell_price=1380.0, sell_date="2026-06-15",
                              exit_reason="TARGET_1", lesson="great trade")
    assert ok
    assert len(state.get_positions()) == 0
    journal = state.get_journal()
    assert len(journal) == 1
    j = journal[0]
    assert j["symbol"] == "RELIANCE"
    assert j["sell_price"] == 1380.0
    assert j["qty"] == 10
    assert j["exit_reason"] == "TARGET_1"
    assert j["lesson"] == "great trade"
    assert j["score_at_entry"] == 78.0
    assert j["tape_at_entry"] == "MIXED"


def test_close_position_computes_realised_pnl_with_charges(empty_session):
    _seed_position()
    ok = state.close_position(0, sell_price=1400.0, sell_date="2026-06-15")
    assert ok
    j = state.get_journal()[0]
    # Gross: (1400 - 1300) * 10 = 1000
    assert j["gross_pnl"] == 1000.0
    # Net should be less than gross (charges deducted)
    assert j["pnl"] < j["gross_pnl"]
    # Net return % stored as buy-to-sell percentage
    assert abs(j["net_return_pct"] - (1400 - 1300) / 1300 * 100) < 0.01


def test_close_position_invalid_index(empty_session):
    _seed_position()
    assert state.close_position(99, sell_price=1380.0) is False
    assert state.close_position(-1, sell_price=1380.0) is False
    assert len(state.get_positions()) == 1


def test_close_position_invalid_price(empty_session):
    _seed_position()
    assert state.close_position(0, sell_price=0) is False
    assert state.close_position(0, sell_price=-100) is False
    assert len(state.get_positions()) == 1


def test_close_preserves_entry_context_for_post_mortem(empty_session):
    """The post-mortem narrative in Trade Replay needs the original score,
    tape regime, win prob, and override flag. close_position() must preserve
    all of these on the journal record."""
    _seed_position()
    # Mark this as an override trade
    pos = state.get_positions()[0]
    pos["opened_against_engine"] = True
    pos["override_phrase_used"] = "I accept -1.61% expectancy"
    state.set_positions([pos])

    state.close_position(0, sell_price=1280.0, exit_reason="STOP_LOSS")
    j = state.get_journal()[0]
    assert j["opened_against_engine"] is True
    assert j["override_phrase_used"] == "I accept -1.61% expectancy"
    # Score/tape/winp/RR all preserved
    assert j["score_at_entry"] == 78.0
    assert j["tape_at_entry"] == "MIXED"
    assert j["win_prob_at_entry"] == 65.0
    assert j["rr_at_entry"] == 2.0


def test_close_multiple_positions_in_order(empty_session):
    """Closing one position must not affect the indices of others."""
    _seed_position()
    state.add_position({
        "symbol": "TCS", "buy_price": 4000.0, "qty": 5,
        "stop_loss": 3800.0, "target_1": 4200.0, "entry_date": "2026-06-02",
        "score_at_entry": 75.0, "tape_at_entry": "MIXED",
    })
    assert len(state.get_positions()) == 2
    # Close the first (RELIANCE)
    state.close_position(0, sell_price=1380.0)
    remaining = state.get_positions()
    assert len(remaining) == 1
    assert remaining[0]["symbol"] == "TCS"
    # Close the now-only-remaining
    state.close_position(0, sell_price=4100.0)
    assert state.get_positions() == []
    assert len(state.get_journal()) == 2
