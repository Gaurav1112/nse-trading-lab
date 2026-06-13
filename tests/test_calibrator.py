"""Phase 3 v0: calibrator tests — Meera Nair (C.9)."""
import json
from pathlib import Path

import numpy as np
import pytest

import nse_backtest.model.calibrator as cal_mod
from nse_backtest.model.calibrator import calibrate


@pytest.fixture(autouse=True)
def reset_calibrator_cache():
    cal_mod._load_calibrator.cache_clear()
    yield
    cal_mod._load_calibrator.cache_clear()


def _write_calibrator(path: Path, grid_points, calibrated_points, n=100):
    payload = {
        "version": "test", "fit_date": "2026-06-13",
        "n_trades": n, "grid": list(grid_points),
        "calibrated": list(calibrated_points), "bucket_stats": {},
    }
    path.write_text(json.dumps(payload))


def test_no_calibrator_returns_raw(tmp_path, monkeypatch):
    """If the calibrator file is missing, return the raw probability unchanged."""
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "nonexistent.json")
    cal_mod._load_calibrator.cache_clear()
    out, reason = calibrate(72.0)
    assert out == 72.0
    assert "unavailable" in reason.lower()


def test_identity_calibrator_passes_through(tmp_path, monkeypatch):
    grid = np.linspace(0, 1, 11)
    _write_calibrator(tmp_path / "calib.json", grid, grid)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "calib.json")
    cal_mod._load_calibrator.cache_clear()
    out, _ = calibrate(50.0)
    assert out == pytest.approx(50.0, abs=1.0)


def test_calibrator_shrinks_overconfident_probabilities(tmp_path, monkeypatch):
    """When raw_p=0.8 was historically a 0.6 win rate, calibrate should return ~60%."""
    grid = [0.0, 0.5, 0.8, 1.0]
    cal_curve = [0.0, 0.5, 0.6, 0.7]  # shrinks high probs toward 0.7 ceiling
    _write_calibrator(tmp_path / "calib.json", grid, cal_curve)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "calib.json")
    cal_mod._load_calibrator.cache_clear()
    out, _ = calibrate(80.0)
    assert out == pytest.approx(60.0, abs=1.0)


def test_calibrator_accepts_both_input_scales(tmp_path, monkeypatch):
    grid = np.linspace(0, 1, 11)
    _write_calibrator(tmp_path / "calib.json", grid, grid)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "calib.json")
    cal_mod._load_calibrator.cache_clear()
    out_pct, _ = calibrate(72.0)
    out_frac, _ = calibrate(0.72)
    assert out_pct == pytest.approx(out_frac, abs=0.1)


def test_calibrator_clips_out_of_range(tmp_path, monkeypatch):
    grid = np.linspace(0, 1, 11)
    _write_calibrator(tmp_path / "calib.json", grid, grid)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "calib.json")
    cal_mod._load_calibrator.cache_clear()
    out_high, _ = calibrate(150.0)
    out_low, _ = calibrate(-10.0)
    assert 0.0 <= out_high <= 100.0
    assert 0.0 <= out_low <= 100.0
