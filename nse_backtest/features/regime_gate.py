"""Market-regime gate — uses tape_monitor's TRENDING/MIXED/HOSTILE classification.

Decision matrix:
  TRENDING tape — no change to GO verdicts
  MIXED    tape — downgrade GO → WAIT when final_score < 75
  HOSTILE  tape — downgrade ALL GO → WAIT

Toggle via REGIME_GATE_ENABLED env var (default "1" when engine=v2).
"""
from __future__ import annotations

import os

import pandas as pd

from ..tape_monitor import assess_tape, TapeRegime


def _enabled() -> bool:
    return os.getenv("REGIME_GATE_ENABLED", "1") != "0"


def _mixed_threshold() -> float:
    """Min score required for GO to survive in MIXED tape.

    Default 70 (tuned vs 75 on walk-forward 2023/2024/2025 — see verdict.md).
    Override via REGIME_GATE_MIXED_MIN env var to re-sweep.
    """
    try:
        return float(os.getenv("REGIME_GATE_MIXED_MIN", "70"))
    except ValueError:
        return 70.0


def regime_action(nifty_df: pd.DataFrame | None, final_score: float) -> tuple[bool, str]:
    """Return (downgrade_go, reason).

    downgrade_go=True means caller should downgrade any GO verdict to WAIT.
    """
    if not _enabled():
        return False, "Regime gate disabled"
    if nifty_df is None:
        return False, "Regime gate: no nifty data"

    assessment = assess_tape(nifty_df)
    if assessment is None:
        return False, "Regime gate: insufficient nifty history"

    if assessment.regime == TapeRegime.HOSTILE:
        return True, f"Regime block (HOSTILE): {assessment.recommendation[:80]}…"
    thr = _mixed_threshold()
    if assessment.regime == TapeRegime.MIXED and final_score < thr:
        return True, f"Regime block (MIXED, score {final_score:.0f}<{thr:.0f}): be selective"
    return False, f"Tape regime: {assessment.regime} — no downgrade"
