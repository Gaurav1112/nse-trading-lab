from datetime import datetime, timezone, timedelta
from components.trust_badge import classify

NOW = datetime(2026, 7, 15, 10, 30, tzinfo=timezone.utc)


def test_healthy_recent_run_no_errors():
    h = {"status": "healthy", "errors": [], "last_run_ts": (NOW - timedelta(minutes=3)).isoformat()}
    state, msg = classify(h, NOW)
    assert state == "healthy"


def test_degraded_when_errors_present():
    h = {"status": "degraded", "errors": ["stale: [X]"], "last_run_ts": (NOW - timedelta(minutes=3)).isoformat()}
    state, _ = classify(h, NOW)
    assert state == "degraded"


def test_degraded_when_10_to_30_min_old():
    h = {"status": "healthy", "errors": [], "last_run_ts": (NOW - timedelta(minutes=15)).isoformat()}
    state, _ = classify(h, NOW)
    assert state == "degraded"


def test_dead_when_over_30_min_old():
    h = {"status": "healthy", "errors": [], "last_run_ts": (NOW - timedelta(minutes=45)).isoformat()}
    state, _ = classify(h, NOW)
    assert state == "dead"
