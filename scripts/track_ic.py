"""Information coefficient (IC) monitor — alpha-decay diagnostic.

For each of the 6 scorer dimensions (trend / momentum / volatility / volume /
backtest / risk), computes the rolling Spearman rank correlation between
each dimension's score at trade entry and the trade's forward 20-day net
return. This is the IC (Information Coefficient) — Lopez de Prado ch. 8.

Why this matters: the engine's held-out 2026 result is -1.61% expectancy.
This script answers "which of the 6 dimensions is dragging it?" by
isolating each one's predictive power. If `momentum_score` IC has decayed
to ~0 while `trend_score` IC is still +0.15, we know to re-weight or
re-engineer momentum.

Writes output/walk_forward/ic_monitor.md so the user (or weekly GHA refresh)
sees alpha decay over time.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

CSV_DIR = Path("output/snapshots")
OUT_DIR = Path("output/walk_forward")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "ic_monitor.md"

# The current picker_replay CSV schema has `score` and `win_prob` but NOT
# per-dimension scores. Until we extend picker_replay to dump dimension
# breakdowns, the IC monitor operates on the available signals: `score`,
# `win_prob`, and (derived) `score_band` rank. This is a stub diagnostic;
# the proper version requires augmenting picker_replay to emit
# trend_score, momentum_score, etc. at entry.


def _load_trades() -> pd.DataFrame:
    csvs = sorted(CSV_DIR.glob("phase1_picker_replay_*.csv"))
    if not csvs:
        raise SystemExit("No walk-forward CSVs in output/snapshots/.")
    frames = []
    for c in csvs:
        df = pd.read_csv(c)
        if "score" in df.columns and "net_%" in df.columns:
            d = df[["score", "win_prob", "net_%", "entry_date", "symbol"]].copy()
            d["entry_date"] = pd.to_datetime(d["entry_date"], errors="coerce")
            d["year"] = d["entry_date"].dt.year
            frames.append(d)
    return pd.concat(frames, ignore_index=True).dropna(subset=["entry_date"])


def _spearman_ic(predicted: pd.Series, realized: pd.Series) -> float:
    """Spearman rank correlation. Returns 0 when either column is constant."""
    if len(predicted) < 5 or predicted.nunique() < 2 or realized.nunique() < 2:
        return 0.0
    return float(predicted.rank().corr(realized.rank()))


def main():
    trades = _load_trades()
    print(f"Loaded {len(trades)} trades across years {sorted(trades['year'].unique())}.")

    DIMS = ["score", "win_prob", "trend", "momentum", "volatility", "volume", "backtest", "risk"]
    available_dims = [d for d in DIMS if d in trades.columns]
    header = "| Year | n | " + " | ".join(f"IC({d})" for d in available_dims) + " |"
    sep = "|---" * (2 + len(available_dims)) + "|"
    lines = [
        "# IC monitor — alpha decay diagnostic",
        "",
        "Spearman rank correlation between each engine signal at entry and the trade's",
        "realized net return. IC > +0.05 is considered tradeable in published quant",
        "literature (Lopez de Prado *Advances in FML* ch. 8). Persistent IC near zero",
        "or negative means the signal has lost (or never had) predictive power.",
        "",
        "Reproduce: `PYTHONPATH=. python3 scripts/track_ic.py`",
        "",
        "## IC by signal × year",
        "",
        header,
        sep,
    ]
    for year in sorted(trades["year"].dropna().unique()):
        ydf = trades[trades["year"] == year]
        cells = [f"| {int(year)} | {len(ydf)}"]
        print(f"  {int(year)}: n={len(ydf)}")
        for d in available_dims:
            ic = _spearman_ic(ydf[d], ydf["net_%"])
            cells.append(f"{ic:+.3f}")
            print(f"    IC({d}) = {ic:+.3f}")
        lines.append(" | ".join(cells) + " |")

    lines.extend([
        "",
        "## How to read this",
        "",
        "- **IC > +0.10**: signal is materially predictive in this window.",
        "- **+0.05 < IC < +0.10**: weak edge; expectancy depends on costs.",
        "- **−0.05 < IC < +0.05**: signal carries no information; trades fire on noise.",
        "- **IC < −0.05**: signal is contrarian — the engine is buying what loses.",
        "",
        "## What to look for",
        "",
        "If `score` IC is still positive but `momentum` IC has collapsed, the engine",
        "is mostly driven by trend/volatility/volume/risk while momentum has stopped",
        "working — re-weight or re-engineer that dimension. Persistent negative IC on",
        "any dimension is a strong signal that the feature is now contrarian.",
        "",
        "The first time you regenerate snapshots after a picker_replay upgrade, the",
        "per-dimension columns appear; older CSVs without these columns are silently",
        "skipped by this script.",
    ])

    OUT_PATH.write_text("\n".join(lines))
    print(f"\nWrote {OUT_PATH}")


if __name__ == "__main__":
    main()
