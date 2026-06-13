"""Walk-forward A/B: v1 vs v2 across 2023, 2024, 2025 windows.

Writes output/walk_forward/v2_regime_gate_verdict.md with a 3-window comparison.
"""
import os
from pathlib import Path

import pandas as pd

from nse_backtest.data import fetch_multiple, fetch_nifty50, NIFTY50_SYMBOLS
from nse_backtest.picker_replay import replay_picker
from nse_backtest.stats import (
    bonferroni_alpha, deflated_sharpe, expectancy_ci_bootstrap,
)

OUT_DIR = Path("output/walk_forward")
OUT_DIR.mkdir(parents=True, exist_ok=True)

WINDOWS = [
    ("2023", "2023-01-01", "2023-12-31"),
    ("2024", "2024-01-01", "2024-12-31"),
    ("2025_to_today", "2025-01-01", "2026-06-13"),
]
STRIDE = int(os.getenv("WF_STRIDE", "5"))
MIN_SCORE = float(os.getenv("WF_MIN_SCORE", "65"))

# Phase G — Tomás Vega: tally of variants tested across Phase 2A + 2B.
# Phase 2A swept 4 rs_vs_nifty thresholds (5/7/8/10%); Phase 2B swept
# 2 regime_gate MIXED thresholds (75/70). Total ~6 independent trials.
N_TRIALS_PHASE_2 = 6


def _stats_row(report):
    rets = [t.net_return_pct for t in report.trades]
    if len(rets) < 2:
        return {"ci_low": 0.0, "ci_mid": 0.0, "ci_high": 0.0,
                "defl_sharpe": 0.0, "prob_sharpe": 0.0}
    low, mid, high = expectancy_ci_bootstrap(rets, n_boot=500)
    ds = deflated_sharpe(rets, n_trials_tested=N_TRIALS_PHASE_2)
    return {
        "ci_low": low, "ci_mid": mid, "ci_high": high,
        "defl_sharpe": ds["deflated_sharpe"],
        "prob_sharpe": ds["prob_sharpe"],
    }


def stride_filter(symbol_data, stride):
    if stride <= 1:
        return symbol_data
    return {s: df.iloc[::stride].copy() for s, df in symbol_data.items()}


def fmt(r):
    return {
        "trades": r.total_trades,
        "win_rate": r.win_rate * 100,
        "avg_win": r.avg_win_pct,
        "avg_loss": r.avg_loss_pct,
        "expectancy": r.expectancy_pct,
        "profit_factor": r.profit_factor,
    }


