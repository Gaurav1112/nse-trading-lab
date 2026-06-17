"""Held-out validation on 2026 YTD — the year the engine has never been
tuned against. Runs picker_replay for both v1 and v2 engines on 2026
trades only, then writes a verdict markdown.

If the v2 expectancy here is meaningfully different from the walk-forward
A/B's 2025_to_today number (currently +0.33%), that signals either
generalization success (similar) or over-fit (large gap).

Writes output/walk_forward/held_out_2026.md.
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

from nse_backtest.data import fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS
from nse_backtest.picker_replay import replay_picker

OUT_DIR = Path("output/walk_forward")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH = OUT_DIR / "held_out_2026.md"

YEAR_START = "2026-01-01"
YEAR_END = os.environ.get("HELD_OUT_END", pd.Timestamp.today().strftime("%Y-%m-%d"))


def summarize(report, engine: str) -> dict:
    if not report.trades:
        return {"engine": engine, "n_trades": 0, "win_rate_pct": 0.0,
                "expectancy_pct": 0.0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0}
    wins = [t for t in report.trades if t.net_return_pct > 0]
    losses = [t for t in report.trades if t.net_return_pct <= 0]
    n = len(report.trades)
    avg = sum(t.net_return_pct for t in report.trades) / n
    return {
        "engine": engine, "n_trades": n,
        "win_rate_pct": round(len(wins) / n * 100, 2),
        "expectancy_pct": round(avg, 3),
        "avg_win_pct": round(sum(t.net_return_pct for t in wins) / len(wins), 3) if wins else 0.0,
        "avg_loss_pct": round(sum(t.net_return_pct for t in losses) / len(losses), 3) if losses else 0.0,
    }


def main():
    print(f"Held-out validation window: {YEAR_START} → {YEAR_END}")
    nifty = fetch_nifty50(start="2022-01-01")
    symbol_data = fetch_multiple(NIFTY50_SYMBOLS, start="2024-01-01")
    print(f"Loaded {len(symbol_data)} symbols.")

    summaries = []
    for engine in ["v1", "v2"]:
        print(f"\n=== Replaying engine={engine} on 2026 YTD ===")
        report = replay_picker(
            symbol_data=symbol_data,
            start=YEAR_START, end=YEAR_END,
            min_score=65, max_hold=15, capital=100_000,
            risk_pct=2.0, nifty_df=nifty, engine=engine,
        )
        s = summarize(report, engine)
        summaries.append(s)
        print(f"  trades={s['n_trades']}, win_rate={s['win_rate_pct']}%, expectancy={s['expectancy_pct']:+.3f}%")

    lines = [
        f"# Held-out 2026 YTD validation — {YEAR_START} → {YEAR_END}",
        "",
        "**Honest framing.** The 2023, 2024, and 2025 windows referenced",
        "elsewhere in this repo are **in-sample tuning data** — gate thresholds",
        "(rs_vs_nifty, regime gate MIXED min, MTF rules, liquidity floor, gap-up",
        "limit, sector cap, earnings buffer) were chosen by inspecting outcomes",
        "on those years. The calibrator was fit on the same trades. Any reported",
        "expectancy on 2023-2025 is **biased upward by overfitting** and should",
        "be read as an upper bound on what the engine can do in similar tape,",
        "not as a forward estimate.",
        "",
        "**The only honest out-of-sample test we have is 2026 YTD below.** It",
        "is the engine's first encounter with data we did not look at during",
        "tuning. The numbers it produced are the most reliable forward estimate",
        "we have at this data scale.",
        "",
        "Reproduce: `PYTHONPATH=. python3 scripts/validate_held_out_2026.py`",
        "",
        "## Realized outcomes",
        "",
        "| Engine | Trades | Win rate | Expectancy | Avg win | Avg loss |",
        "|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s['engine']} | {s['n_trades']} | "
            f"{s['win_rate_pct']:.1f}% | {s['expectancy_pct']:+.3f}% | "
            f"{s['avg_win_pct']:+.2f}% | {s['avg_loss_pct']:+.2f}% |"
        )

    lines.extend([
        "",
        "## Interpretation guide",
        "",
        "- **v2 expectancy similar to walk-forward 2025 estimate (+0.33%)**:",
        "  the engine generalizes; Wave A is doing its job; HOSTILE tape remains",
        "  in CI-includes-zero territory but the regime gate keeps it non-negative.",
        "- **v2 expectancy materially lower**: out-of-sample degradation; the",
        "  walk-forward A/B numbers are over-fit; size DOWN.",
        "- **v2 expectancy materially higher**: lucky window, not edge — wait",
        "  for more trades before scaling up.",
        "- **Both engines very low trade count**: tape filter is doing its job",
        "  in HOSTILE conditions; 0-low picks is the correct behaviour.",
        "",
        "## What this is NOT",
        "",
        "This is not a license to ignore the engine's empty-state recommendation",
        "in HOSTILE tape. The 2025 walk-forward CI [-1.44%, +2.00%] is the",
        "honest uncertainty; a single quarter of 2026 cannot collapse that CI.",
        "Two quarters of paper-trading record collected by the user themselves",
        "remains the gold-standard verification.",
    ])

    OUT_PATH.write_text("\n".join(lines))
    print(f"\nWrote verdict to {OUT_PATH}")


if __name__ == "__main__":
    main()
