"""Phase 3 v2 regime-conditional calibrator tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import nse_backtest.model.calibrator as cal_mod
from nse_backtest.model.calibrator import calibrate


@pytest.fixture(autouse=True)
def reset_calibrator_cache():
    cal_mod._load_calibrator.cache_clear()
    yield
    cal_mod._load_calibrator.cache_clear()


def _write_v2_artifact(path: Path, *, trending_top=0.7, mixed_top=0.5, hostile_top=0.45):
    """Synthesize a v2 artifact with three distinct per-regime curves."""
    grid = [0.0, 0.5, 1.0]
    payload = {
        "version": "v2",
        "n_trades": 100,
        "grid": grid,                   # v1-compatible fallback fields
        "calibrated": [0.3, 0.4, 0.55],
        "bucket_stats": {"global": {"n": 50, "actual_win_rate": 0.55, "avg_raw_p": 0.65}},
        "held_out_brier": {"global_chrono_80_20": 0.25},
        "fold_results": [],
        "by_regime": {
            "TRENDING": {
                "version": "v2", "n_trades": 50, "grid": grid,
                "calibrated": [0.25, 0.5, trending_top],
                "bucket_stats": {"top": {"n": 25, "actual_win_rate": trending_top, "avg_raw_p": 0.85}},
                "held_out_brier": {"chrono_80_20": 0.22},
                "fold_results": [],
            },
            "MIXED": {
                "version": "v2", "n_trades": 40, "grid": grid,
                "calibrated": [0.25, 0.4, mixed_top],
                "bucket_stats": {"top": {"n": 20, "actual_win_rate": mixed_top, "avg_raw_p": 0.85}},
                "held_out_brier": {"chrono_80_20": 0.23},
                "fold_results": [],
            },
            "HOSTILE": {
                "version": "v2", "n_trades": 10, "grid": grid,
                "calibrated": [0.25, 0.35, hostile_top],
                "bucket_stats": {"top": {"n": 5, "actual_win_rate": hostile_top, "avg_raw_p": 0.85}},
                "held_out_brier": {"chrono_80_20": 0.21},
                "fold_results": [],
            },
        },
        "notes": "v2 test artifact",
    }
    path.write_text(json.dumps(payload))


def test_regime_hint_dispatches_to_correct_curve(tmp_path, monkeypatch):
    _write_v2_artifact(tmp_path / "c.json",
                       trending_top=0.80, mixed_top=0.55, hostile_top=0.45)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    # raw 100% probes the top of each curve; ceiling cap clips at the
    # curve's top-bucket win rate (n>=20 for TRENDING/MIXED; n=5 for HOSTILE,
    # which falls back to its single bucket).
    t_pct, t_reason = calibrate(100.0, regime="TRENDING")
    m_pct, m_reason = calibrate(100.0, regime="MIXED")
    h_pct, h_reason = calibrate(100.0, regime="HOSTILE")

    assert "regime=TRENDING" in t_reason
    assert "regime=MIXED" in m_reason
    assert "regime=HOSTILE" in h_reason
    # Trending curve allows higher calibrated win prob than hostile
    assert t_pct > m_pct > h_pct
    assert abs(t_pct - 80.0) < 1
    assert abs(m_pct - 55.0) < 1
    assert abs(h_pct - 45.0) < 1


def test_unknown_regime_falls_back_to_global(tmp_path, monkeypatch):
    _write_v2_artifact(tmp_path / "c.json")
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    pct, reason = calibrate(100.0, regime="UNKNOWN")
    assert "global" in reason
    # Global ceiling = 0.55 from synthetic bucket_stats
    assert abs(pct - 55.0) < 1


def test_no_regime_argument_uses_global(tmp_path, monkeypatch):
    _write_v2_artifact(tmp_path / "c.json")
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    pct, reason = calibrate(100.0)
    assert "global" in reason


def test_v1_artifact_still_loads_through_v2_reader(tmp_path, monkeypatch):
    """A v1 artifact has no `by_regime` key — must still calibrate via the
    top-level fields. This is the backward-compat invariant."""
    grid = [0.0, 0.5, 1.0]
    v1_payload = {
        "version": "v1",
        "n_trades": 390,
        "grid": grid,
        "calibrated": [0.25, 0.45, 0.55],
        "bucket_stats": {"top": {"n": 200, "actual_win_rate": 0.53, "avg_raw_p": 0.74}},
        "held_out_brier": {"2025": 0.257},
    }
    (tmp_path / "c.json").write_text(json.dumps(v1_payload))
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    pct, reason = calibrate(100.0, regime="TRENDING")
    assert "global" in reason  # No by_regime → falls back to top-level curve
    assert pct <= 53.5  # Ceiling cap from top bucket


def test_shipped_v2_artifact_loads():
    """The artifact we just trained must load cleanly."""
    cal_mod._load_calibrator.cache_clear()
    cal = cal_mod._load_calibrator()
    assert cal is not None
    pct, reason = calibrate(72.0, regime="HOSTILE")
    assert 0 <= pct <= 100
