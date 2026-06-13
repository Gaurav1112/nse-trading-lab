"""Phase 3 v1 — walk-forward isotonic calibration with held-out Brier.

Reads the per-trade CSVs from output/snapshots/phase1_picker_replay_*.csv,
extracts (predicted_win_prob, actual_win) pairs grouped by entry year, and
performs walk-forward training:

    Fold 1: train on 2023            → evaluate Brier on 2024
    Fold 2: train on 2023 + 2024     → evaluate Brier on 2025
    Production: train on all 3 years (the curve actually shipped)

Brier score is the mean squared error between predicted probability and the
0/1 outcome. Lower is better. Always-predict-0.50 yields Brier=0.25 — that
is the reference line. A calibrator that scores above 0.25 is worse than a
constant.

Why no LightGBM at this scale: with ~390 trades and 2 informative inputs
(score, raw_win_prob), a gradient-boosted tree will overfit and lose to
isotonic on held-out Brier. Revisit once we have ≥2000 trades and ≥5
genuinely independent features. Documented decision, not an omission.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss

CSV_DIR = Path("output/snapshots")
OUT_DIR = Path("nse_backtest/model")
OUT_DIR.mkdir(parents=True, exist_ok=True)
CALIB_PATH = OUT_DIR / "calibrator.json"


def _load_trades_by_year() -> dict[int, pd.DataFrame]:
    """Return {year: DataFrame} with columns raw_p (0-1) and win (0/1)."""
    csvs = sorted(CSV_DIR.glob("phase1_picker_replay_*.csv"))
    if not csvs:
        raise SystemExit(
            "No walk-forward CSVs found in output/snapshots/. "
            "Run scripts/generate_snapshots.py first."
        )
    rows = []
    for c in csvs:
        df = pd.read_csv(c)
        if "win_prob" not in df.columns or "net_%" not in df.columns:
            print(f"  skipping {c.name}: missing columns")
            continue
        df = df[["win_prob", "net_%", "score", "entry_date", "symbol"]].copy()
        df["raw_p"] = df["win_prob"].clip(0, 100) / 100.0
        df["win"] = (df["net_%"] > 0).astype(int)
        df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce")
        df["year"] = df["entry_date"].dt.year
        df["source"] = c.stem
        rows.append(df)
    full = pd.concat(rows, ignore_index=True).dropna(subset=["year", "raw_p"])
    return {int(y): g for y, g in full.groupby("year")}


def _fit_isotonic(train: pd.DataFrame) -> IsotonicRegression:
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(train["raw_p"].values, train["win"].values)
    return iso


def _brier(iso: IsotonicRegression, eval_df: pd.DataFrame) -> float:
    p_hat = iso.predict(eval_df["raw_p"].values)
    return float(brier_score_loss(eval_df["win"].values, p_hat))


def _brier_baseline(eval_df: pd.DataFrame) -> float:
    """Brier of a constant prediction at the eval set's base rate.
    A useful sanity check: a calibrator should beat this."""
    base = float(eval_df["win"].mean())
    p_hat = np.full(len(eval_df), base)
    return float(brier_score_loss(eval_df["win"].values, p_hat))


def _bucket_stats(df: pd.DataFrame) -> dict:
    bins = [0, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    labels = pd.cut(df["raw_p"], bins=bins, include_lowest=True)
    g = (
        df.assign(bucket=labels)
        .groupby("bucket", observed=True)
        .agg(n=("win", "size"), actual_win_rate=("win", "mean"),
             avg_raw_p=("raw_p", "mean"))
    )
    return {
        str(k): {"n": int(v["n"]),
                 "actual_win_rate": float(v["actual_win_rate"]),
                 "avg_raw_p": float(v["avg_raw_p"])}
        for k, v in g.to_dict(orient="index").items()
    }


def main():
    by_year = _load_trades_by_year()
    years = sorted(by_year.keys())
    print(f"Loaded {sum(len(d) for d in by_year.values())} trades across years {years}")
    for y in years:
        df = by_year[y]
        print(f"  {y}: n={len(df)}, win_rate={df['win'].mean():.3f}, avg_raw_p={df['raw_p'].mean():.3f}")

    folds: list[tuple[list[int], int]] = []
    for i in range(1, len(years)):
        train_years = years[:i]
        eval_year = years[i]
        folds.append((train_years, eval_year))

    held_out_brier: dict[str, float] = {}
    fold_results = []
    print("\n=== Walk-forward folds ===")
    for train_years, eval_year in folds:
        train = pd.concat([by_year[y] for y in train_years], ignore_index=True)
        ev = by_year[eval_year]
        iso = _fit_isotonic(train)
        b_iso = _brier(iso, ev)
        b_base = _brier_baseline(ev)
        delta = b_base - b_iso
        held_out_brier[str(eval_year)] = round(b_iso, 4)
        fold_results.append({
            "train_years": train_years,
            "eval_year": eval_year,
            "n_train": int(len(train)),
            "n_eval": int(len(ev)),
            "brier_isotonic": round(b_iso, 4),
            "brier_constant_baseline": round(b_base, 4),
            "improvement_vs_baseline": round(delta, 4),
        })
        print(
            f"  train={train_years} (n={len(train)}) → eval {eval_year} (n={len(ev)})  "
            f"Brier iso={b_iso:.4f}  baseline={b_base:.4f}  Δ={delta:+.4f}"
        )

    print("\n=== Production fit (all years) ===")
    all_df = pd.concat([by_year[y] for y in years], ignore_index=True)
    prod_iso = _fit_isotonic(all_df)
    grid = np.linspace(0.0, 1.0, 101)
    calibrated = prod_iso.predict(grid)

    buckets = _bucket_stats(all_df)
    print(f"Production trained on n={len(all_df)} trades")
    print("\nBucket stats (production data):")
    for k, v in buckets.items():
        print(f"  {k}: n={v['n']:3d}  actual_win={v['actual_win_rate']:.3f}  avg_raw_p={v['avg_raw_p']:.3f}")

    payload = {
        "version": "v1",
        "fit_date": pd.Timestamp.now().isoformat(timespec="seconds"),
        "n_trades": int(len(all_df)),
        "grid": grid.tolist(),
        "calibrated": calibrated.tolist(),
        "bucket_stats": buckets,
        "held_out_brier": held_out_brier,
        "fold_results": fold_results,
        "notes": (
            "Walk-forward isotonic. LightGBM intentionally not used at this "
            "data scale (n~390) — would overfit on 2 informative inputs. "
            "Runtime ceiling cap in calibrator.py clips outputs at "
            "max(actual_win_rate) for buckets with n>=20."
        ),
    }
    CALIB_PATH.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote calibrator v1 to {CALIB_PATH}")
    print(f"Held-out Brier per eval year: {held_out_brier}")


if __name__ == "__main__":
    main()
