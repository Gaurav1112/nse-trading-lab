"""Apply a fitted isotonic calibrator to raw probabilities.

Owner: Dr. Meera Nair (C.9). Calibrator JSON is produced by
scripts/train_calibration.py and shipped in nse_backtest/model/calibrator.json.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np


_CALIB_PATH = Path(__file__).resolve().parent / "calibrator.json"


@lru_cache(maxsize=1)
def _load_calibrator() -> Optional[dict]:
    if not _CALIB_PATH.exists():
        return None
    try:
        return json.loads(_CALIB_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def calibrate(raw_prob: float) -> tuple[float, str]:
    """Map a raw win-probability (0-1 or 0-100) to its calibrated equivalent.

    Returns (calibrated_prob_0_to_100, reason).
    If no calibrator is available, returns the input unchanged.
    """
    cal = _load_calibrator()
    if cal is None:
        return float(raw_prob), "Calibrator unavailable; returning raw probability"

    # Accept either 0-1 or 0-100 input
    if raw_prob > 1.0:
        x = float(raw_prob) / 100.0
    else:
        x = float(raw_prob)
    x = max(0.0, min(1.0, x))

    grid = np.asarray(cal["grid"], dtype=float)
    cal_arr = np.asarray(cal["calibrated"], dtype=float)
    # Linear interpolation on the saved curve
    y = float(np.interp(x, grid, cal_arr))
    pct = round(y * 100.0, 1)
    return pct, f"Calibrated from raw {raw_prob:.1f}% via isotonic (n={cal.get('n_trades', '?')})"
