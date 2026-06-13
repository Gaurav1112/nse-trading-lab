# Walk-Forward A/B: v1 vs v2 (regime_gate + rs_vs_nifty)

Nifty 50, stride=5, min_score=65, max_hold=15. v2 engine applies both
rs_vs_nifty additive booster and regime_block defensive downgrade.

| Window | Engine | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|---|---|
| 2023 | v1 | 103 | 75.7% | +11.40% | -4.48% | +7.55% | 7.94 |
| 2023 | v2 | 100 | 73.0% | +11.56% | -4.60% | +7.20% | 6.79 |
| 2024 | v1 | 146 | 45.9% | +12.61% | -6.53% | +2.25% | 1.64 |
| 2024 | v2 | 134 | 43.3% | +12.47% | -6.70% | +1.60% | 1.42 |
| 2025_to_today | v1 | 141 | 42.6% | +8.26% | -6.03% | +0.05% | 1.01 |
| 2025_to_today | v2 | 124 | 41.9% | +8.08% | -5.92% | -0.05% | 0.99 |

## Deltas (v2 - v1)

| Window | Δ trades | Δ win rate | Δ expectancy | Δ PF |
|---|---|---|---|---|
| 2023 | -3 | -2.7pp | -0.35pp | -1.15 |
| 2024 | -12 | -2.6pp | -0.66pp | -0.22 |
| 2025_to_today | -17 | -0.6pp | -0.10pp | -0.03 |

## Ship decision

- 2025 Δ expectancy: -0.10pp (need > +0.5pp)
- 2023 v2 expectancy: +7.20% (need > +5%)
- **VERDICT: Do NOT ship as default. Iterate on regime_gate thresholds.**