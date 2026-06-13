"""Phase 3 v0 — fit isotonic regression on walk-forward win-probability vs actuals.

Reads the per-trade CSVs from output/snapshots/phase1_picker_replay_*.csv,
extracts (predicted_win_prob, actual_win) pairs, fits IsotonicRegression,
saves the calibrator to nse_backtest/model/calibrator.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

CSV_DIR = Path("output/snapshots")
OUT_DIR = Path("nse_backtest/model")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CALIB_PATH = OUT_DIR / "calibrator.json"


def main():
    csvs = sorted(CSV_DIR.glob("phase1_picker_replay_*.csv"))
    if not csvs:
        raise SystemExit(
            "No walk-forward CSVs found in output/snapshots/. Run "
            "scripts/generate_snapshots.py first."
        )

    frames = []
    for c in csvs:
        df = pd.read_csv(c)
        if "win_prob" not in df.columns or "net_%" not in df.columns:
            print(f"  skipping {c.name}: missing columns")
            continue
        df = df[["win_prob", "net_%", "score", "entry_date", "symbol"]].copy()
        df["win"] = (df["net_%"] > 0).astype(int)
        df["source"] = c.stem
        frames.append(df)

    all_trades = pd.concat(frames, ignore_index=True)
    print(f"Loaded {len(all_trades)} trades from {len(frames)} CSV(s)")

    raw_p = all_trades["win_prob"].clip(0, 100) / 100.0
    y = all_trades["win"].astype(int).values

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(raw_p.values, y)

    # Sample the calibration curve on a 0..1 grid for serialization
    grid = np.linspace(0.0, 1.0, 101)
    calibrated = iso.predict(grid)

    # Sanity stats: average actual win when raw_p in each bucket
    bins = [0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    labels = pd.cut(raw_p, bins=bins, include_lowest=True)
    bucket_stats = (
        all_trades.assign(bucket=labels, raw_p=raw_p)
        .groupby("bucket", observed=True)
        .agg(n=("win", "size"), actual_win_rate=("win", "mean"),
             avg_raw_p=("raw_p", "mean"))
    )
    print("\n=== Calibration bucket stats (raw_p vs actual win rate) ===")
    print(bucket_stats.to_string())

    payload = {
        "version": "v0",
        "fit_date": pd.Timestamp.now().isoformat(timespec="seconds"),
        "n_trades": int(len(all_trades)),
        "grid": grid.tolist(),
        "calibrated": calibrated.tolist(),
        "bucket_stats": {
            str(k): {"n": int(v["n"]), "actual_win_rate": float(v["actual_win_rate"]),
                     "avg_raw_p": float(v["avg_raw_p"])}
            for k, v in bucket_stats.to_dict(orient="index").items()
        },
    }
    CALIB_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote calibrator to {CALIB_PATH}")
    print(f"Use it via nse_backtest.model.calibrator.calibrate(raw_prob).")


if __name__ == "__main__":
    main()
