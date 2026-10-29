import json
from pathlib import Path
from components.state_reader import read_latest, read_health


def test_read_latest_returns_dict(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "latest.json").write_text(json.dumps({"regime": "MIXED", "signals": []}))
    monkeypatch.setenv("SIGNALS_LOCAL_PATH", str(tmp_path))
    assert read_latest()["regime"] == "MIXED"


def test_read_latest_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGNALS_LOCAL_PATH", str(tmp_path))
    assert read_latest() is None


def test_read_health_returns_dict(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "pipeline_health.json").write_text(json.dumps({"status": "healthy"}))
    monkeypatch.setenv("SIGNALS_LOCAL_PATH", str(tmp_path))
    assert read_health()["status"] == "healthy"
