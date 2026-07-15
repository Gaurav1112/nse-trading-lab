from datetime import datetime, timezone, timedelta
from pipeline.fetch import LTPQuote
from pipeline.gates import is_market_hours, staleness_gate, dual_source_gate

IST = timezone(timedelta(hours=5, minutes=30))


def test_market_hours_inside_window():
    inside = datetime(2026, 7, 15, 10, 30, tzinfo=IST)  # Wednesday 10:30 IST
    assert is_market_hours(inside) is True


def test_market_hours_before_open():
    before = datetime(2026, 7, 15, 9, 10, tzinfo=IST)
    assert is_market_hours(before) is False


def test_market_hours_weekend():
    sat = datetime(2026, 7, 18, 10, 30, tzinfo=IST)  # Saturday
    assert is_market_hours(sat) is False


def test_staleness_gate_filters_old():
    now = datetime.now(timezone.utc)
    fresh = LTPQuote("A", 100.0, now - timedelta(minutes=5), "fyers")
    stale = LTPQuote("B", 200.0, now - timedelta(minutes=25), "fyers")
    kept, dropped = staleness_gate({"A": fresh, "B": stale}, max_age_min=20)
    assert list(kept.keys()) == ["A"]
    assert dropped == ["B"]


def test_dual_source_gate_flags_divergence():
    now = datetime.now(timezone.utc)
    primary = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, now, "fyers")}
    reference = {"RELIANCE": LTPQuote("RELIANCE", 2470.0, now, "yfinance")}  # ~0.8% off
    divergent = dual_source_gate(primary, reference, tolerance_pct=0.5)
    assert "RELIANCE" in divergent


def test_dual_source_gate_ignores_within_tolerance():
    now = datetime.now(timezone.utc)
    primary = {"RELIANCE": LTPQuote("RELIANCE", 2450.0, now, "fyers")}
    reference = {"RELIANCE": LTPQuote("RELIANCE", 2452.0, now, "yfinance")}  # ~0.08% off
    divergent = dual_source_gate(primary, reference, tolerance_pct=0.5)
    assert divergent == []