def main():
    print("Fetching Nifty 50 + index (one-shot)…")
    symbol_data = fetch_multiple(NIFTY50_SYMBOLS, start="2022-01-01")
    nifty = fetch_nifty50(start="2022-01-01")
    sampled = stride_filter(symbol_data, STRIDE)

    rows = []
    r1_map, r2_map = {}, {}
    for label, start, end in WINDOWS:
        print(f"\n=== Window {label} ({start} → {end}) ===")
        r1 = replay_picker(symbol_data=sampled, start=start, end=end,
                          min_score=MIN_SCORE, max_hold=15, engine="v1")
        r2 = replay_picker(symbol_data=sampled, start=start, end=end,
                          min_score=MIN_SCORE, max_hold=15,
                          engine="v2", nifty_df=nifty)
        rows.append((label, fmt(r1), fmt(r2)))
        r1_map[label] = r1
        r2_map[label] = r2
        print(f"  v1: trades={r1.total_trades}, WR={r1.win_rate*100:.1f}%, expectancy={r1.expectancy_pct:+.2f}%, PF={r1.profit_factor:.2f}")
        print(f"  v2: trades={r2.total_trades}, WR={r2.win_rate*100:.1f}%, expectancy={r2.expectancy_pct:+.2f}%, PF={r2.profit_factor:.2f}")

    lines = ["# Walk-Forward A/B: v1 vs v2 (regime_gate + rs_vs_nifty)\n"]
    lines.append("Nifty 50, stride=5, min_score=65, max_hold=15. v2 engine applies both")
    lines.append("rs_vs_nifty additive booster and regime_block defensive downgrade.\n")
    lines.append("| Window | Engine | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for label, v1, v2 in rows:
        lines.append(f"| {label} | v1 | {v1['trades']} | {v1['win_rate']:.1f}% | {v1['avg_win']:+.2f}% | {v1['avg_loss']:+.2f}% | {v1['expectancy']:+.2f}% | {v1['profit_factor']:.2f} |")
        lines.append(f"| {label} | v2 | {v2['trades']} | {v2['win_rate']:.1f}% | {v2['avg_win']:+.2f}% | {v2['avg_loss']:+.2f}% | {v2['expectancy']:+.2f}% | {v2['profit_factor']:.2f} |")

    lines.append("\n## Deltas (v2 - v1)\n")
    lines.append("| Window | Δ trades | Δ win rate | Δ expectancy | Δ PF |")
    lines.append("|---|---|---|---|---|")
    for label, v1, v2 in rows:
        lines.append(f"| {label} | {v2['trades']-v1['trades']:+d} | {v2['win_rate']-v1['win_rate']:+.1f}pp | {v2['expectancy']-v1['expectancy']:+.2f}pp | {v2['profit_factor']-v1['profit_factor']:+.2f} |")

    # Ship decision: v2 wins if 2025 Δ expectancy > +0.5pp AND 2023 expectancy stays > +5%
    e2025_v1 = next(v1["expectancy"] for label, v1, v2 in rows if label == "2025_to_today")
    e2025_v2 = next(v2["expectancy"] for label, v1, v2 in rows if label == "2025_to_today")
    e2023_v2 = next(v2["expectancy"] for label, v1, v2 in rows if label == "2023")

    delta_2025 = e2025_v2 - e2025_v1
    ship = delta_2025 > 0.5 and e2023_v2 > 5.0
    lines.append(f"\n## Ship decision\n")
    lines.append(f"- 2025 Δ expectancy: {delta_2025:+.2f}pp (need > +0.5pp)")
    lines.append(f"- 2023 v2 expectancy: {e2023_v2:+.2f}% (need > +5%)")
    if ship:
        lines.append(f"- **VERDICT: Ship v2 (regime_gate + rs_vs_nifty) as the default engine.**")
    else:
        lines.append(f"- **VERDICT: Do NOT ship as default. Iterate on regime_gate thresholds.**")

    # Phase G — Tomás Vega: statistical honesty section
    lines.append("\n## Statistical honesty (Phase G — Tomás)\n")
    lines.append(
        f"Multiple-testing tally: tested ~{N_TRIALS_PHASE_2} variants "
        f"(rs_vs_nifty thresholds + regime_gate MIXED thresholds). "
        f"Bonferroni-corrected per-test α = "
        f"{bonferroni_alpha(N_TRIALS_PHASE_2):.4f} (from family α=0.05).\n"
    )
    lines.append("Expectancy 95% CI is non-parametric bootstrap (n_boot=500) "
                 "on per-trade net returns. Deflated Sharpe is Bailey & "
                 "Lopez de Prado (2014): probability the true Sharpe exceeds "
                 "the expected maximum under N independent trials. "
                 "Prob Sharpe>0 is the classic probabilistic Sharpe ratio.\n")
    lines.append("| Window | Engine | Expectancy 95% CI (low / mid / high) | Deflated Sharpe | Prob Sharpe>0 |")
    lines.append("|---|---|---|---|---|")
    for label, v1, v2 in rows:
        s1 = _stats_row(r1_map[label])
        s2 = _stats_row(r2_map[label])
        lines.append(
            f"| {label} | v1 | "
            f"{s1['ci_low']:+.2f}% / {s1['ci_mid']:+.2f}% / {s1['ci_high']:+.2f}% | "
            f"{s1['defl_sharpe']:.2f} | {s1['prob_sharpe']:.2f} |"
        )
        lines.append(
            f"| {label} | v2 | "
            f"{s2['ci_low']:+.2f}% / {s2['ci_mid']:+.2f}% / {s2['ci_high']:+.2f}% | "
            f"{s2['defl_sharpe']:.2f} | {s2['prob_sharpe']:.2f} |"
        )

    out_path = OUT_DIR / "v2_regime_gate_verdict.md"
    out_path.write_text("\n".join(lines))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
