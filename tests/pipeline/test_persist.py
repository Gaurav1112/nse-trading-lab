import json
from pathlib import Path
from datetime import datetime, timezone
import pytest
from pipeline.compute import SignalBatch, Signal
from pipeline.persist import persist_batch, write_health


def _batch():
    return SignalBatch(
        generated_at=datetime(2026, 7, 15, 10, 35, tzinfo=timezone.utc),
        regime="MIXED",
        regime_conditions={"nifty_close": 21400.0},
        swing_signals=[
            Signal("swing-t1", "SWING", "BUY", "RELIANCE", 2450.0, 2410.0, 2530.0, "MIXED", "test")
        ],
    )


def test_persist_writes_latest_json(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "signals").mkdir()
    persist_batch(_batch(), tmp_path, push=False)
    latest = json.loads((tmp_path / "state" / "latest.json").read_text())
    assert latest["regime"] == "MIXED"
    assert latest["signals"][0]["symbol"] == "RELIANCE"


def test_persist_appends_daily_signal_log(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "signals").mkdir()
    persist_batch(_batch(), tmp_path, push=False)
    daily_dir = tmp_path / "signals" / "2026-07-15"
    assert daily_dir.exists()
    files = list(daily_dir.glob("*.json"))
    assert len(files) == 1


def test_write_health_updates_status(tmp_path):
    health_path = tmp_path / "pipeline_health.json"
    write_health(health_path, status="healthy", errors=[], last_run_ts=datetime.now(timezone.utc))
    h = json.loads(health_path.read_text())
    assert h["status"] == "healthy"
