"""Apply a fitted isotonic calibrator to raw probabilities.

Phase 3 v2 (regime-conditional): the artifact contains per-regime
isotonic curves under `by_regime`. calibrate() takes a regime hint and
dispatches to the matching curve, falling back to the global curve
when regime is None or UNKNOWN. v1 artifacts (no `by_regime` key) are
read transparently using the top-level fallback curve.

Runtime ceiling cap (from v1) is still applied per-curve: the returned
value can never exceed max(actual_win_rate) for buckets with n >= 20
in whichever curve was selected. Belt-and-suspenders for any future
isotonic that pins to 1.0 at the top of its grid.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np


_CALIB_PATH = Path(__file__).resolve().parent / "calibrator.json"
MIN_BUCKET_N = 20


@lru_cache(maxsize=1)
def _load_calibrator() -> Optional[dict]:
    if not _CALIB_PATH.exists():
        return None
    try:
        return json.loads(_CALIB_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _ceiling_from_buckets(bucket_stats: dict) -> Optional[float]:
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


def _curve_for_regime(cal: dict, regime: Optional[str]) -> tuple[dict, str]:
    """Pick (curve_blob, curve_label). Falls back to global when regime missing
    or no per-regime curve is fitted."""
    by_regime = cal.get("by_regime")
    if isinstance(by_regime, dict) and regime and regime in by_regime:
        blob = by_regime[regime]
        if blob.get("n_trades", 0) > 0:
            return blob, f"regime={regime}"
    return cal, "global"


def calibrate(raw_prob: float, regime: Optional[str] = None) -> tuple[float, str]:
    """Map a raw win-probability (0-1 or 0-100) to its calibrated equivalent.

    `regime` lets the v2 calibrator dispatch to the right curve. When omitted
    or unknown to the artifact, the global (all-trades) curve is used.

    Returns (calibrated_prob_0_to_100, reason).
    """
    cal = _load_calibrator()
    if cal is None:
        return float(raw_prob), "Calibrator unavailable; returning raw probability"

    if raw_prob > 1.0:
        x = float(raw_prob) / 100.0
    else:
        x = float(raw_prob)
    x = max(0.0, min(1.0, x))

    curve, curve_label = _curve_for_regime(cal, regime)
    grid = np.asarray(curve["grid"], dtype=float)
    cal_arr = np.asarray(curve["calibrated"], dtype=float)
    y = float(np.interp(x, grid, cal_arr))

    ceiling = _ceiling_from_buckets(curve.get("bucket_stats", {}))
    capped = False
    if ceiling is not None and y > ceiling:
        y = ceiling
        capped = True

    pct = round(y * 100.0, 1)

    bits = [
        f"Calibrated from raw {raw_prob:.1f}% via isotonic ({curve_label}, "
        f"n={curve.get('n_trades', '?')})"
    ]
    if capped:
        bits.append(f"capped at observed top-bucket win rate {ceiling * 100:.1f}%")
    brier = curve.get("held_out_brier")
    if isinstance(brier, dict) and brier:
        parts = ", ".join(f"{k}={v:.3f}" for k, v in sorted(brier.items()))
        bits.append(f"held-out Brier: {parts}")
    return pct, "; ".join(bits)
