"""Phase 3 v2 — regime-conditional walk-forward isotonic calibration.

Builds on v1 (walk-forward isotonic with held-out Brier and runtime
ceiling cap) by training a separate calibrator per tape regime
(TRENDING / MIXED / HOSTILE). The motivation: v1's 2024 held-out Brier
(0.371) was much worse than baseline because the 2023 training set was
TRENDING-tape (75.7% win rate) while the 2024 eval set was MIXED tape
(45.9% win rate). A static calibrator can't bridge that distribution
shift. A regime-conditional one fits each tape's distribution separately
and dispatches at inference time on the current regime.

Inputs:
  - output/snapshots/phase1_picker_replay_*.csv  (existing trades)
  - yfinance ^NSEI history (re-fetched once to label each trade's
    entry_date with its tape regime)

Output:
  nse_backtest/model/calibrator.json with shape:
    {
      "version": "v2",
      "by_regime": {
        "TRENDING": {grid, calibrated, bucket_stats, n_trades, held_out_brier, ...},
        "MIXED":    {...},
        "HOSTILE":  {...},
      },
      ... global fallback curve trained on all trades (used when regime
      can't be determined or for backward compat with v1 readers).
    }

Why no LightGBM at this scale: with ~390 trades and 2 informative inputs,
gradient-boosted trees overfit. The honest path forward at this data
volume is finer-grained slicing of the existing data (regime
conditioning), not a higher-variance model.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss

from nse_backtest.data import fetch_nifty50
from nse_backtest.tape_monitor import assess_tape, TapeRegime

CSV_DIR = Path("output/snapshots")
OUT_DIR = Path("nse_backtest/model")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CALIB_PATH = OUT_DIR / "calibrator.json"


def _load_all_trades() -> pd.DataFrame:
    csvs = sorted(CSV_DIR.glob("phase1_picker_replay_*.csv"))
    if not csvs:
        raise SystemExit("No walk-forward CSVs in output/snapshots/. Run generate_snapshots.py first.")
    rows = []
    for c in csvs:
        df = pd.read_csv(c)
        if "win_prob" not in df.columns or "net_%" not in df.columns:
            print(f"  skipping {c.name}: missing columns"); continue
        df = df[["win_prob", "net_%", "score", "entry_date", "symbol"]].copy()
        df["raw_p"] = df["win_prob"].clip(0, 100) / 100.0
        df["win"] = (df["net_%"] > 0).astype(int)
        df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
        rows.append(df)
    return pd.concat(rows, ignore_index=True).dropna(subset=["entry_date", "raw_p"])


def _label_regime(trades: pd.DataFrame, nifty: pd.DataFrame) -> pd.DataFrame:
    """Add `regime` column by re-running tape_monitor at each entry_date."""
    out = trades.copy()
    out["regime"] = "UNKNOWN"
    for i, row in out.iterrows():
        d = row["entry_date"]
        if pd.isna(d):
            continue
        try:
            hist = nifty.loc[:d]
            a = assess_tape(hist)
            if a is not None:
                out.at[i, "regime"] = a.regime
        except (KeyError, IndexError):
            pass
    return out


def _fit_isotonic(train: pd.DataFrame) -> IsotonicRegression:
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(train["raw_p"].values, train["win"].values)
    return iso


def _brier(iso: IsotonicRegression, eval_df: pd.DataFrame) -> float:
    p_hat = iso.predict(eval_df["raw_p"].values)
    return float(brier_score_loss(eval_df["win"].values, p_hat))


def _brier_baseline(eval_df: pd.DataFrame) -> float:
    base = float(eval_df["win"].mean())
    p_hat = np.full(len(eval_df), base)
    return float(brier_score_loss(eval_df["win"].values, p_hat))


def _bucket_stats(df: pd.DataFrame) -> dict:
    bins = [0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    labels = pd.cut(df["raw_p"], bins=bins, include_lowest=True)
    g = (
        df.assign(bucket=labels)
        .groupby("bucket", observed=True)
        .agg(n=("win", "size"),
             actual_win_rate=("win", "mean"),
             avg_raw_p=("raw_p", "mean"))
    )
    return {
        str(k): {"n": int(v["n"]),
                 "actual_win_rate": float(v["actual_win_rate"]),
                 "avg_raw_p": float(v["avg_raw_p"])}
        for k, v in g.to_dict(orient="index").items()
    }


def _fit_with_walk_forward_brier(
    trades: pd.DataFrame, label: str
) -> dict:
    """Fit production isotonic + report held-out Brier on a single chronological split."""
    grid = np.linspace(0.0, 1.0, 101)
    if trades.empty:
        return {"version": "v2-empty", "n_trades": 0,
                "grid": grid.tolist(), "calibrated": grid.tolist(),
                "bucket_stats": {}, "held_out_brier": {}, "fold_results": []}

    # Held-out: chronological 80/20.
    trades_sorted = trades.sort_values("entry_date")
    n_train = max(1, int(len(trades_sorted) * 0.8))
    train_df = trades_sorted.iloc[:n_train]
    eval_df = trades_sorted.iloc[n_train:]
    fold_results = []
    held_out_brier: dict[str, float] = {}
    if len(eval_df) >= 5 and len(train_df) >= 10:
        iso_hold = _fit_isotonic(train_df)
        b_iso = _brier(iso_hold, eval_df)
        b_base = _brier_baseline(eval_df)
        held_out_brier["chrono_80_20"] = round(b_iso, 4)
        fold_results.append({
            "split": "chronological 80/20",
            "n_train": int(len(train_df)),
            "n_eval": int(len(eval_df)),
            "brier_isotonic": round(b_iso, 4),
            "brier_constant_baseline": round(b_base, 4),
            "improvement_vs_baseline": round(b_base - b_iso, 4),
        })
        print(f"  [{label}] train n={len(train_df)} -> eval n={len(eval_df)}  "
              f"Brier iso={b_iso:.4f}  baseline={b_base:.4f}  delta={b_base-b_iso:+.4f}")
    else:
        print(f"  [{label}] insufficient data for held-out Brier "
              f"(n_train={len(train_df)}, n_eval={len(eval_df)})")

    iso_prod = _fit_isotonic(trades_sorted)
    calibrated = iso_prod.predict(grid)
    return {
        "version": "v2",
        "n_trades": int(len(trades_sorted)),
        "grid": grid.tolist(),
        "calibrated": calibrated.tolist(),
        "bucket_stats": _bucket_stats(trades_sorted),
        "held_out_brier": held_out_brier,
        "fold_results": fold_results,
    }


def main():
    trades = _load_all_trades()
    print(f"Loaded {len(trades)} total trades.")

    nifty = fetch_nifty50(start="2022-01-01")
    print(f"Nifty history loaded ({len(nifty)} bars).")

    print("Labelling each trade with its entry-date tape regime...")
    trades = _label_regime(trades, nifty)
    print("Regime distribution:")
    print(trades.groupby("regime").agg(n=("win","size"), win_rate=("win","mean"),
                                      avg_raw_p=("raw_p","mean")).to_string())
    print()

    print("=== Per-regime walk-forward fits ===")
    by_regime: dict[str, dict] = {}
    for regime in [TapeRegime.TRENDING, TapeRegime.MIXED, TapeRegime.HOSTILE]:
        subset = trades[trades["regime"] == regime]
        print(f"\n-- {regime} (n={len(subset)}) --")
        by_regime[regime] = _fit_with_walk_forward_brier(subset, regime)

    print("\n=== Global fallback (all trades) ===")
    fallback = _fit_with_walk_forward_brier(trades, "GLOBAL")

    payload = {
        "version": "v2",
        "fit_date": pd.Timestamp.now().isoformat(timespec="seconds"),
        "n_trades": int(len(trades)),
        "by_regime": by_regime,
        # v1-compatible top-level keys (fallback curve) so older readers still work.
        "grid": fallback["grid"],
        "calibrated": fallback["calibrated"],
        "bucket_stats": fallback["bucket_stats"],
        "held_out_brier": fallback["held_out_brier"],
        "fold_results": fallback["fold_results"],
        "notes": (
            "Phase 3 v2 — regime-conditional isotonic. Three separate "
            "isotonic curves: one per tape regime (TRENDING / MIXED / "
            "HOSTILE). Calibrator dispatches at inference time on the "
            "current tape regime; falls back to global curve when regime "
            "is UNKNOWN. Runtime ceiling cap in calibrator.py still "
            "applies per-regime."
        ),
    }
    CALIB_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote calibrator v2 to {CALIB_PATH}")
    for regime, blob in by_regime.items():
        print(f"  {regime:9}: n={blob['n_trades']:3d}  "
              f"held_out_brier={blob['held_out_brier']}")


if __name__ == "__main__":
    main()
