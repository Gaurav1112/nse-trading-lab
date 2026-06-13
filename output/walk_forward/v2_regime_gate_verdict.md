# Walk-Forward A/B: v1 vs v2 (regime_gate + rs_vs_nifty)

Nifty 50, stride=5, min_score=65, max_hold=15. v2 engine applies both
rs_vs_nifty additive booster and regime_block defensive downgrade.

| Window | Engine | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|---|---|
| 2023 | v1 | 105 | 80.0% | +11.68% | -4.90% | +8.36% | 9.52 |
| 2023 | v2 | 99 | 73.7% | +12.22% | -5.57% | +7.55% | 6.16 |
| 2024 | v1 | 170 | 46.5% | +13.28% | -7.47% | +2.17% | 1.54 |
| 2024 | v2 | 139 | 47.5% | +12.56% | -7.40% | +2.08% | 1.53 |
| 2025_to_today | v1 | 145 | 43.4% | +8.04% | -6.94% | -0.43% | 0.89 |
| 2025_to_today | v2 | 76 | 46.1% | +8.10% | -6.30% | +0.33% | 1.10 |

## Deltas (v2 - v1)

| Window | Δ trades | Δ win rate | Δ expectancy | Δ PF |
|---|---|---|---|---|
| 2023 | -6 | -6.3pp | -0.81pp | -3.36 |
| 2024 | -31 | +1.0pp | -0.10pp | -0.01 |
| 2025_to_today | -69 | +2.6pp | +0.76pp | +0.21 |

## Ship decision

- 2025 Δ expectancy: +0.76pp (need > +0.5pp)
- 2023 v2 expectancy: +7.55% (need > +5%)
- **VERDICT: Ship v2 (regime_gate + rs_vs_nifty) as the default engine.**

## Statistical honesty (Phase G — Tomás)

Multiple-testing tally: tested ~6 variants (rs_vs_nifty thresholds + regime_gate MIXED thresholds). Bonferroni-corrected per-test α = 0.0083 (from family α=0.05).

Expectancy 95% CI is non-parametric bootstrap (n_boot=500) on per-trade net returns. Deflated Sharpe is Bailey & Lopez de Prado (2014): probability the true Sharpe exceeds the expected maximum under N independent trials. Prob Sharpe>0 is the classic probabilistic Sharpe ratio.

| Window | Engine | Expectancy 95% CI (low / mid / high) | Deflated Sharpe | Prob Sharpe>0 |
|---|---|---|---|---|
| 2023 | v1 | +6.82% / +8.36% / +10.07% | 0.00 | 1.00 |
| 2023 | v2 | +5.70% / +7.55% / +9.37% | 0.00 | 1.00 |
| 2024 | v1 | +0.52% / +2.17% / +3.94% | 0.00 | 1.00 |
| 2024 | v2 | +0.26% / +2.08% / +3.96% | 0.00 | 0.99 |
| 2025_to_today | v1 | -1.71% / -0.43% / +1.00% | 0.00 | 0.27 |
| 2025_to_today | v2 | -1.44% / +0.33% / +2.00% | 0.00 | 0.64 |