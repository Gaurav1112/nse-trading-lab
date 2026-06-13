# Walk-Forward A/B: v1 vs v2 (regime_gate + rs_vs_nifty)

Nifty 50, stride=5, min_score=65, max_hold=15. v2 engine applies both
rs_vs_nifty additive booster and regime_block defensive downgrade.

| Window | Engine | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|---|---|
| 2023 | v1 | 103 | 75.7% | +11.40% | -4.48% | +7.55% | 7.94 |
| 2023 | v2 | 102 | 71.6% | +11.71% | -4.94% | +6.98% | 5.97 |
| 2024 | v1 | 146 | 45.9% | +12.61% | -6.53% | +2.25% | 1.64 |
| 2024 | v2 | 138 | 47.1% | +12.62% | -6.54% | +2.48% | 1.72 |
| 2025_to_today | v1 | 141 | 42.6% | +8.26% | -6.03% | +0.05% | 1.01 |
| 2025_to_today | v2 | 83 | 44.6% | +8.06% | -5.62% | +0.48% | 1.15 |

## Deltas (v2 - v1)

| Window | Δ trades | Δ win rate | Δ expectancy | Δ PF |
|---|---|---|---|---|
| 2023 | -1 | -4.2pp | -0.57pp | -1.97 |
| 2024 | -8 | +1.2pp | +0.23pp | +0.08 |
| 2025_to_today | -58 | +2.0pp | +0.43pp | +0.14 |

## Ship decision

- 2025 Δ expectancy: +0.43pp (need > +0.5pp)
- 2023 v2 expectancy: +6.98% (need > +5%)
- **VERDICT: Do NOT ship as default. Iterate on regime_gate thresholds.**