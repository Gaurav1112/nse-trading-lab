# Walk-Forward A/B: v1 vs v2 (regime_gate + rs_vs_nifty)

Nifty 50, stride=5, min_score=65, max_hold=15. v2 engine applies both
rs_vs_nifty additive booster and regime_block defensive downgrade.

| Window | Engine | Trades | Win rate | Avg win | Avg loss | Expectancy | PF |
|---|---|---|---|---|---|---|---|
| 2023 | v1 | 105 | 80.0% | +11.66% | -4.96% | +8.34% | 9.40 |
| 2023 | v2 | 99 | 73.7% | +12.20% | -5.66% | +7.51% | 6.05 |
| 2024 | v1 | 170 | 46.5% | +13.27% | -7.64% | +2.08% | 1.51 |
| 2024 | v2 | 139 | 47.5% | +12.55% | -7.55% | +1.99% | 1.50 |
| 2025_to_today | v1 | 148 | 43.2% | +7.86% | -7.11% | -0.64% | 0.84 |
| 2025_to_today | v2 | 76 | 46.1% | +8.10% | -6.44% | +0.26% | 1.07 |

## Deltas (v2 - v1)

| Window | Δ trades | Δ win rate | Δ expectancy | Δ PF |
|---|---|---|---|---|
| 2023 | -6 | -6.3pp | -0.83pp | -3.35 |
| 2024 | -31 | +1.0pp | -0.08pp | -0.01 |
| 2025_to_today | -72 | +2.8pp | +0.89pp | +0.23 |

## Ship decision

- 2025 Δ expectancy: +0.89pp (need > +0.5pp)
- 2023 v2 expectancy: +7.51% (need > +5%)
- **VERDICT: Ship v2 (regime_gate + rs_vs_nifty) as the default engine.**

## Statistical honesty (Phase G — Tomás)

Multiple-testing tally: tested ~40 variants (rs_vs_nifty thresholds + regime_gate MIXED thresholds). Bonferroni-corrected per-test α = 0.0013 (from family α=0.05).

Expectancy 95% CI is non-parametric bootstrap (n_boot=500) on per-trade net returns. Deflated Sharpe is Bailey & Lopez de Prado (2014): probability the true Sharpe exceeds the expected maximum under N independent trials. Prob Sharpe>0 is the classic probabilistic Sharpe ratio.

| Window | Engine | Expectancy 95% CI (low / mid / high) | Deflated Sharpe | Prob Sharpe>0 |
|---|---|---|---|---|
| 2023 | v1 | +6.69% / +8.34% / +10.01% | 0.00 | 1.00 |
| 2023 | v2 | +5.63% / +7.51% / +9.40% | 0.00 | 1.00 |
| 2024 | v1 | +0.53% / +2.08% / +3.80% | 0.00 | 0.99 |
| 2024 | v2 | +0.34% / +1.99% / +3.78% | 0.00 | 0.99 |
| 2025_to_today | v1 | -2.03% / -0.64% / +1.00% | 0.00 | 0.19 |
| 2025_to_today | v2 | -1.52% / +0.26% / +1.85% | 0.00 | 0.61 |