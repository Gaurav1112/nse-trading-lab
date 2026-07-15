from __future__ import annotations
import json
import os
from pathlib import Path


def _base() -> Path:
    return Path(os.environ.get("SIGNALS_LOCAL_PATH", str(Path.home() / ".nse-trading-lab" / "signals-clone")))


def read_latest() -> dict | None:
    p = _base() / "state" / "latest.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())


def read_health() -> dict | None:
    p = _base() / "state" / "pipeline_health.json"
    if not p.exists():
        return None
    return json.loads(p.read_text())
