"""Phase 3 v1 — calibrator ceiling cap + walk-forward Brier surfacing.

The v0 isotonic curve produced calibrated=1.0 for raw_p > 81% while the
actual top-bucket win rate was 0.50. That is dangerous if a real-money
trader sees "100% win probability" on a pick. The runtime ceiling cap
clips calibrated output at the highest observed actual_win_rate in
bucket_stats so we never claim more confidence than the data supports.
"""
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


def _write_v1_calibrator(
    path: Path,
    grid_points,
    calibrated_points,
    bucket_stats,
    held_out_brier=None,
):
    payload = {
        "version": "v1",
        "fit_date": "2026-06-14",
        "n_trades": sum(b["n"] for b in bucket_stats.values()),
        "grid": list(grid_points),
        "calibrated": list(calibrated_points),
        "bucket_stats": bucket_stats,
    }
    if held_out_brier is not None:
        payload["held_out_brier"] = held_out_brier
    path.write_text(json.dumps(payload))


def test_ceiling_cap_clips_overconfident_curve(tmp_path, monkeypatch):
    """A broken curve that maps high raw_p to 1.0 must be capped at the
    highest observed actual_win_rate from bucket_stats."""
    grid = [0.0, 0.5, 0.8, 0.9, 1.0]
    cal_curve = [0.25, 0.25, 0.55, 1.0, 1.0]  # the v0 bug — top pinned at 1.0
    buckets = {
        "(0.6, 0.7]": {"n": 134, "actual_win_rate": 0.515, "avg_raw_p": 0.67},
        "(0.7, 0.8]": {"n": 236, "actual_win_rate": 0.534, "avg_raw_p": 0.74},
        "(0.8, 0.9]": {"n": 18, "actual_win_rate": 0.500, "avg_raw_p": 0.82},
    }
    _write_v1_calibrator(tmp_path / "c.json", grid, cal_curve, buckets)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    out, reason = calibrate(95.0)
    # Max actual win rate in reliable buckets (n>=20) is 0.534 → 53.4%
    assert out <= 53.5, f"Calibrator returned {out}; ceiling cap should clip to ≤53.4%"
    assert "cap" in reason.lower() or "ceiling" in reason.lower()


def test_ceiling_cap_no_effect_when_curve_within_observed_range(tmp_path, monkeypatch):
    """If the curve already stays within the observed top win rate, no cap fires."""
    grid = [0.0, 0.5, 1.0]
    cal_curve = [0.30, 0.45, 0.52]  # all <= 0.534 ceiling
    buckets = {
        "(0.6, 0.7]": {"n": 134, "actual_win_rate": 0.515, "avg_raw_p": 0.67},
        "(0.7, 0.8]": {"n": 236, "actual_win_rate": 0.534, "avg_raw_p": 0.74},
    }
    _write_v1_calibrator(tmp_path / "c.json", grid, cal_curve, buckets)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    out, reason = calibrate(95.0)
    # interp at x=0.95 on (0.5→0.45, 1.0→0.52) is 0.513 → 51.3%
    assert out == pytest.approx(51.3, abs=0.5)
    assert "cap" not in reason.lower()


def test_unreliable_small_buckets_excluded_from_ceiling(tmp_path, monkeypatch):
    """Buckets with n<20 are not trustworthy enough to set the ceiling."""
    grid = [0.0, 0.5, 1.0]
    cal_curve = [0.30, 0.45, 0.90]
    buckets = {
        # Bigger buckets cap at 0.534; the tiny n=3 bucket with 0.95 is ignored.
        "(0.7, 0.8]": {"n": 236, "actual_win_rate": 0.534, "avg_raw_p": 0.74},
        "(0.9, 1.0]": {"n": 3,   "actual_win_rate": 0.95,  "avg_raw_p": 0.95},
    }
    _write_v1_calibrator(tmp_path / "c.json", grid, cal_curve, buckets)
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    out, _ = calibrate(95.0)
    assert out <= 53.5, f"Got {out}; unreliable n=3 bucket should not raise ceiling"


def test_held_out_brier_surfaces_in_reason(tmp_path, monkeypatch):
    """If artifact carries held_out_brier, calibrate() should expose it."""
    grid = [0.0, 0.5, 1.0]
    cal_curve = [0.30, 0.45, 0.52]
    buckets = {"(0.7, 0.8]": {"n": 236, "actual_win_rate": 0.534, "avg_raw_p": 0.74}}
    _write_v1_calibrator(
        tmp_path / "c.json", grid, cal_curve, buckets,
        held_out_brier={"2024": 0.246, "2025": 0.249},
    )
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    out, reason = calibrate(72.0)
    assert "brier" in reason.lower()


def test_existing_v0_artifact_still_loads_safely(tmp_path, monkeypatch):
    """A v0 artifact without held_out_brier must still load without error;
    the ceiling cap still applies because bucket_stats are present."""
    grid = [0.0, 0.85, 1.0]
    cal_curve = [0.25, 1.0, 1.0]  # mimics the v0 bug
    buckets = {
        "(0.6, 0.7]": {"n": 134, "actual_win_rate": 0.515, "avg_raw_p": 0.67},
        "(0.7, 0.8]": {"n": 236, "actual_win_rate": 0.534, "avg_raw_p": 0.74},
        "(0.8, 0.9]": {"n": 18, "actual_win_rate": 0.500, "avg_raw_p": 0.82},
    }
    payload = {
        "version": "v0",
        "n_trades": 388,
        "grid": list(grid),
        "calibrated": list(cal_curve),
        "bucket_stats": buckets,
    }
    (tmp_path / "c.json").write_text(json.dumps(payload))
    monkeypatch.setattr(cal_mod, "_CALIB_PATH", tmp_path / "c.json")
    cal_mod._load_calibrator.cache_clear()

    out, _ = calibrate(95.0)
    assert out <= 53.5  # v0 bug is neutralised by the runtime cap
