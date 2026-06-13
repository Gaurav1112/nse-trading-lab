"""Phase A: positions must survive a fresh session_state."""
import os
import json
import importlib
from unittest.mock import patch

import pytest


@pytest.fixture
def isolated_positions(tmp_path, monkeypatch):
    """Redirect _POSITIONS_PATH to a tmp file so the test doesn't touch the real positions.json."""
    fake_path = str(tmp_path / "positions.json")

    # Import + patch the module constant. Use a fresh import so that prior tests
    # that may have evicted components.state from sys.modules cannot break us.
    import sys
    sys.modules.pop("components.state", None)
    from components import state as state_mod
    monkeypatch.setattr(state_mod, "_POSITIONS_PATH", fake_path)
    return state_mod, fake_path


def test_add_position_writes_to_disk(isolated_positions, monkeypatch):
    state_mod, fake_path = isolated_positions

    fake_session: dict = {}
    monkeypatch.setattr(state_mod.st, "session_state", fake_session)

    pos = {
        "symbol": "TEST", "buy_price": 100.0, "qty": 10,
        "stop_loss": 95.0, "target": 110.0,
        "entry_date": "2026-06-13", "thesis": "test trade", "score_at_entry": 78,
    }
    state_mod.add_position(pos)

    assert os.path.exists(fake_path), "positions.json was not created"
    with open(fake_path) as f:
        saved = json.load(f)
    assert len(saved) == 1
    assert saved[0]["symbol"] == "TEST"
    assert saved[0]["thesis"] == "test trade"


def test_load_position_from_disk_into_fresh_session(isolated_positions, monkeypatch):
    state_mod, fake_path = isolated_positions

    # Seed disk with a position
    with open(fake_path, "w") as f:
        json.dump([{"symbol": "SEED", "buy_price": 50.0, "qty": 5,
                    "stop_loss": 45.0, "target": 60.0,
                    "entry_date": "2026-06-01", "thesis": "seeded for test"}], f)

    # Fresh session_state (empty)
    fake_session: dict = {}
    monkeypatch.setattr(state_mod.st, "session_state", fake_session)

    state_mod._load_positions_from_disk()
    assert fake_session.get("positions") == [{
        "symbol": "SEED", "buy_price": 50.0, "qty": 5,
        "stop_loss": 45.0, "target": 60.0,
        "entry_date": "2026-06-01", "thesis": "seeded for test",
    }]


def test_legacy_date_key_still_works_in_bars_held(monkeypatch):
    """Old positions saved with 'date' (not 'entry_date') must still compute bars_held."""
    import pandas as pd
    import numpy as np
    from nse_backtest.position_monitor import daily_check

    n = 100
    rng = np.random.default_rng(0)
    close = 100 + rng.normal(0, 1, n).cumsum() * 0.1
    df = pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": [1_000_000] * n,
    }, index=pd.bdate_range("2025-01-02", periods=n))

    legacy_pos = {
        "symbol": "LEGACY", "buy_price": 100.0, "qty": 10,
        "stop_loss": 95.0, "target": 110.0,
        "date": df.index[-30].strftime("%Y-%m-%d"),  # legacy key
    }
    verdict = daily_check(legacy_pos, df)
    assert verdict.bars_held > 0, "Legacy 'date' key should still produce bars_held > 0"
