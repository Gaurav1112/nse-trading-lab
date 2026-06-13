"""Phase C: risk governor — Kavya Rao's portfolio guards."""
from datetime import datetime, timedelta
import json

import pytest

from components.risk_governor import (
    assess, at_position_limit, count_open_positions,
    weekly_pnl_pct, is_cooling_off, log_verdict, DEFAULTS,
)


def test_no_positions_no_journal_means_can_trade():
    v = assess(positions=[], journal=[], capital=200_000)
    assert v.can_trade is True
    assert v.open_positions == 0
    assert v.cooling_off_active is False


def test_at_max_positions_blocks_new_trade():
    positions = [{"symbol": f"S{i}", "buy_price": 100, "qty": 1, "stop_loss": 95,
                  "target": 110, "entry_date": "2026-06-13"} for i in range(5)]
    v = assess(positions=positions, journal=[], capital=200_000)
    assert v.can_trade is False
    assert v.open_positions == 5
    assert any("max concurrent positions" in r.lower() for r in v.reasons)


def test_closed_positions_do_not_count_toward_limit():
    positions = [
        {"symbol": "OPEN", "buy_price": 100, "qty": 1, "stop_loss": 95, "target": 110},
        {"symbol": "CLOSED", "buy_price": 100, "qty": 1, "stop_loss": 95, "target": 110,
         "closed_date": "2026-06-10"},
    ]
    assert count_open_positions(positions) == 1


def test_weekly_drawdown_triggers_cooling_off():
    """Two recent losing trades summing to -7% of ₹2L capital → cooling-off."""
    today = datetime.now().date().isoformat()
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    journal = [
        {"closed_date": today, "pnl": -10_000},
        {"closed_date": yesterday, "pnl": -5_000},
    ]
    v = assess(positions=[], journal=journal, capital=200_000)
    assert v.cooling_off_active is True
    assert v.can_trade is False
    assert v.cooling_off_until is not None


def test_old_losses_outside_window_do_not_trigger_cooling_off():
    """A loss from 30 days ago should not block trading today."""
    old = (datetime.now() - timedelta(days=30)).date().isoformat()
    journal = [{"closed_date": old, "pnl": -50_000}]  # huge loss but stale
    v = assess(positions=[], journal=journal, capital=200_000)
    assert v.cooling_off_active is False
    assert v.can_trade is True


def test_audit_log_appends_jsonl(tmp_path, monkeypatch):
    """Each call to log_verdict appends one JSON line."""
    fake_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr("components.risk_governor._AUDIT_LOG_PATH", fake_path)
    log_verdict("RELIANCE", "WAIT", 62.5, 58.0, "HOSTILE")
    log_verdict("TCS", "GO", 78.0, 72.0, "MIXED")
    lines = fake_path.read_text().strip().split("\n")
    assert len(lines) == 2
    r0 = json.loads(lines[0])
    assert r0["symbol"] == "RELIANCE" and r0["verdict"] == "WAIT"
    assert r0["tape_regime"] == "HOSTILE"


def test_weekly_pnl_handles_missing_dates_gracefully():
    journal = [
        {"pnl": -1000},  # no date — should be ignored
        {"closed_date": "not-a-date", "pnl": -1000},  # bad date — ignored
        {"closed_date": datetime.now().date().isoformat(), "pnl": -2000},  # counts
    ]
    pnl = weekly_pnl_pct(journal, capital=100_000)
    assert pnl == pytest.approx(-2.0)  # only the valid entry contributes
