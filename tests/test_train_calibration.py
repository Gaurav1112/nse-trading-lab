"""Phase 3 v1 — verify the walk-forward training script produces a
correctly-shaped artifact with held-out Brier and that artifact loads
cleanly through the calibrator runtime.

The actual training quality (Brier values) is data-dependent and not
asserted here; the verdict markdown surfaces that to the user.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest


def _make_synthetic_csv(path: Path, year: int, n: int, win_rate: float, seed: int):
    import numpy as np
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        win = rng.random() < win_rate
        raw_p_pct = float(rng.uniform(60, 80))
        rows.append({
            "symbol": f"SYM{i:03d}",
            "entry_date": f"{year}-{(i % 12) + 1:02d}-15",
            "exit_date": f"{year}-{(i % 12) + 1:02d}-25",
            "entry_price": 100.0,
            "exit_price": 105.0 if win else 95.0,
            "bars_held": 10,
            "gross_%": 5.0 if win else -5.0,
            "net_%": 4.5 if win else -5.5,
            "exit_reason": "T1" if win else "SL",
            "score": 70.0,
            "win_prob": raw_p_pct,
        })
    pd.DataFrame(rows).to_csv(path, index=False)


def test_training_script_produces_v1_artifact(tmp_path, monkeypatch):
    """End-to-end: synthetic CSVs → training → artifact has v1 schema."""
    repo = Path(__file__).resolve().parent.parent
    snap = tmp_path / "output" / "snapshots"
    model = tmp_path / "nse_backtest" / "model"
    snap.mkdir(parents=True)
    model.mkdir(parents=True)

    _make_synthetic_csv(snap / "phase1_picker_replay_2023.csv", 2023, 60, 0.70, seed=1)
    _make_synthetic_csv(snap / "phase1_picker_replay_2024.csv", 2024, 60, 0.45, seed=2)
    _make_synthetic_csv(snap / "phase1_picker_replay_2025.csv", 2025, 60, 0.45, seed=3)

    repo_root = str(repo)
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "train_calibration.py")],
        cwd=str(tmp_path), env={**__import__("os").environ, "PYTHONPATH": repo_root},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"Training failed: {result.stderr}"

    artifact_path = model / "calibrator.json"
    assert artifact_path.exists(), f"Expected artifact at {artifact_path}"
    artifact = json.loads(artifact_path.read_text())

    # Phase 3 v2 — regime-conditional artifact. Synthetic test data is regime-less
    # (entry_date labels won't match any real Nifty history in tmp_path), so we
    # validate the v2 schema's invariants without assuming per-regime curves exist.
    assert artifact["version"] == "v2"
    assert artifact["n_trades"] == 180
    assert "by_regime" in artifact
    assert "held_out_brier" in artifact
    assert "fold_results" in artifact
    for fold in artifact["fold_results"]:
        assert "brier_isotonic" in fold
        assert "brier_constant_baseline" in fold
        assert 0.0 <= fold["brier_isotonic"] <= 1.0
    assert "bucket_stats" in artifact
    assert isinstance(artifact["notes"], str) and "isotonic" in artifact["notes"].lower()


def test_calibrator_loads_real_shipped_v1_artifact():
    """The artifact we just trained and committed should load cleanly through
    the calibrate() runtime, including the ceiling cap and Brier reason."""
    import nse_backtest.model.calibrator as cal_mod
    cal_mod._load_calibrator.cache_clear()
    cal = cal_mod._load_calibrator()
    assert cal is not None
    assert cal["version"] in ("v0", "v1", "v2")
    out, reason = cal_mod.calibrate(95.0)
    assert 0.0 <= out <= 100.0
    if cal["version"] in ("v1", "v2"):
        assert "held-out brier" in reason.lower() or "brier" in reason.lower()
