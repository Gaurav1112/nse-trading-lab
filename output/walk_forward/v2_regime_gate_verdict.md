# Walk-Forward A/B: v1 vs v2 (regime_gate + rs_vs_nifty)

Nifty 50, stride=5, min_score=65, max_hold=15. v2 engine applies both
rs_vs_nifty additive booster and regime_block defensive downgrade.

| Window | Engine | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|---|---|
| 2023 | v1 | 103 | 75.7% | +11.40% | -4.48% | +7.55% | 7.94 |
| 2023 | v2 | 102 | 72.5% | +11.61% | -4.71% | +7.13% | 6.51 |
| 2024 | v1 | 146 | 45.9% | +12.61% | -6.53% | +2.25% | 1.64 |
| 2024 | v2 | 139 | 46.8% | +12.70% | -6.47% | +2.49% | 1.72 |
| 2025_to_today | v1 | 141 | 42.6% | +8.26% | -6.03% | +0.05% | 1.01 |
| 2025_to_today | v2 | 88 | 46.6% | +8.05% | -5.77% | +0.67% | 1.22 |

## Deltas (v2 - v1)

| Window | Δ trades | Δ win rate | Δ expectancy | Δ PF |
|---|---|---|---|---|
| 2023 | -1 | -3.2pp | -0.42pp | -1.43 |
| 2024 | -7 | +0.9pp | +0.24pp | +0.08 |
| 2025_to_today | -53 | +4.0pp | +0.62pp | +0.20 |

## Ship decision

- 2025 Δ expectancy: +0.62pp (need > +0.5pp)
- 2023 v2 expectancy: +7.13% (need > +5%)
- **VERDICT: Ship v2 (regime_gate + rs_vs_nifty) as the default engine.**