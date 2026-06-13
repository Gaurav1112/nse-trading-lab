"""Sector exposure cap — refuses new entry when sector cap reached."""
from __future__ import annotations

from components.risk_governor import can_open_in_sector, count_by_sector
from nse_backtest.sectors import sector_of


def test_sector_of_known_symbols():
    assert sector_of("HDFCBANK") == "Banks"
    assert sector_of("RELIANCE") == "Energy"
    assert sector_of("TCS") == "IT"
    assert sector_of("HDFCBANK.NS") == "Banks"


def test_sector_of_unknown_returns_unclassified():
    assert sector_of("RANDOM_SYMBOL_XYZ") == "Unclassified"
    assert sector_of("") == "Unclassified"


def test_count_by_sector_ignores_closed_positions():
    positions = [
        {"symbol": "HDFCBANK"},
        {"symbol": "ICICIBANK"},
        {"symbol": "SBIN", "closed_date": "2026-06-10"},
        {"symbol": "TCS"},
    ]
    counts = count_by_sector(positions)
    assert counts.get("Banks", 0) == 2
    assert counts.get("IT", 0) == 1


def test_can_open_in_sector_allows_when_below_cap():
    positions = [{"symbol": "HDFCBANK"}]
    allowed, reason = can_open_in_sector(positions, "ICICIBANK")
    assert allowed
    assert "Banks 1/2" in reason


def test_can_open_in_sector_blocks_at_cap():
    positions = [{"symbol": "HDFCBANK"}, {"symbol": "ICICIBANK"}]
    allowed, reason = can_open_in_sector(positions, "SBIN")
    assert not allowed
    assert "cap reached" in reason.lower()


def test_can_open_in_sector_unclassified_never_blocks():
    positions = [
        {"symbol": "UNK1"}, {"symbol": "UNK2"}, {"symbol": "UNK3"},
    ]
    allowed, reason = can_open_in_sector(positions, "UNKNOWN_NEW")
    assert allowed
    assert "no block" in reason.lower()
