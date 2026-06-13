"""Apply a fitted isotonic calibrator to raw probabilities.

Owner: Dr. Meera Nair (C.9). Calibrator JSON is produced by
scripts/train_calibration.py and shipped in nse_backtest/model/calibrator.json.

Phase 3 v1 adds a runtime ceiling cap so we never claim a calibrated win
probability higher than the highest observed actual win rate in any reliable
training bucket (n >= MIN_BUCKET_N). This is defense in depth: even if a
future fit produces a curve that pins to 1.0 in a region with no training
data, the cap clips it back to honesty.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np


_CALIB_PATH = Path(__file__).resolve().parent / "calibrator.json"
MIN_BUCKET_N = 20  # smaller buckets are too noisy to set a ceiling


@lru_cache(maxsize=1)
def _load_calibrator() -> Optional[dict]:
    if not _CALIB_PATH.exists():
        return None
    try:
        return json.loads(_CALIB_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _ceiling_from_buckets(bucket_stats: dict) -> Optional[float]:
    """Highest actual_win_rate among buckets with reliable sample size."""
    if not bucket_stats:
        return None
    reliable = [
        b["actual_win_rate"] for b in bucket_stats.values()
        if isinstance(b, dict) and b.get("n", 0) >= MIN_BUCKET_N
        and "actual_win_rate" in b
    ]
    if reliable:
        return float(max(reliable))
    fallback = [
        b["actual_win_rate"] for b in bucket_stats.values()
        if isinstance(b, dict) and "actual_win_rate" in b
    ]
    return float(max(fallback)) if fallback else None


def calibrate(raw_prob: float) -> tuple[float, str]:
    """Map a raw win-probability (0-1 or 0-100) to its calibrated equivalent.

    Returns (calibrated_prob_0_to_100, reason).
    If no calibrator is available, returns the input unchanged.
    """
    cal = _load_calibrator()
    if cal is None:
        return float(raw_prob), "Calibrator unavailable; returning raw probability"

    if raw_prob > 1.0:
        x = float(raw_prob) / 100.0
    else:
        x = float(raw_prob)
    x = max(0.0, min(1.0, x))

    grid = np.asarray(cal["grid"], dtype=float)
    cal_arr = np.asarray(cal["calibrated"], dtype=float)
    y = float(np.interp(x, grid, cal_arr))

    ceiling = _ceiling_from_buckets(cal.get("bucket_stats", {}))
    capped = False
    if ceiling is not None and y > ceiling:
        y = ceiling
        capped = True

    pct = round(y * 100.0, 1)

    bits = [f"Calibrated from raw {raw_prob:.1f}% via isotonic (n={cal.get('n_trades', '?')})"]
    if capped:
        bits.append(f"capped at observed top-bucket win rate {ceiling * 100:.1f}%")
    brier = cal.get("held_out_brier")
    if isinstance(brier, dict) and brier:
        parts = ", ".join(f"{k}={v:.3f}" for k, v in sorted(brier.items()))
        bits.append(f"held-out Brier: {parts}")
    return pct, "; ".join(bits)
